"""Cheap text metrics for the editor: word count + estimated spoken duration."""
from __future__ import annotations

from .prosody import TextPrep


def metrics(text: str, config: dict, preset: str | None = None, speed: float = 1.0) -> dict:
    preset = preset or config.get("default_preset", "обычный")
    prep = TextPrep(config)
    pt = prep.prepare(text or "", preset, config.get("language", "ru"))

    wpm = float(config.get("estimate", {}).get("words_per_minute", 135)) or 135.0
    speak_seconds = (pt.n_words / wpm) * 60.0 / max(float(speed or 1.0), 0.1)
    pause_seconds = sum(s.duration_ms for s in pt.segments if not s.is_speech) / 1000.0
    total = speak_seconds + pause_seconds

    return {
        "chars_total": len(text or ""),
        "chars_spoken": len(pt.spoken_text),
        "words": pt.n_words,
        "segments_speech": sum(1 for s in pt.segments if s.is_speech),
        "segments_silence": sum(1 for s in pt.segments if not s.is_speech),
        "pause_seconds": round(pause_seconds, 1),
        "estimated_seconds": round(total, 1),
        "estimated_clock": _clock(total),
        "warnings": pt.warnings,
        "normalized_preview": pt.normalized[:400],
    }


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
