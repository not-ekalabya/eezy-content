"""Lazy handles to Modal services.

Prefers a deployed app (`modal deploy modal_app.py`). If none exists,
falls back to running the app ephemerally from this process — no deploy
step needed; the ephemeral app lives and dies with the server.
"""

import atexit
import threading

import modal
import modal.exception

from config import MODAL_APP_NAME

_cache: dict[str, object] = {}
_lock = threading.Lock()
_ephemeral = False


def _start_ephemeral():
    global _ephemeral
    import modal_app

    print(f"[remote] deployed app '{MODAL_APP_NAME}' not found — starting ephemeral Modal app")
    output_ctx = modal.enable_output()
    output_ctx.__enter__()
    run_ctx = modal_app.app.run()
    run_ctx.__enter__()

    def _shutdown():
        run_ctx.__exit__(None, None, None)
        output_ctx.__exit__(None, None, None)

    atexit.register(_shutdown)
    _cache.clear()
    _ephemeral = True


def _service(name: str):
    if name not in _cache:
        if _ephemeral:
            import modal_app

            _cache[name] = getattr(modal_app, name)()
        else:
            _cache[name] = modal.Cls.from_name(MODAL_APP_NAME, name)()
    return _cache[name]


def _call(service_name: str, method: str, *args):
    try:
        return getattr(_service(service_name), method).remote(*args)
    except modal.exception.NotFoundError:
        with _lock:
            if not _ephemeral:
                _start_ephemeral()
        return getattr(_service(service_name), method).remote(*args)
    except modal.exception.ConflictError:
        # cached handle points at a stopped app version (redeploy happened);
        # refresh handle and retry once
        _cache.pop(service_name, None)
        return getattr(_service(service_name), method).remote(*args)


def embed_images(images: list[bytes]) -> list[list[float]]:
    return _call("EmbeddingService", "embed_images", images)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _call("EmbeddingService", "embed_texts", texts)


def vlm_chat(
    messages: list[dict], max_new_tokens: int = 512, json_schema: dict | None = None
) -> str:
    return _call("VLMService", "chat", messages, max_new_tokens, json_schema)


def vlm_look(images: list[bytes], question: str) -> list[str]:
    return _call("VLMService", "look", images, question)


def vlm_caption(images: list[bytes]) -> list[str]:
    return _call("VLMService", "caption", images)


def whisper_transcribe(audio: bytes, suffix: str = ".ogg") -> list[dict]:
    return _call("WhisperService", "transcribe", audio, suffix)
