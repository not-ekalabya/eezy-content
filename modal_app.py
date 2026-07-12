"""Modal GPU services: SigLIP-2 embeddings, Qwen3.5-9B (AWQ, vLLM), Whisper ASR.

Deploy:  modal deploy modal_app.py
"""

import io
import os
from pathlib import Path

import modal

APP_NAME = "video-search"

app = modal.App(APP_NAME)

hf_cache = modal.Volume.from_name("video-search-hf-cache", create_if_missing=True)
CACHE_DIR = "/hf-cache"
secrets = [modal.Secret.from_dotenv(Path(__file__).parent)]

EMBED_MODEL = "google/siglip2-so400m-patch14-384"
# 4-bit AWQ of the multimodal Qwen3.5-9B: agent brain and eyes in one model.
# ~6 GB weights -> fits an L4 with ~14 GB left for vLLM KV cache.
VLM_MODEL = "QuantTrio/Qwen3.5-9B-AWQ"
WHISPER_MODEL = "large-v3-turbo"

embed_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.5.1",
        "transformers>=4.49.0",
        "pillow",
        "numpy",
        "sentencepiece",
        "protobuf",
    )
    .env({"HF_HOME": CACHE_DIR})
)

vlm_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")  # torchcodec (vLLM video input) needs libav* at runtime
    .pip_install(
        "vllm>=0.11",
        "pillow",
        "hf_transfer",
    )
    .env(
        {
            "HF_HOME": CACHE_DIR,
            "HF_HUB_ENABLE_HF_TRANSFER": "1",
            # persist torch.compile/AOT artifacts across cold starts
            "VLLM_CACHE_ROOT": f"{CACHE_DIR}/vllm-cache",
            # no nvcc in debian_slim -> flashinfer JIT crashes the engine;
            # FA2 attention + torch sampler avoid flashinfer entirely
            "VLLM_ATTENTION_BACKEND": "FLASH_ATTN",
            "VLLM_USE_FLASHINFER_SAMPLER": "0",
        }
    )
)

# ctranslate2 needs cuBLAS 12 + cuDNN 9 — debian_slim lacks them
whisper_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11"
    )
    .pip_install("faster-whisper>=1.1.0")
    .env({"HF_HOME": CACHE_DIR})
)


@app.cls(
    image=embed_image,
    gpu="L4",
    volumes={CACHE_DIR: hf_cache},
    secrets=secrets,
    scaledown_window=240,
    timeout=600,
)
class EmbeddingService:
    @modal.enter()
    def load(self):
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch = torch
        self.model = (
            AutoModel.from_pretrained(EMBED_MODEL, torch_dtype=torch.float16)
            .to("cuda")
            .eval()
        )
        self.processor = AutoProcessor.from_pretrained(EMBED_MODEL)
        hf_cache.commit()

    def _normalize(self, feats):
        if hasattr(feats, "pooler_output"):  # some transformers versions return ModelOutput
            feats = feats.pooler_output
        feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.float().cpu().numpy().tolist()

    @modal.method()
    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        from PIL import Image

        pils = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
        out: list[list[float]] = []
        with self.torch.inference_mode():
            for i in range(0, len(pils), 32):
                batch = pils[i : i + 32]
                inputs = self.processor(images=batch, return_tensors="pt").to("cuda")
                feats = self.model.get_image_features(**inputs)
                out.extend(self._normalize(feats))
        return out

    @modal.method()
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        with self.torch.inference_mode():
            inputs = self.processor(
                text=texts,
                padding="max_length",
                max_length=64,
                truncation=True,
                return_tensors="pt",
            ).to("cuda")
            feats = self.model.get_text_features(**inputs)
            return self._normalize(feats)


