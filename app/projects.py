"""Local project system.

projects/<slug>/
    project.json      metadata + current settings + generation index
    script.txt        the narration script (source of truth for the editor)
    generations/<timestamp>/
        raw.wav       unprocessed concatenation
        final.wav     post-processed
        final.mp3
        meta.json     settings + prosody plan + timing for this run
    final/            user-promoted "keeper" exports
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import PROJECTS_DIR

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so a concurrent read/write (two browser tabs,
    a crash) can never leave a half-written or truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s[:60] or "project"


def timestamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


@dataclass
class ProjectSettings:
    voice: str = "ru_female"
    preset: str = "обычный"
    speed: float = 1.0
    n_variants: int = 1
    mp3_bitrate: str = "192k"
    post_enabled: bool = True


@dataclass
class Project:
    slug: str
    name: str
    created: str
    updated: str
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    generations: list[dict] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return PROJECTS_DIR / self.slug

    @property
    def script_path(self) -> Path:
        return self.dir / "script.txt"

    def read_script(self) -> str:
        return self.script_path.read_text(encoding="utf-8") if self.script_path.exists() else ""

    def write_script(self, text: str) -> None:
        _atomic_write(self.script_path, text or "")
        self.touch()

    def touch(self) -> None:
        self.updated = _dt.datetime.now().isoformat(timespec="microseconds")
        self.save()

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "generations").mkdir(exist_ok=True)
        (self.dir / "final").mkdir(exist_ok=True)
        data = {
            "slug": self.slug, "name": self.name,
            "created": self.created, "updated": self.updated,
            "settings": asdict(self.settings), "generations": self.generations,
        }
        _atomic_write(self.dir / "project.json",
                      json.dumps(data, indent=2, ensure_ascii=False))

    def to_dict(self) -> dict:
        return {
            "slug": self.slug, "name": self.name,
            "created": self.created, "updated": self.updated,
            "settings": asdict(self.settings),
            "generations": self.generations,
            "script_chars": len(self.read_script()),
        }

    # -- generation history ------------------------------------------------
    def new_generation_dir(self) -> tuple[str, Path]:
        gid = timestamp()
        d = self.dir / "generations" / gid
        n = 2
        while d.exists():
            d = self.dir / "generations" / f"{gid}_{n}"
            n += 1
        d.mkdir(parents=True)
        return d.name, d

    def record_generation(self, gid: str, meta: dict) -> None:
        entry = {"id": gid, "at": _dt.datetime.now().isoformat(timespec="microseconds"), **meta}
        self.generations.insert(0, entry)
        self.generations = self.generations[:100]
        self.touch()

    def promote_to_final(self, gid: str) -> Path:
        src = self.dir / "generations" / gid
        if not src.is_dir():
            raise FileNotFoundError(gid)
        for name in ("final.mp3", "final.wav"):
            f = src / name
            if f.exists():
                dst = self.dir / "final" / f"{gid}_{name}"
                shutil.copyfile(f, dst)
        return self.dir / "final"


def _load(slug: str) -> Project:
    p = PROJECTS_DIR / slug / "project.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    known = set(asdict(ProjectSettings()))
    raw_settings = {k: v for k, v in d.get("settings", {}).items() if k in known}
    return Project(
        slug=d["slug"], name=d["name"], created=d["created"], updated=d["updated"],
        settings=ProjectSettings(**{**asdict(ProjectSettings()), **raw_settings}),
        generations=d.get("generations", []),
    )


def list_projects() -> list[dict]:
    out = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if (d / "project.json").exists():
            try:
                out.append(_load(d.name).to_dict())
            except Exception:
                continue
    return sorted(out, key=lambda x: x["updated"], reverse=True)


def get_project(slug: str) -> Project:
    if not (PROJECTS_DIR / slug / "project.json").exists():
        raise KeyError(f"No project '{slug}'")
    return _load(slug)


def create_project(name: str, settings: dict[str, Any] | None = None, script: str = "",
                   slug: str | None = None) -> Project:
    if slug is None:
        slug = slugify(name)
        base = slug
        i = 2
        while (PROJECTS_DIR / slug).exists():
            slug = f"{base}-{i}"
            i += 1
    now = _dt.datetime.now().isoformat(timespec="microseconds")
    known = set(asdict(ProjectSettings()))
    st = ProjectSettings(**{**asdict(ProjectSettings()),
                            **{k: v for k, v in (settings or {}).items() if k in known}})
    proj = Project(slug=slug, name=name.strip() or slug, created=now, updated=now, settings=st)
    proj.save()
    proj.write_script(script)
    return proj


def get_or_create(slug_or_name: str, settings: dict | None = None, script: str = "") -> Project:
    try:
        return get_project(slugify(slug_or_name))
    except KeyError:
        return create_project(slug_or_name, settings, script)


def delete_project(slug: str) -> None:
    d = PROJECTS_DIR / slug
    if d.is_dir():
        shutil.rmtree(d)
