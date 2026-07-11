"""Modal GPU services: SigLIP-2 embeddings, Qwen2.5-VL-3B, Whisper ASR.

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
VLM_MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"
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
    .pip_install(
        "torch==2.5.1",
        "torchvision==0.20.1",  # Qwen2VL processor requires it
        "transformers>=4.49.0",
        "accelerate",
        "qwen-vl-utils",
        "pillow",
    )
    .env({"HF_HOME": CACHE_DIR})
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
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        self.torch = torch
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            VLM_MODEL, torch_dtype=torch.bfloat16, device_map="cuda"
        ).eval()
        # cap image tokens: 256..768 visual patches keeps latency low
        self.processor = AutoProcessor.from_pretrained(
            VLM_MODEL, min_pixels=256 * 28 * 28, max_pixels=768 * 28 * 28
        )
        hf_cache.commit()

    def _generate(self, image_bytes: bytes, prompt: str, max_new_tokens: int) -> str:
        from PIL import Image
        from qwen_vl_utils import process_vision_info

        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": pil},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, _ = process_vision_info(messages)
        inputs = self.processor(
            text=[text], images=image_inputs, return_tensors="pt"
        ).to("cuda")
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=False
            )
        trimmed = out[0][inputs.input_ids.shape[1] :]
        return self.processor.decode(trimmed, skip_special_tokens=True).strip()

    @modal.method()
    def chat(self, messages: list[dict], max_new_tokens: int = 512) -> str:
        """Text-only chat completion (agent brain). messages: [{role, content}]."""
        text = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.processor(text=[text], return_tensors="pt").to("cuda")
        with self.torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=max_new_tokens, do_sample=False
            )
        trimmed = out[0][inputs.input_ids.shape[1] :]
        return self.processor.decode(trimmed, skip_special_tokens=True).strip()

    @modal.method()
    def look(self, images: list[bytes], question: str) -> list[str]:
        """Answer a question about each image. Returns one answer per image."""
        prompt = (
            "Answer the question about this image precisely and concisely. "
            "If the answer is no or the thing is absent, say so explicitly.\n"
            f"Question: {question}"
        )
        return [self._generate(b, prompt, 160) for b in images]

    @modal.method()
    def caption(self, images: list[bytes]) -> list[str]:
        """Dense caption + tags per image, JSON per line."""
        prompt = (
            "Describe this image for a search index. Respond with strict JSON only:\n"
            '{"caption": "<2-3 sentences: subjects, actions, setting, notable objects, '
            'any visible text, lighting/mood>", "tags": ["<5-12 short lowercase tags>"]}'
        )
        return [self._generate(b, prompt, 260) for b in images]


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
