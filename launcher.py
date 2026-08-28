"""LocalTTS Studio launcher.

Double-click LocalTTS.exe (or run: run.bat):

  1. locate the Python runtime (runtime/)
  2. check hardware (nvidia-smi) and model files (models/<engine>/)
  3. start the local server:  runtime\\Scripts\\python.exe -m app.server
  4. wait for http://127.0.0.1:<port>/api/health
  5. open that URL in the default browser
  6. on Ctrl+C / window close: POST /api/shutdown, then kill the server tree
     (releases GPU memory)

This file is intentionally dependency-free (stdlib only) so it can be frozen
into a small EXE with PyInstaller. Model weights and heavy libraries are NOT
bundled — they live in runtime/ and models/.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_ROOT = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent

RUNTIME_PY = APP_ROOT / "runtime" / "Scripts" / "python.exe"
CONFIG = APP_ROOT / "config" / "config.json"
CONFIG_EXAMPLE = APP_ROOT / "config" / "config.example.json"
CAPS = APP_ROOT / "config" / "model_capabilities.json"


def _print(msg=""):
    print(msg, flush=True)


def load_config() -> dict:
    for p in (CONFIG, CONFIG_EXAMPLE):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def check_runtime() -> bool:
    if RUNTIME_PY.exists():
        return True
    _print("[X] Python runtime not found (runtime\\Scripts\\python.exe).")
    _print("    Run  setup.bat  once (needs internet) to create it.")
    return False


def check_hardware() -> None:
    _print("[*] Hardware:")
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            _print("      GPU: " + out.stdout.strip())
        else:
            _print("      GPU: none detected -> CPU mode (slower)")
    except Exception:
        _print("      GPU: nvidia-smi not available -> CPU mode (slower)")
    parts = [p.lower() for p in APP_ROOT.parts]
    if "downloads" in parts:
        _print("    ! This folder is under Downloads; Windows Storage Sense can delete it.")


def check_models(config: dict) -> None:
    engine = config.get("engine", "chatterbox")
    try:
        caps = json.loads(CAPS.read_text(encoding="utf-8"))["engines"][engine]
    except Exception:
        _print(f"[*] Model check skipped (no capability entry for '{engine}').")
        return
    md = caps.get("model_dir")
    required = caps.get("required_files", [])
    if not required:
        _print(f"[*] Engine '{engine}': no weight files required.")
        return
    folder = APP_ROOT / "models" / md
    missing = [f for f in required if not (folder / f).exists()]
    if missing:
        _print(f"[!] Engine '{engine}': model is NOT installed locally.")
        _print(f"    Missing in models\\{md}\\: {', '.join(missing)}")
        _print("    The UI will open but generation is disabled until you run setup.bat.")
    else:
        _print(f"[*] Engine '{engine}': model weights present in models\\{md}\\.")


def wait_for_health(url: str, timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as r:
                data = json.loads(r.read().decode())
                if data.get("ready"):
                    return True
                if data.get("error"):
                    _print(f"    engine: {data['error']}")
                    return True  # server is up; UI will show the error
        except Exception:
            pass
        time.sleep(1.0)
    return False


def post_shutdown(url: str) -> None:
    try:
        req = urllib.request.Request(url + "/api/shutdown", method="POST", data=b"{}")
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def main() -> int:
    _print("=" * 56)
    _print("  LocalTTS Studio")
    _print("=" * 56)
    if not check_runtime():
        input("\nPress Enter to close...")
        return 1

    config = load_config()
    port = int(config.get("server", {}).get("port", 8765))
    host = config.get("server", {}).get("host", "127.0.0.1")
    url = f"http://{host}:{port}"

    check_hardware()
    check_models(config)
    _print(f"[*] Starting server on {url} ...")

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [str(RUNTIME_PY), "-m", "app.server", "--no-browser"],
        cwd=str(APP_ROOT), env=env, creationflags=creationflags,
    )

    try:
        if wait_for_health(url):
            _print(f"[*] Opening {url}")
            webbrowser.open(url)
        else:
            _print("[X] Server did not become healthy in time. Check logs\\localtts.log")
        _print("\nLocalTTS Studio is running. Close this window or press Ctrl+C to stop.\n")
        while proc.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        _print("\n[*] Stopping ...")
    finally:
        post_shutdown(url)
        time.sleep(0.5)
        kill_tree(proc)
        _print("[*] Stopped. GPU memory released.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # keep the console open on a hard failure
        print(f"\nFATAL: {e}")
        try:
            input("Press Enter to close...")
        except Exception:
            pass
        sys.exit(1)
