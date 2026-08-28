"""File-backed voice registry.

A voice = a folder under voices/:
    voices/en_male/
        voice.json     {id, label, gender, languages, reference}
        reference.wav  5-20 s clean single-speaker speech (for zero-shot cloning)

Engines also expose built-in voices (no reference). Folder voices override
built-ins on id collision.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .config import VOICES_DIR
from .tts.base import TTSProvider, Voice

_VALID_GENDERS = {"male", "female", "unknown"}
_SLUG_OK = "abcdefghijklmnopqrstuvwxyz0123456789_-"


def slug_voice_id(s: str) -> str:
    s = (s or "").strip().lower().replace(" ", "_")
    s = "".join(c for c in s if c in _SLUG_OK).strip("_-")
    return s[:40] or "voice"


def _ffmpeg() -> str:
    from .audio import ffmpeg_exe

    exe = ffmpeg_exe()
    if exe is None:
        raise RuntimeError("FFmpeg not available (needed to process the reference clip).")
    return exe


def _to_mono24(src: Path, dst: Path, exe: str) -> None:
    r = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(dst)],
        capture_output=True,
    )
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError(f"FFmpeg could not read '{src.name}': "
                           + r.stderr.decode("utf-8", "replace")[-200:])


def combine_sources(sources: list[Path], dst: Path, exe: str, gap_ms: int = 140) -> None:
    """Decode each clip to mono/24 kHz and concatenate them (with a short silence
    between) into one WAV at ``dst``. Order is preserved."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        parts: list[Path] = []
        gap = tdp / "_gap.wav"
        subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"anullsrc=r=24000:cl=mono", "-t", f"{gap_ms/1000:.3f}",
             "-c:a", "pcm_s16le", str(gap)],
            check=True, capture_output=True,
        )
        for i, s in enumerate(sources):
            w = tdp / f"{i:03d}.wav"
            _to_mono24(s, w, exe)
            if i > 0:
                parts.append(gap)
            parts.append(w)

        listfile = tdp / "list.txt"
        listfile.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
             "-safe", "0", "-i", str(listfile), "-c:a", "pcm_s16le", str(dst)],
            capture_output=True,
        )
        if r.returncode != 0 or not dst.exists():
            raise RuntimeError("FFmpeg failed to combine the clips: "
                               + r.stderr.decode("utf-8", "replace")[-200:])


def clean_reference_audio(src: Path, dst: Path, max_seconds: int = 45) -> float:
    """Mono / 24 kHz, trim long leading+trailing silence, collapse mid-gaps > 0.7 s,
    loudness-normalise, cap length. Returns the resulting duration in seconds."""
    exe = _ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    af = (
        # trim long silence at the very start and end
        "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB:detection=peak,"
        "areverse,"
        "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB:detection=peak,"
        "areverse,"
        # shorten any internal gap longer than ~0.7 s (e.g. between combined takes)
        "silenceremove=stop_periods=-1:stop_duration=0.7:stop_threshold=-45dB:detection=peak,"
        "loudnorm=I=-18:TP=-2:LRA=11"
    )
    r = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-af", af, "-ac", "1", "-ar", "24000", "-t", str(int(max_seconds)), str(dst)],
        capture_output=True,
    )
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1000:
        raise RuntimeError("FFmpeg could not process the audio: "
                           + r.stderr.decode("utf-8", "replace")[-300:])
    try:
        import soundfile as sf

        info = sf.info(str(dst))
        return info.frames / info.samplerate
    except Exception:
        return 0.0


def _load_folder_voice(folder: Path) -> Voice | None:
    meta_path = folder / "voice.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"voices/{folder.name}/voice.json is invalid JSON: {e}") from e

    vid = str(meta.get("id") or folder.name)
    ref_name = meta.get("reference", "reference.wav")
    ref_path = (folder / ref_name) if ref_name else None
    if ref_path is not None and not ref_path.exists():
        raise FileNotFoundError(
            f"voice '{vid}' references '{ref_name}' but voices/{folder.name}/{ref_name} is missing."
        )
    gender = str(meta.get("gender", "unknown")).lower()
    if gender not in _VALID_GENDERS:
        gender = "unknown"
    return Voice(
        id=vid, label=str(meta.get("label", vid)), gender=gender,
        languages=list(meta.get("languages", []) or []),
        reference_wav=str(ref_path.resolve()) if ref_path else None,
        reference_text=meta.get("reference_text") or None,
        engines=list(meta.get("engines", []) or []),
        builtin=False,
    )


def discover_folder_voices() -> list[Voice]:
    voices: list[Voice] = []
    for folder in sorted(p for p in VOICES_DIR.iterdir() if p.is_dir()):
        v = _load_folder_voice(folder)
        if v is not None:
            voices.append(v)
    return voices


