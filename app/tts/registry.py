"""Engine registry. LocalTTS Studio runs Qwen3-TTS only; 'mock' is for tests."""
from __future__ import annotations

import importlib

from ..capabilities import engine_caps, engine_ids
from .base import TTSProvider

_FALLBACK = {
    "qwen": "app.tts.qwen_engine:QwenTTSProvider",
    "mock": "app.tts.mock_engine:MockProvider",
}


def available_engines() -> list[str]:
    return engine_ids() or sorted(_FALLBACK)


def _load_class(path: str):
    mod_name, _, cls_name = path.partition(":")
    return getattr(importlib.import_module(mod_name), cls_name)


def create_provider(engine: str = "qwen", device: str = "auto") -> TTSProvider:
    engine = (engine or "qwen").lower()
    try:
        path = engine_caps(engine).get("class") or _FALLBACK.get(engine)
    except KeyError:
        path = _FALLBACK.get(engine)
    if not path:
        raise ValueError(f"Unknown engine '{engine}'.")
    cls = _load_class(path)
    try:
        return cls(device=device)
    except TypeError:
        return cls()
