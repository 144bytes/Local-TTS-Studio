"""One-time model download (run by setup.bat, WITH internet).

Pulls the Qwen3-TTS 1.7B weights into models/qwen-1.7b/. After this, normal
operation is fully offline.

    python -m scripts.download_models
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config as cfg  # noqa: E402


def _online_env() -> None:
    os.environ["HF_HOME"] = str(cfg.HF_CACHE_DIR)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cfg.HF_CACHE_DIR)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)


def download_qwen() -> None:
    from huggingface_hub import snapshot_download

    from app.tts.qwen_engine import LOCAL_DIR, REQUIRED

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    if all((LOCAL_DIR / f).exists() for f in REQUIRED):
        print(f"[download] Веса Qwen3-TTS уже на месте: {LOCAL_DIR} — пропускаю.")
    else:
        print(f"[download] Qwen3-TTS 1.7B -> {LOCAL_DIR} (~4.6 ГБ) ...")
        snapshot_download(repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-Base", local_dir=str(LOCAL_DIR),
                          ignore_patterns=["*.pth", "original/*", "*.gguf"])
    missing = [f for f in REQUIRED if not (LOCAL_DIR / f).exists()]
    if missing:
        raise RuntimeError(f"неполный чекпоинт, нет: {missing}")

    print("[download] проверка (загрузка модели) ...")
    import torch

    from app.quiet import suppress_stdio

    with suppress_stdio():
        from qwen_tts import Qwen3TTSModel

    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    dt = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    Qwen3TTSModel.from_pretrained(str(LOCAL_DIR), local_files_only=True,
                                  device_map=dev, dtype=dt, attn_implementation="sdpa")
    print("[download] Qwen3-TTS OK.")


def main(argv: list[str] | None = None) -> int:
    _online_env()
    cfg.HF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cfg.ensure_config_file()
    try:
        download_qwen()
    except Exception as e:
        print(f"[download] ОШИБКА: {e}", file=sys.stderr)
        print("[download] Проверьте интернет и запустите setup.bat заново.", file=sys.stderr)
        return 1
    print("[download] Готово. Можно отключать интернет и запускать run.bat / LocalTTS.bat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