class VoiceRegistry:
    def __init__(self, provider: TTSProvider):
        self._provider = provider
        self._engine = getattr(provider, "name", "")
        self._builtin_ids: set[str] = set()
        self._voices: dict[str, Voice] = {}
        self.reload()

    def reload(self) -> None:
        merged: dict[str, Voice] = {}
        self._builtin_ids = set()
        for v in self._provider.list_voices():          # engine built-ins
            merged[v.id] = v
            self._builtin_ids.add(v.id)
        for v in discover_folder_voices():              # folder voices override
            merged[v.id] = v
        self._voices = merged

    def _usable(self, v: Voice) -> bool:
        if v.id in self._builtin_ids:
            return True
        if v.engines and self._engine not in v.engines:
            return False
        return True

    def all(self) -> list[Voice]:
        return sorted((v for v in self._voices.values() if self._usable(v)),
                      key=lambda v: (v.gender, v.label))

    def get(self, voice_id: str) -> Voice:
        if voice_id not in self._voices:
            raise KeyError(f"Unknown voice '{voice_id}'. Available: {', '.join(self.ids())}")
        return self._voices[voice_id]

    def ids(self) -> list[str]:
        return sorted(v.id for v in self._voices.values() if self._usable(v))


class VoiceExists(ValueError):
    pass


def is_builtin_id(provider: TTSProvider, voice_id: str) -> bool:
    return any(v.id == voice_id for v in provider.list_voices())


def add_voice(voice_id: str, label: str, gender: str,
              src_audio: str | Path | list[str | Path],
              *, reference_text: str | None = None, languages: list[str] | None = None,
              engines: list[str] | None = None, clean: bool = True,
              max_seconds: int = 45, overwrite: bool = False) -> dict:
    """Create/replace voices/<id>/ from one recording OR several short ones (which
    are combined in order). Returns {voice, duration, warnings, sources}."""
    vid = slug_voice_id(voice_id)
    sources = [Path(p) for p in (src_audio if isinstance(src_audio, (list, tuple)) else [src_audio])]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    if not sources:
        raise ValueError("no audio provided")

    folder = VOICES_DIR / vid
    if folder.exists() and not overwrite:
        raise VoiceExists(f"Voice '{vid}' already exists. Use overwrite to replace it.")
    folder.mkdir(parents=True, exist_ok=True)
    ref = folder / "reference.wav"
    warnings: list[str] = []

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        if len(sources) == 1:
            staged = sources[0]
        else:
            staged = Path(td) / "combined.wav"
            combine_sources(sources, staged, _ffmpeg())
        if clean:
            dur = clean_reference_audio(staged, ref, max_seconds)
        else:
            shutil.copyfile(staged, ref)
            try:
                import soundfile as sf

                info = sf.info(str(ref))
                dur = info.frames / info.samplerate
            except Exception:
                dur = 0.0

    if len(sources) > 1:
        warnings.append(f"Combined {len(sources)} clips into a {dur:.1f}s reference "
                        f"(order = the order you selected them).")
    if dur and dur < 6:
        warnings.append(f"Reference is only {dur:.1f}s — aim for 15–40 s total of clean speech.")
    if dur and dur >= max_seconds - 0.5:
        warnings.append(f"Reference was capped at {max_seconds}s. That's plenty; extra audio is trimmed.")

    gender = (gender or "unknown").lower()
    if gender not in _VALID_GENDERS:
        gender = "unknown"
    meta = {
        "id": vid, "label": (label or vid).strip(), "gender": gender,
        "languages": list(languages or []), "reference": "reference.wav",
        "engines": list(engines or []),
    }
    if (reference_text or "").strip():
        meta["reference_text"] = reference_text.strip()
    else:
        warnings.append("No transcript given — this voice will work with Chatterbox but "
                        "NOT with Qwen (add a transcript later).")
    (folder / "voice.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"voice": _load_folder_voice(folder).__dict__, "duration": round(dur, 1),
            "warnings": warnings, "sources": len(sources)}


def update_voice_meta(voice_id: str, **fields) -> dict:
    folder = VOICES_DIR / voice_id
    p = folder / "voice.json"
    if not p.exists():
        raise FileNotFoundError(f"voices/{voice_id}/voice.json")
    meta = json.loads(p.read_text(encoding="utf-8"))
    for k in ("label", "gender", "languages", "engines", "reference_text"):
        if k in fields and fields[k] is not None:
            meta[k] = fields[k]
    if meta.get("gender", "unknown") not in _VALID_GENDERS:
        meta["gender"] = "unknown"
    p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return _load_folder_voice(folder).__dict__


def delete_voice(voice_id: str) -> None:
    folder = VOICES_DIR / voice_id
    if not (folder / "voice.json").exists():
        raise FileNotFoundError(voice_id)
    shutil.rmtree(folder)


def list_all(provider: TTSProvider) -> list[dict]:
    """Every voice (builtin + folder) with UI-relevant flags."""
    builtins = {v.id for v in provider.list_voices()}
    reg = VoiceRegistry(provider)
    out = []
    for v in sorted(reg._voices.values(), key=lambda x: (x.gender, x.label)):
        out.append({
            **v.__dict__,
            "kind": "built-in" if v.id in builtins else "custom",
            "has_reference": bool(v.reference_wav),
            "has_transcript": bool(v.reference_text),
            "usable_now": reg._usable(v),
        })
    return out
