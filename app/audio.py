"""Audio assembly + post-processing.

float32 mono, ~[-1, 1], engine sample rate (24 kHz for Chatterbox/Kokoro).

No system FFmpeg is assumed: ``imageio-ffmpeg`` ships a static binary; a PATH
FFmpeg is only a fallback. WAV always works (soundfile); MP3 and the DSP
post-chain need FFmpeg.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf


class FFmpegNotFound(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def have_ffmpeg() -> bool:
    return ffmpeg_exe() is not None


# --- primitives ------------------------------------------------------------
def silence(duration_ms: int, sample_rate: int) -> np.ndarray:
    n = max(0, int(round(sample_rate * duration_ms / 1000.0)))
    return np.zeros(n, dtype=np.float32)


def apply_gain_db(samples: np.ndarray, gain_db: float) -> np.ndarray:
    if not gain_db:
        return samples
    return (samples * (10.0 ** (gain_db / 20.0))).astype(np.float32)


def peak_normalize(samples: np.ndarray, peak_dbfs: float = -1.0) -> np.ndarray:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < 1e-6:
        return samples
    return (samples * (10.0 ** (peak_dbfs / 20.0) / peak)).astype(np.float32)


def concat(parts: list[np.ndarray]) -> np.ndarray:
    parts = [p for p in parts if p is not None and p.size]
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32)


def time_stretch(samples: np.ndarray, sample_rate: int, factor: float) -> np.ndarray:
    """Change tempo by ``factor`` (>1 faster) without changing pitch (FFmpeg atempo)."""
    if abs(factor - 1.0) < 1e-3 or samples.size == 0:
        return samples
    factor = float(np.clip(factor, 0.5, 2.0))
    exe = ffmpeg_exe()
    if exe is None:
        idx = np.round(np.arange(0, samples.size, factor)).astype(np.int64)
        return samples[idx[idx < samples.size]].astype(np.float32)
    return _run_filter(exe, samples, sample_rate, f"atempo={factor:.6f}")


def pitch_shift(samples: np.ndarray, sample_rate: int, semitones: float) -> np.ndarray:
    """Shift pitch by ``semitones`` keeping duration (resample + compensating atempo)."""
    if abs(semitones) < 0.05 or samples.size == 0:
        return samples
    exe = ffmpeg_exe()
    if exe is None:
        return samples
    ratio = 2.0 ** (float(np.clip(semitones, -12, 12)) / 12.0)
    new_sr = int(round(sample_rate * ratio))
    af = f"asetrate={new_sr},aresample={sample_rate},atempo={1.0 / ratio:.6f}"
    return _run_filter(exe, samples, sample_rate, af)


def equalize(samples: np.ndarray, sample_rate: int, eq: dict) -> np.ndarray:
    """Gentle tone shaping. eq keys: high_pass_hz, low_shelf_db, presence_db."""
    if not eq or samples.size == 0:
        return samples
    exe = ffmpeg_exe()
    if exe is None:
        return samples
    clauses: list[str] = []
    hp = int(eq.get("high_pass_hz", 0) or 0)
    if hp > 0:
        clauses.append(f"highpass=f={hp}")
    low = float(eq.get("low_shelf_db", 0.0) or 0.0)
    if low:
        clauses.append(f"bass=g={low}:f=140")
    pres = float(eq.get("presence_db", 0.0) or 0.0)
    if pres:
        clauses.append(f"treble=g={pres}:f=4200")
    if not clauses:
        return samples
    return _run_filter(exe, samples, sample_rate, ",".join(clauses))


# --- post-processing chain ------------------------------------------------
def build_post_filter(post: dict, sample_rate: int) -> list[str]:
    """Return an ordered list of FFmpeg -af filter clauses for the enabled steps."""
    if not post or not post.get("enabled", True):
        return []
    f: list[str] = []
    if post.get("eq"):
        hp = int(post.get("eq_high_pass_hz", 0) or 0)
        if hp > 0:
            f.append(f"highpass=f={hp}")
        low = float(post.get("eq_low_shelf_db", 0.0) or 0.0)
        if low:
            f.append(f"bass=g={low}:f=120")
        pres = float(post.get("eq_presence_db", 0.0) or 0.0)
        if pres:
            f.append(f"treble=g={pres}:f=4500")
    if post.get("compressor"):
        thr = float(post.get("compressor_threshold_db", -18.0))
        ratio = float(post.get("compressor_ratio", 3.0))
        f.append(f"acompressor=threshold={thr}dB:ratio={ratio}:attack=8:release=180:makeup=2")
    if post.get("loudness_normalize"):
        i = float(post.get("target_lufs", -16.0))
        tp = float(post.get("true_peak_db", -1.5))
        f.append(f"loudnorm=I={i}:TP={tp}:LRA=11")
    if post.get("limiter"):
        ceil = float(post.get("limiter_ceiling_db", -1.0))
        lvl = 10.0 ** (ceil / 20.0)
        f.append(f"alimiter=limit={lvl:.4f}:level=disabled")
    return f


def post_process(samples: np.ndarray, sample_rate: int, post: dict) -> tuple[np.ndarray, list[str]]:
    clauses = build_post_filter(post, sample_rate)
    if not clauses or samples.size == 0:
        return samples, []
    exe = ffmpeg_exe()
    if exe is None:
        return samples, []
    out = _run_filter(exe, samples, sample_rate, ",".join(clauses))
    return out, clauses


def master_post(samples: np.ndarray, sample_rate: int, post: dict) -> tuple[np.ndarray, list[str]]:
    """Master chain over the whole track: [denoise] -> [compress] -> loudnorm -> limiter."""
    if samples.size == 0:
        return samples, []
    exe = ffmpeg_exe()
    if exe is None:
        return peak_normalize(samples, -1.0), ["peak_normalize"]
    f: list[str] = []
    if post.get("denoise"):
        f.append("afftdn=nr=10:nf=-25")
    if post.get("compressor"):
        f.append("acompressor=threshold=-18dB:ratio=3:attack=8:release=180:makeup=2")
    if post.get("loudness_normalize", True):
        i = float(post.get("target_lufs", -16.0))
        tp = float(post.get("true_peak_db", -1.5))
        f.append(f"loudnorm=I={i}:TP={tp}:LRA=11")
    if post.get("limiter", True):
        ceil = float(post.get("limiter_ceiling_db", -1.0))
        f.append(f"alimiter=limit={10.0 ** (ceil / 20.0):.4f}:level=disabled")
    if not f:
        return peak_normalize(samples, -1.0), ["peak_normalize"]
    out = _run_filter(exe, samples, sample_rate, ",".join(f))
    return out, [c.split("=")[0] for c in f]


# --- file output ---------------------------------------------------------
def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples.astype(np.float32), sample_rate, subtype="PCM_16")
    return path


def write_mp3(path: Path, samples: np.ndarray, sample_rate: int, bitrate: str = "160k") -> Path:
    exe = ffmpeg_exe()
    if exe is None:
        raise FFmpegNotFound(
            "FFmpeg is required for MP3 output but was not found. "
            "setup.bat installs it via `pip install imageio-ffmpeg`."
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0).astype("<f4").tobytes()
    base = [exe, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0"]
    proc = None
    for codec in (["-codec:a", "libmp3lame", "-b:a", bitrate], ["-codec:a", "mp3", "-b:a", bitrate]):
        proc = subprocess.run(base + codec + [str(path)], input=pcm, capture_output=True)
        if proc.returncode == 0 and path.exists() and path.stat().st_size > 0:
            return path
    raise RuntimeError("FFmpeg MP3 encode failed: " + (proc.stderr.decode("utf-8", "replace")[-400:] if proc else "?"))


def measure_lufs(samples: np.ndarray, sample_rate: int) -> float | None:
    """Integrated loudness via FFmpeg ebur128 (best-effort; None if unavailable)."""
    exe = ffmpeg_exe()
    if exe is None or samples.size == 0:
        return None
    pcm = np.clip(samples, -1.0, 1.0).astype("<f4").tobytes()
    proc = subprocess.run(
        [exe, "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(sample_rate), "-ac", "1",
         "-i", "pipe:0", "-filter_complex", "ebur128", "-f", "null", "-"],
        input=pcm, capture_output=True,
    )
    text = proc.stderr.decode("utf-8", "replace")
    val = None
    for line in text.splitlines():
        if "I:" in line and "LUFS" in line:
            try:
                val = float(line.split("I:")[1].split("LUFS")[0].strip())
            except (ValueError, IndexError):
                pass
    return val


def _run_filter(exe: str, samples: np.ndarray, sample_rate: int, af: str) -> np.ndarray:
    pcm = np.clip(samples, -1.0, 1.0).astype("<f4").tobytes()
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "f32le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
         "-af", af, "-f", "f32le", "-ac", "1", "-ar", str(sample_rate), "pipe:1"],
        input=pcm, capture_output=True,
    )
    if proc.returncode != 0:
        return samples
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)
