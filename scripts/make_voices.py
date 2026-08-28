"""Build the bundled starter voices (offline, deterministic).

Chatterbox clones a voice from a short reference clip. To ship ready male/female
voices without redistributing anyone's audio, we build the references locally
from Windows SAPI voices, then Chatterbox re-synthesises them in its own style.

    voices/en_male/          SAPI en-US female, pitch-shifted down
    voices/en_female_bright/ SAPI en-US female (Zira ...)
    voices/ru_female/        SAPI ru-RU female (Irina ...)

The engine built-in 'en_female' needs no reference and is always available.
Replace any reference.wav with a real 7-20 s recording for better quality.

    python -m scripts.make_voices [--force]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import VOICES_DIR  # noqa: E402

_RU = ("Это образец записи для озвучивания. Голос должен звучать спокойно, чётко и "
       "естественно, с ровным темпом. Хорошая озвучка не отвлекает слушателя от смысла.")

# id -> (label, gender, languages, sapi_culture, pitch_factor, passage)
# Starter voice built from a Windows SAPI voice. Replace voices/ru_female/reference.wav
# with a real 20-40s recording (via the 🎙 button) for real quality.
_TARGETS = {
    "ru_female": ("Русский — Женский", "female", ["ru"], "ru", 1.0, _RU),
}

_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -like '{culture}*' }} | Select-Object -First 1
if (-not $v) {{ Write-Error 'no SAPI voice for {culture}'; exit 3 }}
$s.SelectVoice($v.VoiceInfo.Name); $s.Rate = -1
$s.SetOutputToWaveFile('{out}')
$s.Speak(@'
{text}
'@)
$s.Dispose(); Write-Output $v.VoiceInfo.Name
"""


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil

        exe = shutil.which("ffmpeg")
        if not exe:
            raise SystemExit("FFmpeg not found. pip install imageio-ffmpeg")
        return exe


def _sapi(culture: str, text: str, out: Path) -> str | None:
    script = _PS.format(culture=culture, out=str(out).replace("\\", "/"), text=text)
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not out.exists():
        print(f"   SAPI({culture}) unavailable: {p.stderr.strip()[:140]}")
        return None
    return (p.stdout.strip() or "SAPI").splitlines()[-1]


def _pitch(src: Path, dst: Path, factor: float, ff: str) -> None:
    if abs(factor - 1.0) < 1e-3:
        dst.write_bytes(src.read_bytes())
        return
    with wave.open(str(src), "rb") as w:
        sr = w.getframerate()
    af = f"asetrate={int(sr * factor)},aresample={sr},atempo={1.0 / factor:.6f}"
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-af", af, "-ac", "1", "-ar", str(sr), str(dst)], check=True)


def _finalize(src: Path, dst: Path, ff: str) -> None:
    af = ("silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB:detection=peak,"
          "areverse,"
          "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB:detection=peak,"
          "areverse,loudnorm=I=-18:TP=-2:LRA=11")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-af", af, "-ac", "1", "-ar", "24000", "-t", "20", str(dst)], check=True)


def build(force: bool = False) -> int:
    ff = _ffmpeg()
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    made = kept = 0
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for vid, (label, gender, langs, culture, pitch, passage) in _TARGETS.items():
            folder = VOICES_DIR / vid
            ref = folder / "reference.wav"
            if ref.exists() and not force:
                print(f" = {vid}: kept")
                kept += 1
                continue
            print(f" + {vid}: building ...")
            raw = tdp / f"{vid}.wav"
            src = _sapi(culture, passage, raw)
            if src is None:
                print(f"   -> skipped {vid}: no SAPI voice. Add voices/{vid}/reference.wav yourself.")
                continue
            shifted = tdp / f"{vid}_s.wav"
            _pitch(raw, shifted, pitch, ff)
            folder.mkdir(parents=True, exist_ok=True)
            _finalize(shifted, ref, ff)
            (folder / "voice.json").write_text(json.dumps({
                "id": vid, "label": label, "gender": gender, "languages": langs,
                "reference": "reference.wav",
                "reference_text": passage,      # transcript — needed by the Qwen engine
                "engines": [],                  # [] = usable by any engine
                "_source": f"Windows SAPI '{src}' (pitch x{pitch})",
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            made += 1
            print(f"   -> voices/{vid}/reference.wav (from {src})")
    print(f"\nVoices: {made} built, {kept} kept. Built-in 'en_female' always available.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    return build(ap.parse_args(argv).force)


if __name__ == "__main__":
    raise SystemExit(main())
