"""Rotating file log in logs/ + concise console output."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LOGS_DIR

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger("localtts")
    if _CONFIGURED:
        return root
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False

    fh = RotatingFileHandler(LOGS_DIR / "localtts.log", maxBytes=2_000_000,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    ch.setLevel(logging.INFO)
    root.addHandler(ch)

    _CONFIGURED = True
    return root
