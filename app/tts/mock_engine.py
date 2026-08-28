"""Mock engine — deterministic tone synthesis, no model, no GPU. For the test suite."""
from __future__ import annotations

import numpy as np

from ..prosody import Style
from .base import TTSProvider, Voice


class MockProvider(TTSProvider):
    name = "mock"
    sample_rate = 24000
    supports_languages = True

    def __init__(self, device: str = "cpu"):
        self._ready = False
        self._prompt_cache: dict = {}

    def initialize(self) -> None:
        self._ready = True

    def shutdown(self) -> None:
        self._ready = False
        self._prompt_cache.clear()

    @property
    def is_ready(self) -> bool:
        return self._ready

    def invalidate_voice(self, reference_wav):
        self._prompt_cache.clear()

    def list_voices(self) -> list[Voice]:
        return []  # reference-only, like the real engine

    def list_languages(self) -> list[str]:
        return ["ru", "en"]

    def generate(self, text: str, voice: Voice, style: Style,
                 seed: int | None = None) -> np.ndarray:
        text = (text or "").strip()
        if not text:
            return np.zeros(0, dtype=np.float32)
        self.validate_language((style.language or "ru").lower())
        rng = np.random.default_rng(seed if seed is not None else None)
        dur = min(45.0, max(0.4, len(text) / 14.0))
        n = int(self.sample_rate * dur)
        t = np.arange(n) / self.sample_rate
        f0 = (130.0 if voice.gender == "male" else 190.0) * (0.9 + 0.2 * style.temperature)
        f0 *= 1.0 + 0.03 * rng.standard_normal()   # per-seed variation
        sig = 0.2 * np.sin(2 * np.pi * f0 * t) + 0.05 * np.sin(2 * np.pi * 2 * f0 * t)
        env = np.minimum(1.0, np.minimum(t * 8, (dur - t) * 8))
        return (sig * env).astype(np.float32)
