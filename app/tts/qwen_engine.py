"""Qwen3-TTS provider (Alibaba Qwen team, Apache-2.0).

Model: Qwen/Qwen3-TTS-12Hz-1.7B-Base — zero-shot voice cloning from a reference
clip + its transcript. Downloaded once by scripts/download_models.py into
models/qwen-1.7b/ and loaded with local_files_only=True.

Delivery is driven by the selected preset: sampling params (temperature, top_p,
repetition_penalty) are passed here; speed / pitch / EQ / pauses are applied by
the pipeline. Every voice needs reference_wav + reference_text.
The clone prompt is cached per (file, mtime, transcript) so re-uploading a voice
at the same id refreshes it.
"""
from __future__ import annotations

import logging
import os
import threading

import numpy as np

from ..capabilities import is_installed
from ..config import MODELS_DIR
from ..prosody import Style
from .base import EngineError, ModelNotInstalled, TTSProvider, Voice

log = logging.getLogger("localtts.qwen")

LOCAL_DIR = MODELS_DIR / "qwen-1.7b"
REQUIRED = ("config.json", "model.safetensors", "speech_tokenizer/config.json")

# our BCP-ish code -> the language name Qwen expects
_LANG = {
    "en": "English", "ru": "Russian", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "de": "German", "fr": "French", "pt": "Portuguese",
    "es": "Spanish", "it": "Italian",
}


class QwenTTSProvider(TTSProvider):
    name = "qwen"
    sample_rate = 24000
    supports_languages = True

    def __init__(self, device: str = "auto"):
        self._device_pref = device
        self._device = "cpu"
        self._model = None
        self._prompt_cache: dict[str, object] = {}
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def initialize(self) -> None:
        if self._model is not None:
            return
        ok, msg = is_installed("qwen")
        if not ok:
            raise ModelNotInstalled(msg)
        try:
            import torch
        except Exception as e:
            raise EngineError(f"PyTorch is not available: {e}") from e
        try:
            from ..quiet import suppress_stdio

            with suppress_stdio():   # hush the `sox` package's "SoX not found" banner
                from qwen_tts import Qwen3TTSModel
        except Exception:
            # suppression can fail on detached processes — retry a plain import so a
            # real error surfaces (and we just live with the cosmetic banner).
            from qwen_tts import Qwen3TTSModel

        self._device = _resolve_device(self._device_pref)
        dtype = torch.bfloat16 if self._device == "cuda" else torch.float32
        dev_map = "cuda:0" if self._device == "cuda" else "cpu"
        log.info("Loading Qwen3-TTS 1.7B on %s (%s) from %s", self._device, dtype, LOCAL_DIR)
        try:
            self._model = Qwen3TTSModel.from_pretrained(
                str(LOCAL_DIR), local_files_only=True,
                device_map=dev_map, dtype=dtype, attn_implementation="sdpa",
            )
        except Exception as e:
            raise EngineError(f"Failed to load the Qwen3-TTS model: {e}") from e
        _log_vram(self._device)
        log.info("Qwen3-TTS ready.")

    def shutdown(self) -> None:
        with self._lock:
            self._model = None
            self._prompt_cache.clear()
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass
        log.info("Qwen3-TTS shut down, GPU memory released.")

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    # -- capabilities --------------------------------------------------
    def list_voices(self) -> list[Voice]:
        return []  # reference-only; folder voices with reference_text are added by the registry

    def list_languages(self) -> list[str]:
        return sorted(_LANG)

    def invalidate_voice(self, reference_wav: str | None) -> None:
        """Drop the cached clone prompt for a reference file (call after a voice
        is re-added/edited at the same path)."""
        for k in list(self._prompt_cache):
            if reference_wav is None or k[0] == reference_wav:
                self._prompt_cache.pop(k, None)

    # -- synthesis ---------------------------------------------------
    def generate(self, text: str, voice: Voice, style: Style,
                 seed: int | None = None) -> np.ndarray:
        if self._model is None:
            raise EngineError("Модель Qwen3-TTS не загружена.")
        text = (text or "").strip()
        if not text:
            return np.zeros(0, dtype=np.float32)
        if seed is not None:
            import torch

            torch.manual_seed(int(seed) & 0x7FFFFFFF)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(int(seed) & 0x7FFFFFFF)
        if not voice.reference_wav or not voice.reference_text:
            raise EngineError(
                f"Голосу «{voice.id}» нужны референс-аудио и его расшифровка. "
                f"Добавьте/отредактируйте голос через 🎙 / ⚙."
            )
        lang = _LANG.get(self.validate_language((style.language or "ru").lower()), "Russian")

        # cache key includes the file's mtime, so re-uploading the same id refreshes it
        try:
            mtime = os.path.getmtime(voice.reference_wav)
        except OSError:
            mtime = 0.0
        key = (voice.reference_wav, round(mtime, 3), voice.reference_text)

        with self._lock:
            prompt = self._prompt_cache.get(key)
            if prompt is None:
                self.invalidate_voice(voice.reference_wav)
                try:
                    prompt = self._model.create_voice_clone_prompt(
                        ref_audio=voice.reference_wav, ref_text=voice.reference_text)
                except Exception as e:
                    raise EngineError(f"Не удалось подготовить референс «{voice.id}»: {e}") from e
                self._prompt_cache[key] = prompt
            try:
                wavs, sr = self._model.generate_voice_clone(
                    text=text, language=lang, voice_clone_prompt=prompt, non_streaming_mode=True,
                    temperature=float(style.temperature),
                    top_p=float(style.top_p),
                    repetition_penalty=float(style.repetition_penalty),
                )
            except RuntimeError as e:
                raise EngineError(_runtime_hint(e, self._device)) from e
            except Exception as e:
                raise EngineError(f"Сбой генерации Qwen: {e}") from e

        self.sample_rate = int(sr)
        arr = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
        return np.asarray(arr, dtype=np.float32).reshape(-1)


def _resolve_device(pref: str) -> str:
    pref = (pref or "auto").lower()
    if pref == "cpu":
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if pref == "cuda":
            log.warning("device='cuda' requested but CUDA is unavailable; using CPU (slow for 1.7B).")
    except Exception as e:
        log.warning("torch CUDA probe failed (%s); using CPU.", e)
    return "cpu"


def _log_vram(device: str) -> None:
    try:
        import torch

        if device == "cuda":
            free, total = torch.cuda.mem_get_info()
            log.info("CUDA VRAM after load: %.2f / %.2f GB free", free / 1e9, total / 1e9)
    except Exception:
        pass


def _runtime_hint(e: Exception, device: str) -> str:
    low = str(e).lower()
    if "out of memory" in low:
        return ("Не хватило видеопамяти. Qwen 1.7B нужно ~8 ГБ — закройте другие GPU-приложения, "
                "уменьшите длину текста, или задайте \"device\": \"cpu\" в config.json. "
                "Оригинал: " + str(e))
    return f"Сбой генерации Qwen на {device}: {e}"
