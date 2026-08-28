r"""Register a voice from your own recording (CLI).

You can also do this in the studio: click the 🎙 button next to the Voice
selector. This script is the headless equivalent.

Cleans the clip (mono, 24 kHz, trims long silence, loudness-normalises) and
writes voices/<id>/{reference.wav, voice.json}. `--text` (the transcript) is
required for the Qwen engine.

Run from the PROJECT ROOT (C:\Users\...\LocalTTS), not from runtime\Scripts\:

    runtime\Scripts\python.exe -m scripts.add_voice ru_my "C:\path\to\clip.wav" ^
        --label "Russian - My Voice" --gender female --lang ru ^
        --text "exact words spoken in the recording"

    runtime\Scripts\python.exe -m scripts.add_voice my clip.mp3 --text-file transcript.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import voices as voices_mod  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("voice_id")
    ap.add_argument("audio")
    ap.add_argument("--label", default=None)
    ap.add_argument("--gender", default="unknown", choices=["male", "female", "unknown"])
    ap.add_argument("--lang", action="append", default=[], help="restrict to language code(s); repeatable")
    ap.add_argument("--engines", default="", help="comma list to restrict engines (default: any)")
    ap.add_argument("--text", default=None, help="transcript of the clip (required for Qwen)")
    ap.add_argument("--text-file", default=None)
    ap.add_argument("--max-seconds", type=int, default=30)
    ap.add_argument("--no-clean", action="store_true", help="use the file as-is (already 24k mono)")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.audio)
    if not src.exists():
        print(f"[add_voice] file not found: {src}\n"
              f"            (tip: run this from the project root, and use the real path to your recording)",
              file=sys.stderr)
        return 1
    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()

    try:
        res = voices_mod.add_voice(
            args.voice_id, args.label or args.voice_id, args.gender, src,
            reference_text=text, languages=args.lang,
            engines=[e.strip() for e in args.engines.split(",") if e.strip()],
            clean=not args.no_clean, max_seconds=args.max_seconds, overwrite=args.overwrite,
        )
    except voices_mod.VoiceExists as e:
        print(f"[add_voice] {e}  (pass --overwrite to replace)", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[add_voice] failed: {e}", file=sys.stderr)
        return 1

    print(f"[add_voice] voices/{res['voice']['id']}/reference.wav  ({res['duration']}s, 24 kHz mono)")
    for w in res["warnings"]:
        print(f"[add_voice] note: {w}")
    print("[add_voice] Restart run.bat (or click 🎙 in the studio) to use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
