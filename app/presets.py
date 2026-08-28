"""Presets: named delivery styles.

A preset = {label, order, hint, generation{temperature,top_p,repetition_penalty},
prosody{pause_scale,jitter,...}, audio{gain_db,eq,compress}}.
Built-ins live in config -> "presets"; fully user-editable there.
"""
from __future__ import annotations

import json

from .config import CONFIG_EXAMPLE_PATH, CONFIG_PATH, Config


def list_presets(config: Config) -> dict[str, dict]:
    presets = dict(config.get("presets", {}) or {})
    return dict(sorted(presets.items(), key=lambda kv: kv[1].get("order", 99)))


def preset_names(config: Config) -> list[str]:
    return list(list_presets(config).keys())


def get_preset(config: Config, name: str) -> dict:
    presets = list_presets(config)
    if name not in presets:
        raise KeyError(f"Нет пресета «{name}». Доступны: {', '.join(presets)}")
    return presets[name]


def _persist(config: Config) -> None:
    on_disk = {}
    src = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH
    if src.exists():
        try:
            on_disk = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            on_disk = {}
    on_disk["presets"] = config.data.get("presets", {})
    CONFIG_PATH.write_text(json.dumps(on_disk, indent=2, ensure_ascii=False), encoding="utf-8")


def save_preset(config: Config, name: str, values: dict) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("нужно имя пресета")
    cur = dict(config.data.get("presets", {}).get(name, {}))
    cur.setdefault("label", name.capitalize())
    cur.setdefault("order", 50)
    cur.setdefault("hint", "")
    if "hint" in values:
        cur["hint"] = str(values["hint"])
    for group in ("generation", "prosody", "audio"):
        incoming = values.get(group)
        if isinstance(incoming, dict):
            merged = dict(cur.get(group, {}))
            merged.update(incoming)
            cur[group] = merged
    config.data.setdefault("presets", {})[name] = cur
    _persist(config)
    return cur


def delete_preset(config: Config, name: str) -> None:
    if name in config.data.get("presets", {}):
        del config.data["presets"][name]
        _persist(config)
