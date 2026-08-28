"""Environment report, offline self-test, and demo-project seeding.

    python -m scripts.check_env                    # hardware + deps report
    python -m scripts.check_env --offline-selftest # block network, synth EN+RU, verify
    python -m scripts.check_env --make-demo-project
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config as cfg  # noqa: E402


def report() -> None:
    from app import hardware

    hw = hardware.probe()
    print("=" * 60)
    print("LocalTTS Studio — environment")
    print("=" * 60)
    print(f"App root   : {cfg.APP_ROOT}")
    print(f"OS         : {hw['os']}")
    print(f"Python     : {hw['python']}")
    print(f"CPU        : {hw['cpu']}  ({hw['cpu_count']} threads)")
    print(f"RAM        : {hw['ram_gb']} GB")
    print(f"GPU        : {hw['gpu'] or 'none'}")
    print(f"VRAM       : {hw['vram_total_gb']} GB total / {hw['vram_free_gb']} GB free")
    print(f"CUDA       : {hw['cuda']}  (torch {hw['torch']}, cuda {hw['cuda_version']})")
    print(f"Disk free  : {hw['disk_free_gb']} GB")
    try:
        from app.audio import ffmpeg_exe

        print(f"FFmpeg     : {ffmpeg_exe() or 'NOT FOUND'}")
    except Exception as e:
        print(f"FFmpeg     : check failed ({e})")
    conf = cfg.load_config()
    print(f"engine     : {conf['engine']}   offline_mode: {conf.offline}")
    from app import capabilities

    for e in capabilities.describe_all():
        print(f"  - {e['id']:<11} installed={e['installed']}  {'' if e['installed'] else '(' + e['message'] + ')'}")
    for warn in hw["warnings"]:
        print(f"  ! {warn}")
    print("=" * 60)


class _NoNetwork:
    def __enter__(self):
        self._c = socket.socket.connect
        self._g = socket.getaddrinfo

        def blocked_connect(s, addr, *a, **k):
            host = addr[0] if isinstance(addr, (tuple, list)) else addr
            if str(host) in ("127.0.0.1", "::1", "localhost"):
                return self._c(s, addr, *a, **k)
            raise OSError(f"offline self-test: blocked connect to {host}")

        socket.socket.connect = blocked_connect
        socket.getaddrinfo = lambda *a, **k: self._g(*a, **k)
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._c
        socket.getaddrinfo = self._g


def offline_selftest(conf=None) -> bool:
    conf = conf or cfg.load_config()
    from app import netguard

    netguard.apply(True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    print("[selftest] offline env set; blocking ALL non-loopback network ...")

    from app.pipeline import GenerationRequest, Pipeline
    from app.tts import create_provider
    from app.voices import VoiceRegistry

    ok = True
    with _NoNetwork():
        provider = create_provider("qwen", conf["device"])
        provider.initialize()
        voices = VoiceRegistry(provider)
        pipe = Pipeline(provider, voices, conf.data)

        vids = [v for v in voices.ids()]
        if not vids:
            print("[selftest] нет голосов — сначала запустите scripts.make_voices")
            return False
        vid = conf.get("default_voice") if conf.get("default_voice") in vids else vids[0]
        for preset, text, nv in [
            ("обычный", "Сегодня разберёмся, почему процессоры устроены именно так.", 1),
            ("грустный", "Это был обычный день.\n\nНо потом всё изменилось.", 2),
        ]:
            res = pipe.generate(GenerationRequest(text=text, voice_id=vid, preset=preset,
                                                  language="ru", n_variants=nv,
                                                  filename_hint=f"selftest_{preset}"))
            done = [v for v in res.variants if v.error is None]
            good = bool(done) and all(v.duration_sec > 0.5 and (v.final_mp3 or v.final_wav) for v in done)
            ok = ok and good
            names = ", ".join((v.final_mp3 or v.final_wav or "—") for v in done)
            print(f"[selftest] {preset}: {len(done)}/{len(res.variants)} вар., "
                  f"{res.realtime_factor}x RT -> {names}  {'OK' if good else 'FAIL'}")
        provider.shutdown()
    print(f"[selftest] {'PASS — использовались только локальные файлы.' if ok else 'FAIL'}")
    return ok


def make_demo_project() -> None:
    from app.projects import create_project, get_project

    script = ("Добро пожаловать в LocalTTS Studio.\n\n"
              "Это полностью локальная озвучка. Всё работает на вашем компьютере — "
              "без облака, ключей и интернета.\n\n"
              "Вставьте текст, выберите голос и пресет, нажмите «Озвучить».")
    try:
        get_project("workspace")
        print("[demo] рабочая область уже есть")
    except KeyError:
        p = create_project("Рабочая область", {"voice": "ru_female", "preset": "обычный"},
                           script, slug="workspace")
        print(f"[demo] рабочая область создана: projects/{p.slug}/")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-selftest", action="store_true")
    ap.add_argument("--make-demo-project", action="store_true")
    args = ap.parse_args(argv)
    if args.make_demo_project:
        make_demo_project()
        return 0
    if args.offline_selftest:
        return 0 if offline_selftest() else 1
    report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
