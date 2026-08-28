import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect all writable dirs into a temp folder so tests never touch real data."""
    import shutil

    import app.config as c

    for name in ("PROJECTS_DIR", "OUTPUT_DIR", "LOGS_DIR", "VOICES_DIR"):
        d = tmp_path / name.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(c, name, d, raising=False)

    # isolate config.json so preset-save tests can't touch the real one
    cfg_copy = tmp_path / "config.json"
    if c.CONFIG_EXAMPLE_PATH.exists():
        shutil.copyfile(c.CONFIG_EXAMPLE_PATH, cfg_copy)
    monkeypatch.setattr(c, "CONFIG_PATH", cfg_copy, raising=False)
    import app.presets as _pmod
    monkeypatch.setattr(_pmod, "CONFIG_PATH", cfg_copy, raising=False)

    import app.projects as pr
    monkeypatch.setattr(pr, "PROJECTS_DIR", tmp_path / "projects_dir", raising=False)
    (tmp_path / "projects_dir").mkdir(exist_ok=True)

    import app.pipeline as pl
    monkeypatch.setattr(pl, "OUTPUT_DIR", tmp_path / "output_dir", raising=False)
    (tmp_path / "output_dir").mkdir(exist_ok=True)

    import app.voices as v
    monkeypatch.setattr(v, "VOICES_DIR", tmp_path / "voices_dir", raising=False)
    (tmp_path / "voices_dir").mkdir(exist_ok=True)

    import app.analyze  # noqa: F401  (ensure importable)
    yield