@app.cls(
    image=vlm_image,
    gpu="L4",
    volumes={CACHE_DIR: hf_cache},
    secrets=secrets,
    scaledown_window=240,
    timeout=900,
)
class VLMService:
    @modal.enter()
    def load(self):
        from transformers import AutoProcessor
        from vllm import LLM, SamplingParams

        self.SamplingParams = SamplingParams
        self.llm = LLM(
            model=VLM_MODEL,
            max_model_len=16384,
            # model weights + vision encoder take ~11.2 GiB of the L4's 24;
            # 0.92 overcommitted and OOM-killed the engine during KV allocation
            gpu_memory_utilization=0.85,
            enable_prefix_caching=True,  # agent loop resends history; reuse KV
            limit_mm_per_prompt={"image": 1},
            # cap image tokens: 256..768 visual patches keeps latency low
            mm_processor_kwargs={
                "min_pixels": 256 * 28 * 28,
                "max_pixels": 768 * 28 * 28,
            },
        )
        self.processor = AutoProcessor.from_pretrained(VLM_MODEL)
        hf_cache.commit()

    def _template(self, messages: list[dict]) -> str:
        try:
            return self.processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except TypeError:  # chat template without a thinking switch
            return self.processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )

    def _sampling(self, max_new_tokens: int, json_schema: dict | None = None):
        kwargs = {"temperature": 0.0, "max_tokens": max_new_tokens}
        if json_schema:
            try:
                from vllm.sampling_params import StructuredOutputsParams

                kwargs["structured_outputs"] = StructuredOutputsParams(json=json_schema)
            except ImportError:  # pre-0.11 API
                from vllm.sampling_params import GuidedDecodingParams

                kwargs["guided_decoding"] = GuidedDecodingParams(json=json_schema)
        return self.SamplingParams(**kwargs)

    def _batch_generate(
        self, images: list[bytes], prompt: str, max_new_tokens: int
    ) -> list[str]:
        """One prompt applied to N images, generated as a single vLLM batch."""
        from PIL import Image

        text = self._template(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image"},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
        )
        requests = [
            {
                "prompt": text,
                "multi_modal_data": {
                    "image": Image.open(io.BytesIO(b)).convert("RGB")
                },
            }
            for b in images
        ]
        outs = self.llm.generate(requests, self._sampling(max_new_tokens))
        return [o.outputs[0].text.strip() for o in outs]

    @modal.method()
    def chat(
        self,
        messages: list[dict],
        max_new_tokens: int = 512,
        json_schema: dict | None = None,
    ) -> str:
        """Text-only chat completion (agent brain). messages: [{role, content}].

        With json_schema set, output is constrained to valid JSON matching it.
        """
        outs = self.llm.generate(
            [self._template(messages)], self._sampling(max_new_tokens, json_schema)
        )
        return outs[0].outputs[0].text.strip()

    @modal.method()
    def look(self, images: list[bytes], question: str) -> list[str]:
        """Answer a question about each image. Returns one answer per image."""
        prompt = (
            "Answer the question about this image precisely and concisely. "
            "If the answer is no or the thing is absent, say so explicitly.\n"
            f"Question: {question}"
        )
        return self._batch_generate(images, prompt, 160)

    @modal.method()
    def caption(self, images: list[bytes]) -> list[str]:
        """Dense caption + tags per image, JSON per line."""
        prompt = (
            "Describe this image for a search index. Respond with strict JSON only:\n"
            '{"caption": "<2-3 sentences: subjects, actions, setting, notable objects, '
            'any visible text, lighting/mood>", "tags": ["<5-12 short lowercase tags>"]}'
        )
        return self._batch_generate(images, prompt, 260)


@app.cls(
    image=whisper_image,
    gpu="T4",
    volumes={CACHE_DIR: hf_cache},
    secrets=secrets,
    scaledown_window=240,
    timeout=1800,
)
class WhisperService:
    @modal.enter()
    def load(self):
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            WHISPER_MODEL, device="cuda", compute_type="float16"
        )
        hf_cache.commit()

    @modal.method()
    def transcribe(self, audio: bytes, suffix: str = ".ogg") -> list[dict]:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as f:
            f.write(audio)
            f.flush()
            segments, _info = self.model.transcribe(
                f.name, vad_filter=True, beam_size=1
            )
            return [
                {"start": s.start, "end": s.end, "text": s.text.strip()}
                for s in segments
            ]
