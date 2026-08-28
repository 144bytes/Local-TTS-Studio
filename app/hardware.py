"""Hardware probe: CPU / RAM / GPU / VRAM / CUDA / disk, plus advisory warnings."""
from __future__ import annotations

import os
import platform
import shutil
from functools import lru_cache

from .config import APP_ROOT


@lru_cache(maxsize=1)
def probe() -> dict:
    info: dict = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor() or "unknown",
        "cpu_count": os.cpu_count() or 0,
        "ram_gb": None,
        "gpu": None,
        "vram_total_gb": None,
        "vram_free_gb": None,
        "cuda": False,
        "cuda_version": None,
        "torch": None,
        "disk_free_gb": round(shutil.disk_usage(APP_ROOT).free / 1e9, 1),
        "warnings": [],
    }

    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:  # unix
            info["ram_gb"] = round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9, 1)
        else:  # windows
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            ms = _MS()
            ms.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            info["ram_gb"] = round(ms.ullTotalPhys / 1e9, 1)
    except Exception:
        pass

    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda_version"] = torch.version.cuda
        if torch.cuda.is_available():
            info["cuda"] = True
            info["gpu"] = torch.cuda.get_device_name(0)
            try:
                free, total = torch.cuda.mem_get_info()
                info["vram_total_gb"] = round(total / 1e9, 1)
                info["vram_free_gb"] = round(free / 1e9, 1)
            except Exception:
                props = torch.cuda.get_device_properties(0)
                info["vram_total_gb"] = round(props.total_memory / 1e9, 1)
    except Exception as e:
        info["warnings"].append(f"PyTorch not usable: {e}")

    _advise(info)
    return info


def _advise(info: dict) -> None:
    w = info["warnings"]
    if not info["cuda"]:
        w.append("No CUDA GPU detected — generation will run on CPU (roughly 3–5x slower than real time).")
    elif info["vram_total_gb"] and info["vram_total_gb"] < 4:
        w.append(f"GPU has only {info['vram_total_gb']} GB VRAM — keep chunk size small or use CPU if you hit out-of-memory.")
    if info["disk_free_gb"] < 5:
        w.append(f"Only {info['disk_free_gb']} GB free on disk — model weights need ~3.5 GB.")
    parts = [p.lower() for p in APP_ROOT.parts]
    if "downloads" in parts:
        w.append("App is inside a Downloads folder — Windows Storage Sense may auto-delete it. "
                 "Move LocalTTS/ somewhere else, or turn off 'Delete files in my Downloads folder' in Storage settings.")


def summary_line() -> str:
    i = probe()
    gpu = f"{i['gpu']} ({i['vram_total_gb']} GB)" if i["cuda"] else "CPU only"
    return f"{i['cpu'] or 'CPU'} · {i['ram_gb']} GB RAM · {gpu} · CUDA {i['cuda_version'] or '-'}"
