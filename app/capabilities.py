"""Model capability registry (config/model_capabilities.json).

Tells the app which engines exist, what features each has, and whether its weights
are actually present on disk. Never triggers a download.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .config import CAPABILITIES_PATH, MODELS_DIR


class Feature:
    VOICES_BUILTIN = "voices_builtin"
    VOICE_CLONING = "voice_cloning"
    LANGUAGES = "languages"
    NATIVE_EMOTION_TAGS = "native_emotion_tags"
    NONVERBAL_SOUNDS = "nonverbal_sounds"
    EXAGGERATION = "exaggeration"
    CFG_WEIGHT = "cfg_weight"
    TEMPERATURE = "temperature"
    SPEED_POSTPROCESS = "speed_postprocess"
    PAUSE_INSERTION = "pause_insertion"


@lru_cache(maxsize=1)
def registry() -> dict:
    if CAPABILITIES_PATH.exists():
        return json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    return {"engines": {}, "features": {}}


def engine_ids() -> list[str]:
    return sorted(registry().get("engines", {}))


def engine_caps(engine: str) -> dict:
    caps = registry().get("engines", {}).get(engine)
    if caps is None:
        raise KeyError(f"Engine '{engine}' is not in model_capabilities.json")
    return caps


def supports(engine: str, feature: str) -> bool:
    return feature in engine_caps(engine).get("features", [])


def model_dir(engine: str):
    md = engine_caps(engine).get("model_dir")
    return (MODELS_DIR / md) if md else None


def is_installed(engine: str) -> tuple[bool, str]:
    """(installed?, human message). Checks required weight files exist locally."""
    caps = engine_caps(engine)
    required = caps.get("required_files", [])
    md = model_dir(engine)

    pkg = caps.get("python_package")
    if pkg:
        import importlib.util

        try:
            found = importlib.util.find_spec(pkg) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            return False, f"Python package '{pkg}' is not installed. Run setup.bat --with-{engine}."

    if not required:
        return True, "OK"
    if md is None or not md.is_dir():
        return False, f"Model is not installed locally. Expected weights in models/{caps.get('model_dir')}/."
    missing = [f for f in required if not (md / f).exists()]
    if missing:
        return False, (
            f"Model is not installed locally. Missing from models/{caps.get('model_dir')}/: "
            + ", ".join(missing)
            + ".  Run setup.bat (with internet) once to download it."
        )
    return True, "OK"


def describe_all() -> list[dict]:
    out = []
    for eid in engine_ids():
        caps = engine_caps(eid)
        installed, msg = is_installed(eid)
        out.append({
            "id": eid,
            "label": caps.get("label", eid),
            "installed": installed,
            "message": msg,
            "commercial_ok": caps.get("commercial_ok", None),
            "weights_license": caps.get("weights_license"),
            "languages": caps.get("languages", []),
            "primary_languages": caps.get("primary_languages", []),
            "features": caps.get("features", []),
            "not_supported": caps.get("not_supported", []),
            "hardware": caps.get("hardware", {}),
            "approx_size_gb": caps.get("download", {}).get("approx_size_gb"),
        })
    return out
