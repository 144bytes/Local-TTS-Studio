"""Offline enforcement.

When ``offline_mode`` is on:

* Hugging Face / Transformers are told to never touch the network.
* Outbound socket connections raise, so any accidental network call fails loudly
  instead of silently phoning home. Loopback (127.0.0.1 / ::1) stays allowed so
  the browser can reach the local server.

Call :func:`apply` once, as early as possible, before importing torch/transformers.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket

log = logging.getLogger("localtts.netguard")

_ENGAGED = False
_orig_getaddrinfo = socket.getaddrinfo
_orig_create_connection = socket.create_connection
_orig_socket_connect = socket.socket.connect


def _is_local(host: str) -> bool:
    if host in ("localhost", "", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OfflineViolation(OSError):
    pass


def _guarded_connect(self, address):  # socket.socket.connect
    try:
        host = address[0]
    except Exception:
        host = ""
    if _is_local(str(host)):
        return _orig_socket_connect(self, address)
    raise OfflineViolation(
        f"offline_mode is on — blocked network connection to {host!r}. "
        f"Set \"offline_mode\": false in config/config.json only for initial setup."
    )


def _guarded_getaddrinfo(host, *args, **kwargs):
    if _is_local(str(host)):
        return _orig_getaddrinfo(host, *args, **kwargs)
    # allow resolution but connection will still be blocked; keeps libs from crashing early
    return _orig_getaddrinfo(host, *args, **kwargs)


def set_hf_offline_env() -> None:
    from .config import HF_CACHE_DIR

    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def apply(offline: bool) -> bool:
    """Engage (or not) the offline guard. Returns True if engaged."""
    global _ENGAGED
    if not offline:
        set_hf_offline_env()  # harmless; keeps cache local even when 'online'
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)
        log.info("offline_mode = false (network allowed; for setup only)")
        return False
    if _ENGAGED:
        return True
    set_hf_offline_env()
    socket.socket.connect = _guarded_connect
    socket.getaddrinfo = _guarded_getaddrinfo
    _ENGAGED = True
    log.info("offline_mode = true (non-loopback network blocked, HF offline)")
    return True


def release() -> None:
    global _ENGAGED
    socket.socket.connect = _orig_socket_connect
    socket.getaddrinfo = _orig_getaddrinfo
    _ENGAGED = False
