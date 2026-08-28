import socket

import pytest

from app import capabilities as caps
from app import netguard


def test_registry_has_qwen_and_mock():
    ids = caps.engine_ids()
    assert "qwen" in ids and "mock" in ids


def test_qwen_capabilities():
    d = {e["id"]: e for e in caps.describe_all()}["qwen"]
    assert d["commercial_ok"] is True and d["weights_license"] == "Apache-2.0"
    assert "ru" in d["languages"]
    assert caps.supports("qwen", "voice_cloning")
    assert caps.supports("qwen", "temperature")


def test_mock_is_installed():
    ok, _ = caps.is_installed("mock")
    assert ok


def test_qwen_installed_check_uses_find_spec_not_import(monkeypatch):
    # find_spec must not execute the package (no sox banner side effects)
    import importlib.util
    called = {"import": False}
    real = __import__

    def spy(name, *a, **k):
        if name == "qwen_tts":
            called["import"] = True
        return real(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", spy)
    caps.is_installed("qwen")
    assert called["import"] is False


def test_netguard_blocks_nonlocal_allows_loopback():
    try:
        netguard.apply(True)
        with pytest.raises(OSError):
            socket.socket().connect(("8.8.8.8", 53))
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", 59999))
        except netguard.OfflineViolation:
            pytest.fail("loopback blocked")
        except OSError:
            pass
        finally:
            s.close()
    finally:
        netguard.release()


def test_netguard_release_restores():
    netguard.apply(True)
    netguard.release()
    assert socket.socket.connect is netguard._orig_socket_connect
