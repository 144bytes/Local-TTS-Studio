"""Engine-agnostic TTS interface.

The rest of the app depends only on TTSProvider. Add an engine = implement this
in app/tts/<name>_engine.py + add it to config/model_capabilities.json.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from ..prosody import Style


@dataclass
class Voice:
    id: str
    label: str
    gender: str = "unknown"                 # male | female | unknown
    languages: list[str] = field(default_factory=list)   # [] = all engine languages
    reference_wav: str | None = None        # absolute path, or None for a built-in voice
    reference_text: str | None = None       # transcript of reference_wav (some engines need it, e.g. Qwen)
    builtin: bool = False
    engines: list[str] = field(default_factory=list)     # [] = usable by any engine


class EngineError(RuntimeError):
    """User-facing engine failure (message is safe to show in the UI)."""


class ModelNotInstalled(EngineError):
    pass


class TTSProvider(ABC):
    name: str = "base"
    sample_rate: int = 24000
    supports_languages: bool = True

    # -- lifecycle -----------------------------------------------------------
    @abstractmethod
    def initialize(self) -> None:
        """Load weights into memory. Heavy; called once. Raise ModelNotInstalled
        if the local weights are missing (never download)."""

    @abstractmethod
    def shutdown(self) -> None:
        """Free the model and release GPU memory."""

    @property
    @abstractmethod
    def is_ready(self) -> bool: ...

    # -- capabilities ------------------------------------------------------
    @abstractmethod
    def list_voices(self) -> list[Voice]: ...

    @abstractmethod
    def list_languages(self) -> list[str]: ...

    def supports_feature(self, feature: str) -> bool:
        from ..capabilities import supports

        try:
            return supports(self.name, feature)
        except KeyError:
            return False

    def describe(self) -> dict:
        return {
            "name": self.name,
            "sample_rate": self.sample_rate,
            "ready": self.is_ready,
            "languages": self.list_languages(),
            "voices": [v.__dict__ for v in self.list_voices()],
        }

    # -- synthesis -------------------------------------------------------
    @abstractmethod
    def generate(self, text: str, voice: Voice, style: Style,
                 seed: int | None = None) -> np.ndarray:
        """One semantic chunk -> float32 mono @ self.sample_rate.
        ``seed`` makes the stochastic realization reproducible / distinct per variant.
        gain_db / EQ are applied by the pipeline afterwards, not here."""

    # -- helpers -------------------------------------------------------
    def validate_language(self, lang: str) -> str:
        langs = self.list_languages()
        if not langs or lang in langs:
            return lang
        raise EngineError(
            f"Language '{lang}' is not supported by engine '{self.name}'. "
            f"Supported: {', '.join(langs)}."
        )
