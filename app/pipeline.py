"""Synthesis pipeline (Qwen3-TTS, Russian, preset-driven, multi-variant).

script + preset (+ N variants)
   -> TextPrep.prepare()   (normalize -> stress -> semantic chunks -> pauses)
   -> per variant, per chunk: torch.manual_seed(variant_seed); Qwen ICL
      (temperature / top_p / repetition_penalty from the preset)
   -> concat chunk audio + context pauses  ->  raw.wav
   -> per-chunk gain + optional EQ (minimal — NO pitch shift, NO per-chunk stretch)
   -> master: [compress] -> loudnorm(-16 LUFS) -> limiter          ->  final.wav
   -> optional single global time-stretch for the UI speed slider
   -> final.mp3
   -> projects/workspace/generations/<id>/variant_<n>/  + a debug report.json
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import audio
from .config import OUTPUT_DIR
from .estimate import _clock
from .projects import Project
from .prosody import Segment, Style, TextPrep
from .tts.base import EngineError, TTSProvider
from .voices import VoiceRegistry

log = logging.getLogger("localtts.pipeline")


@dataclass
class GenerationRequest:
    text: str
    voice_id: str
    preset: str = "обычный"
    language: str = "ru"
    speed: float = 1.0
    n_variants: int = 1
    mp3_bitrate: str = "192k"
    post_enabled: bool = True
    make_mp3: bool = True
    seed: int | None = None          # base seed; variant k uses seed+k
    keep_raw: bool | None = None
    filename_hint: str = "narration"


@dataclass
class VariantResult:
    index: int
    seed: int
    raw_wav: str | None
    final_wav: str | None
    final_mp3: str | None
    duration_sec: float
    duration_clock: str
    lufs: float | None
    error: str | None = None


@dataclass
class GenerationResult:
    generation_id: str
    variants: list[VariantResult]
    n_speech: int
    n_silence: int
    warnings: list[str]
    elapsed_sec: float
    realtime_factor: float
    voice: str
    preset: str
    post_applied: list[str]
    normalized_text: str
    original_text: str
    report_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "generation_id": self.generation_id,
            "variants": [asdict(v) for v in self.variants],
            "n_speech": self.n_speech, "n_silence": self.n_silence,
            "warnings": self.warnings, "elapsed_sec": self.elapsed_sec,
            "realtime_factor": self.realtime_factor, "voice": self.voice,
            "preset": self.preset, "post_applied": self.post_applied,
            "normalized_text": self.normalized_text, "original_text": self.original_text,
        }


class Pipeline:
    def __init__(self, provider: TTSProvider, voices: VoiceRegistry, config: dict):
        self.provider = provider
        self.voices = voices
        self.config = config
        self.prep = TextPrep(config)

    # -- planning (no synthesis) --------------------------------------
    def plan(self, req: "GenerationRequest") -> tuple[list[Segment], list[str], dict]:
        pt = self.prep.prepare(req.text, req.preset, req.language, seed=req.seed)
        return pt.segments, pt.warnings, {
            "original": pt.original, "normalized": pt.normalized,
            "replacements": pt.replacements, "plan": pt.plan(),
        }

    # -- full synthesis --------------------------------------------
    def generate(self, req: "GenerationRequest", project: Project | None = None) -> GenerationResult:
        if not (req.text or "").strip():
            raise EngineError("Пустой сценарий — вставьте текст.")
        try:
            voice = self.voices.get(req.voice_id)
        except KeyError as e:
            raise EngineError(str(e).strip('"')) from e
        if not voice.reference_wav:
            raise EngineError(f"У голоса «{voice.id}» нет референс-аудио. Добавьте его через 🎙.")
        if not voice.reference_text:
            raise EngineError(f"У голоса «{voice.id}» нет расшифровки. Откройте ⚙ и добавьте текст записи.")

        language = self.provider.validate_language((req.language or "ru").lower()) \
            if getattr(self.provider, "supports_languages", True) else "ru"
        req.language = language

        n = max(1, min(5, int(req.n_variants or 1)))
        base_seed = req.seed if req.seed is not None else random.randint(1, 2**31 - 1)

        pt = self.prep.prepare(req.text, req.preset, language, seed=base_seed)
        segs = pt.segments
        if not any(s.is_speech for s in segs):
            raise EngineError("После обработки не осталось текста для озвучивания.")

        acfg = self.config.get("audio", {})
        sr = self.provider.sample_rate
        keep_raw = acfg.get("keep_raw", True) if req.keep_raw is None else req.keep_raw
        post_cfg = dict(acfg.get("post", {}))
        style0 = next((s.style for s in segs if s.is_speech), Style())
        if style0.compress:
            post_cfg["compressor"] = True

        gid, gdir = (project.new_generation_dir() if project
                     else (_dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"), OUTPUT_DIR))

        t0 = time.time()
        variants: list[VariantResult] = []
        post_applied: list[str] = []
        n_speech = sum(1 for s in segs if s.is_speech)
        n_sil = sum(1 for s in segs if not s.is_speech)

        for k in range(n):
            v_seed = base_seed + k
            vdir = (gdir / f"variant_{k + 1}") if (n > 1 or project) else gdir
            vdir.mkdir(parents=True, exist_ok=True)
            stem = "final"
            try:
                raw = self._synthesize(segs, voice, v_seed, sr, k + 1, n)
                if raw.size == 0:
                    raise EngineError("Пустой результат синтеза.")
                if req.post_enabled and post_cfg.get("enabled", True):
                    final, applied = audio.master_post(raw, sr, post_cfg)
                else:
                    final, applied = audio.peak_normalize(raw, -1.0), ["peak_normalize"]
                if acfg.get("global_speed_via_stretch", True) and abs(req.speed - 1.0) > 1e-3:
                    final = audio.time_stretch(final, sr, float(req.speed))
                    applied.append(f"speed×{req.speed:.2f}")
                post_applied = applied
                lufs = audio.measure_lufs(final, sr)
                dur = len(final) / sr

                raw_p = audio.write_wav(vdir / "raw.wav", raw, sr) if keep_raw else None
                final_p = audio.write_wav(vdir / f"{stem}.wav", final, sr)
                mp3_p = None
                if req.make_mp3:
                    try:
                        mp3_p = audio.write_mp3(vdir / f"{stem}.mp3", final, sr,
                                                bitrate=str(req.mp3_bitrate or acfg.get("mp3_bitrate", "192k")))
                    except audio.FFmpegNotFound as e:
                        pt.warnings.append(str(e))
                variants.append(VariantResult(
                    index=k + 1, seed=v_seed,
                    raw_wav=raw_p.name if raw_p else None,
                    final_wav=final_p.name, final_mp3=mp3_p.name if mp3_p else None,
                    duration_sec=round(dur, 2), duration_clock=_clock(dur), lufs=lufs,
                ))
            except EngineError as e:
                log.warning("variant %d failed: %s", k + 1, e)
                variants.append(VariantResult(index=k + 1, seed=v_seed, raw_wav=None,
                                              final_wav=None, final_mp3=None, duration_sec=0.0,
                                              duration_clock="0:00", lufs=None, error=str(e)))
            except Exception as e:
                log.exception("variant %d crashed", k + 1)
                variants.append(VariantResult(index=k + 1, seed=v_seed, raw_wav=None,
                                              final_wav=None, final_mp3=None, duration_sec=0.0,
                                              duration_clock="0:00", lufs=None, error=str(e)))

        elapsed = time.time() - t0
        ok = [v for v in variants if v.error is None]
        total_audio = sum(v.duration_sec for v in ok)
        rtf = round(total_audio / elapsed, 2) if elapsed > 0 else 0.0

        # a copy of variant 1 in output/ for quick access
        if not project and ok and ok[0].final_mp3:
            src = gdir / ("variant_1" if (n > 1) else "") / ok[0].final_mp3
            if not src.exists():
                src = gdir / ok[0].final_mp3
            dst = _unique(OUTPUT_DIR / f"{gid}_{_safe(req.filename_hint)}.mp3")
            if src.exists():
                dst.write_bytes(src.read_bytes())

        report = {
            "generation_id": gid, "at": _dt.datetime.now().isoformat(timespec="seconds"),
            "voice": voice.id, "voice_label": voice.label, "preset": req.preset,
            "language": language, "speed": req.speed, "n_variants": n, "base_seed": base_seed,
            "original_text": pt.original, "normalized_text": pt.normalized,
            "replacements": pt.replacements,
            "generation_parameters": {"temperature": style0.temperature, "top_p": style0.top_p,
                                      "repetition_penalty": style0.repetition_penalty},
            "prosody_plan": pt.plan(),
            "post_processing": {"enabled": req.post_enabled, "applied": post_applied,
                                "target_lufs": post_cfg.get("target_lufs")},
            "variants": [asdict(v) for v in variants],
            "elapsed_sec": round(elapsed, 2), "realtime_factor": rtf,
            "warnings": pt.warnings,
        }
        report_path = gdir / "report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        result = GenerationResult(
            generation_id=gid, variants=variants, n_speech=n_speech, n_silence=n_sil,
            warnings=pt.warnings, elapsed_sec=round(elapsed, 2), realtime_factor=rtf,
            voice=voice.label, preset=req.preset, post_applied=post_applied,
            normalized_text=pt.normalized, original_text=pt.original,
            report_path=str(report_path),
        )

        if project is not None and ok:
            v1 = ok[0]
            project.record_generation(gid, {
                "duration_sec": v1.duration_sec, "duration_clock": v1.duration_clock,
                "voice": voice.label, "preset": req.preset, "n_variants": n,
                "selected": 1, "mp3": v1.final_mp3, "realtime_factor": rtf, "lufs": v1.lufs,
                "warnings": len(pt.warnings),
            })

        log.info("готово: %d вариант(ов), %s аудио за %.1fс (%.2fx RT)",
                 len(ok), _clock(total_audio), elapsed, rtf)
        return result

    # -- one variant --------------------------------------------
    def _synthesize(self, segs: list[Segment], voice, seed: int, sr: int,
                    vi: int, vn: int) -> np.ndarray:
        parts: list[np.ndarray] = []
        speech_i = 0
        for seg in segs:
            if seg.kind == "silence":
                parts.append(audio.silence(seg.duration_ms, sr))
                continue
            speech_i += 1
            log.info("v%d/%d seg %d: %d ch temp=%.2f seed=%d",
                     vi, vn, speech_i, len(seg.text), seg.style.temperature, seed)
            wav = self.provider.generate(seg.text, voice, seg.style, seed=seed + speech_i)
            wav = _segment_touch(wav, sr, seg.style)
            parts.append(wav)
        return audio.concat(parts)


def _segment_touch(wav: np.ndarray, sr: int, style: Style) -> np.ndarray:
    """Minimal per-chunk shaping: EQ + gain only. NO pitch, NO time-stretch."""
    if wav.size == 0:
        return wav
    if style.eq:
        wav = audio.equalize(wav, sr, style.eq)
    if style.gain_db:
        wav = audio.apply_gain_db(wav, style.gain_db)
    return wav


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (s or "").strip()).strip("_")[:48] or "narration"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    k = 2
    while (parent / f"{stem}_{k}{suffix}").exists():
        k += 1
    return parent / f"{stem}_{k}{suffix}"
