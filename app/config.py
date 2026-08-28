"""Configuration and path handling.

Every path derives from APP_ROOT (the folder that contains app/), so the whole
LocalTTS/ folder can be copied to another PC without editing anything.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

APP_ROOT: Path = Path(__file__).resolve().parent.parent

CONFIG_DIR = APP_ROOT / "config"
MODELS_DIR = APP_ROOT / "models"
HF_CACHE_DIR = MODELS_DIR / "hf_cache"
VOICES_DIR = APP_ROOT / "voices"
PROJECTS_DIR = APP_ROOT / "projects"
OUTPUT_DIR = APP_ROOT / "output"
LOGS_DIR = APP_ROOT / "logs"
WEB_DIR = APP_ROOT / "web"
RUNTIME_DIR = APP_ROOT / "runtime"

CONFIG_PATH = CONFIG_DIR / "config.json"
CONFIG_EXAMPLE_PATH = CONFIG_DIR / "config.example.json"
CAPABILITIES_PATH = CONFIG_DIR / "model_capabilities.json"

for _d in (CONFIG_DIR, MODELS_DIR, VOICES_DIR, PROJECTS_DIR, OUTPUT_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _preset(label, order, hint, gen, prosody=None, audio=None):
    return {"label": label, "order": order, "hint": hint,
            "generation": gen, "prosody": prosody or {}, "audio": audio or {}}


DEFAULTS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8765, "open_browser": True},
    "offline_mode": True,
    "engine": "qwen",
    "device": "auto",
    "language": "ru",
    "default_voice": "ru_female",
    "default_preset": "обычный",
    "default_variants": 1,
    "estimate": {"words_per_minute": 135},
    "chunking": {"target_chars": 700, "max_chars": 1100, "min_sentences_per_chunk": 1},
    "pauses": {"paragraph_ms": 520, "split_ms": 150, "jitter": 0.16, "min_ms": 0, "max_ms": 1200},
    "generation": {"temperature": 0.85, "top_p": 1.0, "repetition_penalty": 1.05},
    "presets": {
        "обычный": _preset("Обычный", 1, "живая разговорная подача",
                           {"temperature": 0.85, "top_p": 1.0, "repetition_penalty": 1.05},
                           {"pause_scale": 1.0, "jitter": 0.16}),
        "нормальный": _preset("Нормальный", 2, "ровный, максимально стабильный",
                              {"temperature": 0.7, "top_p": 0.92, "repetition_penalty": 1.1},
                              {"pause_scale": 0.95, "jitter": 0.08}),
        "радостный": _preset("Радостный", 3, "энергично, с улыбкой",
                             {"temperature": 0.95, "top_p": 1.0, "repetition_penalty": 1.03},
                             {"pause_scale": 0.82, "jitter": 0.22}, {"gain_db": 0.5}),
        "грустный": _preset("Грустный", 4, "тише, с более длинными паузами",
                            {"temperature": 0.78, "top_p": 0.95, "repetition_penalty": 1.07},
                            {"pause_scale": 1.45, "jitter": 0.25, "trail_ms": 180}, {"gain_db": -1.0}),
        "шёпот": _preset("Шёпот", 5, "тихо, доверительно",
                         {"temperature": 0.72, "top_p": 0.92, "repetition_penalty": 1.08},
                         {"pause_scale": 1.15, "jitter": 0.15},
                         {"gain_db": -8.0, "eq": {"high_pass_hz": 100, "presence_db": 1.5}}),
        "злой": _preset("Злой", 6, "резко, с нажимом, короткие паузы",
                        {"temperature": 0.88, "top_p": 1.0, "repetition_penalty": 1.04},
                        {"pause_scale": 0.72, "jitter": 0.18}, {"gain_db": 1.0}),
        "взволнованный": _preset("Взволнованный", 7, "на подъёме, живой контур",
                                 {"temperature": 1.0, "top_p": 1.0, "repetition_penalty": 1.02},
                                 {"pause_scale": 0.72, "jitter": 0.26}, {"gain_db": 0.3}),
        "саркастичный": _preset("Саркастичный", 8, "с растяжкой и подтекстом",
                                {"temperature": 0.9, "top_p": 1.0, "repetition_penalty": 1.06},
                                {"pause_scale": 1.3, "jitter": 0.2, "comma_dwell": True}),
        "флирт": _preset("Флирт", 9, "мягко, тепло, без деклараций",
                         {"temperature": 0.83, "top_p": 0.97, "repetition_penalty": 1.06},
                         {"pause_scale": 1.25, "jitter": 0.2, "soften_endings": True},
                         {"gain_db": -0.5, "eq": {"low_shelf_db": 1.0}}),
        "документальный": _preset("Документальный", 10, "голос за кадром, уверенно",
                                  {"temperature": 0.74, "top_p": 0.94, "repetition_penalty": 1.08},
                                  {"pause_scale": 1.08, "jitter": 0.1},
                                  {"eq": {"low_shelf_db": 1.5, "presence_db": 1.0}, "compress": True}),
    },
    "stress": {"marker": "+", "enabled": True,
               "dictionary": {"комп": "к+омп", "проц": "пр+оц", "смарт": "см+арт"}},
    "normalizer": {"enabled": True, "numbers": True, "dates": True,
                   "spell_unknown_acronyms": True, "lexicon": {}, "acronyms": {}},
    "audio": {
        "sample_rate": 24000,
        "mp3_bitrate": "192k",
        "mp3_bitrate_choices": ["128k", "160k", "192k", "256k"],
        "keep_raw": True,
        "global_speed_via_stretch": True,
        "post": {"enabled": True, "loudness_normalize": True, "target_lufs": -16.0,
                 "true_peak_db": -1.5, "limiter": True, "limiter_ceiling_db": -1.0, "denoise": False},
    },
    "calibration": {"seed": 12345},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self, data: dict[str, Any]):
        self.data = data

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def port(self) -> int:
        return int(self.data["server"]["port"])

    @property
    def host(self) -> str:
        return str(self.data["server"]["host"])

    @property
    def offline(self) -> bool:
        return bool(self.data.get("offline_mode", True))

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def load_config() -> Config:
    user: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    elif CONFIG_EXAMPLE_PATH.exists():
        user = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
    merged = _deep_merge(DEFAULTS, user)
    # 'presets' is fully user-authored once written — don't merge stale built-ins back in
    if "presets" in user:
        merged["presets"] = user["presets"]

    if os.environ.get("LOCALTTS_PORT"):
        merged["server"]["port"] = int(os.environ["LOCALTTS_PORT"])
    if os.environ.get("LOCALTTS_DEVICE"):
        merged["device"] = os.environ["LOCALTTS_DEVICE"]
    if os.environ.get("LOCALTTS_OFFLINE") in ("0", "false", "False"):
        merged["offline_mode"] = False
    merged["engine"] = "qwen"  # single engine — not user-switchable
    return Config(merged)


def ensure_config_file() -> Path:
    if not CONFIG_PATH.exists() and CONFIG_EXAMPLE_PATH.exists():
        CONFIG_PATH.write_text(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return CONFIG_PATH


def in_downloads_folder() -> bool:
    return "downloads" in [p.lower() for p in APP_ROOT.parts]
