"""Offline calibration harness for Qwen3-TTS 1.7B (Russian).

Grid-searches the Qwen *Base API* sampling parameters (temperature, top_p,
repetition_penalty) over a fixed set of benchmark texts, renders every
combination with a DETERMINISTIC seed, and writes the audio plus a
results.json / README.md into calibration/<timestamp>/.

    python -m scripts.calibrate_tts --voice asii            # coarse grid
    python -m scripts.calibrate_tts --voice asii --grid fine
    python -m scripts.calibrate_tts --dry-run               # plan only, no model

It NEVER touches config/config.json. "Сначала измерить -> затем изменить ->
затем сравнить": listen to the renders, then copy the winning numbers into a
preset's "generation" block (or "generation" defaults) by hand.

Everything runs locally: the model is loaded with local_files_only=True and all
non-loopback sockets are blocked for the duration of the run.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import itertools
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config as cfg  # noqa: E402

CALIBRATION_DIR = cfg.APP_ROOT / "calibration"

# --- benchmark texts: one hard case per delivery dimension -------------------
BENCHMARKS: dict[str, str] = {
    "technical": (
        "Бэкенд написан на C++ и Python, фронтенд — на JavaScript и React. "
        "Данные отдаёт REST API поверх HTTP, формат — JSON."
    ),
    "numbers": (
        "Сегодня двадцать восьмое августа две тысячи двадцать шестого года. "
        "Температура составляет 23 градуса, встреча назначена на 15:30, "
        "бюджет — 1 250 000 рублей, рост — 7,5 процента."
    ),
    "question": (
        "А вы когда-нибудь задумывались, почему небо голубое? "
        "Что произойдёт, если Солнце внезапно погаснет?"
    ),
    "exclamation": (
        "Это невероятно! Мы сделали это! Спасибо всем, кто был рядом весь этот год!"
    ),
    "conversational": (
        "Ну, короче, я сидел дома, никого не трогал, и тут звонок. "
        "Думаю: опять реклама. А это оказался старый друг."
    ),
    "emotional": (
        "Это был последний раз, когда я видел этот дом. "
        "Мы уезжали навсегда, и никто не сказал ни слова."
    ),
    "long": (
        "История процессоров — это история компромиссов между скоростью, "
        "энергопотреблением и стоимостью производства.\n\n"
        "В шестидесятых годах транзисторы были размером с ноготь, и целый "
        "компьютер занимал комнату. Сегодня в одном кристалле их десятки "
        "миллиардов.\n\n"
        "Каждое новое поколение упирается в физику: чем меньше транзистор, "
        "тем сложнее удержать в нём электроны и тем больше тепла он выделяет."
    ),
}

GRIDS: dict[str, dict[str, list[float]]] = {
    "coarse": {
        "temperature": [0.70, 0.85, 1.00],
        "top_p": [0.90, 1.00],
        "repetition_penalty": [1.03, 1.10],
    },
    "fine": {
        "temperature": [0.70, 0.80, 0.90, 1.00, 1.10],
        "top_p": [0.85, 0.90, 0.95, 1.00],
        "repetition_penalty": [1.00, 1.05, 1.10, 1.15],
    },
    "quick": {
        "temperature": [0.80, 0.95],
        "top_p": [1.00],
        "repetition_penalty": [1.05],
    },
}


class _NoNetwork:
    """Block every non-loopback connect for the duration of the run."""

    def __enter__(self):
        self._c = socket.socket.connect

        def guarded(s, addr, *a, **k):
            host = addr[0] if isinstance(addr, (tuple, list)) else addr
            if str(host) in ("127.0.0.1", "::1", "localhost"):
                return self._c(s, addr, *a, **k)
            raise OSError(f"calibrate_tts is offline: blocked connect to {host}")

        socket.socket.connect = guarded
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._c


def _combos(grid: dict[str, list[float]]) -> list[dict[str, float]]:
    keys = ("temperature", "top_p", "repetition_penalty")
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def _tag(c: dict[str, float]) -> str:
    return f"t{c['temperature']:.2f}_p{c['top_p']:.2f}_r{c['repetition_penalty']:.2f}".replace(".", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="calibrate_tts", description=__doc__)
    ap.add_argument("--voice", help="voice id (default: config default_voice or first available)")
    ap.add_argument("--preset", default="обычный", help="preset for chunking/prosody (delivery params are overridden by the grid)")
    ap.add_argument("--grid", choices=sorted(GRIDS), default="coarse")
    ap.add_argument("--texts", help="comma-separated benchmark names (default: all)")
    ap.add_argument("--seed", type=int, help="override calibration seed (default: config calibration.seed)")
    ap.add_argument("--device", help="cpu / cuda / auto (default: config)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit — no model, no audio")
    args = ap.parse_args(argv)

    conf = cfg.load_config()
    if args.device:
        conf.data["device"] = args.device
    seed = args.seed if args.seed is not None else int(conf.get("calibration", {}).get("seed", 12345))
    grid = GRIDS[args.grid]
    combos = _combos(grid)

    names = [t.strip() for t in args.texts.split(",")] if args.texts else list(BENCHMARKS)
    bad = [n for n in names if n not in BENCHMARKS]
    if bad:
        ap.error(f"unknown benchmark(s): {', '.join(bad)}. choices: {', '.join(BENCHMARKS)}")
    texts = {n: BENCHMARKS[n] for n in names}

    total = len(texts) * len(combos)
    print(f"grid={args.grid}  combos={len(combos)}  texts={len(texts)}  renders={total}  seed={seed}")
    for c in combos:
        print(f"  - temperature={c['temperature']:.2f}  top_p={c['top_p']:.2f}  repetition_penalty={c['repetition_penalty']:.2f}")
    if args.dry_run:
        print("\n--dry-run: nothing rendered.")
        return 0

    from app import audio, netguard
    from app.prosody import TextPrep
    from app.tts import create_provider
    from app.voices import VoiceRegistry

    netguard.apply(True)

    with _NoNetwork():
        provider = create_provider("qwen", conf["device"])
        provider.initialize()
        voices = VoiceRegistry(provider)
        vids = voices.ids()
        if not vids:
            print("нет голосов — добавьте голос через студию или scripts.make_voices")
            return 1
        vid = args.voice or (conf.get("default_voice") if conf.get("default_voice") in vids else vids[0])
        if vid not in vids:
            print(f"голос '{vid}' не найден. доступны: {', '.join(vids)}")
            return 1
        voice = voices.get(vid)
        if not (voice.reference_wav and voice.reference_text):
            print(f"у голоса '{vid}' нет reference_wav + reference_text")
            return 1

        prep = TextPrep(conf.data)
        sr = provider.sample_rate
        stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        outdir = CALIBRATION_DIR / stamp
        outdir.mkdir(parents=True, exist_ok=True)

        rows: list[dict] = []
        i = 0
        t_start = time.time()
        for bname, text in texts.items():
            prepared = prep.prepare(text, args.preset, "ru", seed=seed)
            speech = [s for s in prepared.segments if s.is_speech]
            for combo in combos:
                i += 1
                tag = _tag(combo)
                wav_name = f"{bname}__{tag}.wav"
                print(f"[{i}/{total}] {bname}  {tag} ...", end=" ", flush=True)
                try:
                    parts = []
                    for si, seg in enumerate(prepared.segments):
                        if not seg.is_speech:
                            parts.append(audio.silence(seg.duration_ms, sr))
                            continue
                        style = seg.style
                        style.temperature = combo["temperature"]
                        style.top_p = combo["top_p"]
                        style.repetition_penalty = combo["repetition_penalty"]
                        parts.append(provider.generate(seg.text, voice, style, seed=seed + si + 1))
                    wav = audio.peak_normalize(audio.concat(parts), -1.0)
                    audio.write_wav(outdir / wav_name, wav, sr)
                    dur = round(len(wav) / sr, 2)
                    lufs = audio.measure_lufs(wav, sr)
                    rows.append({
                        "benchmark": bname, "wav": wav_name,
                        "temperature": combo["temperature"], "top_p": combo["top_p"],
                        "repetition_penalty": combo["repetition_penalty"],
                        "seed": seed, "chunks": len(speech),
                        "duration_sec": dur, "lufs": lufs,
                    })
                    print(f"{dur}s  LUFS={lufs}")
                except Exception as e:  # noqa: BLE001 — record and continue the grid
                    print(f"FAILED: {e}")
                    rows.append({"benchmark": bname, "wav": None, "error": str(e), **combo, "seed": seed})

        elapsed = round(time.time() - t_start, 1)
        provider.shutdown()

    manifest = {
        "generated_at": stamp, "voice": vid, "preset": args.preset,
        "grid": args.grid, "seed": seed, "elapsed_sec": elapsed,
        "note": "NOT applied to config/config.json. Listen, pick a winner, copy the "
                "temperature/top_p/repetition_penalty into a preset's 'generation' block by hand.",
        "benchmarks": texts,
        "results": rows,
    }
    (outdir / "results.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_readme(outdir, manifest)
    print(f"\nсохранено: {outdir}")
    print(f"  results.json  ({len(rows)} строк, {elapsed}s)")
    print("  README.md     (таблица для прослушивания)")
    print("\nНичего не применено к config.json — это ручное решение после прослушивания.")
    return 0


def _write_readme(outdir: Path, m: dict) -> None:
    ok = [r for r in m["results"] if r.get("wav")]
    lines = [
        f"# Калибровка Qwen3-TTS — {m['generated_at']}", "",
        f"- Голос: `{m['voice']}`  ·  Пресет (chunking/prosody): `{m['preset']}`",
        f"- Сетка: `{m['grid']}`  ·  Seed: `{m['seed']}` (детерминированный)",
        f"- Рендеров: {len(ok)} / {len(m['results'])}  ·  Время: {m['elapsed_sec']} с", "",
        "> **Не применено к `config/config.json`.** Прослушайте, выберите лучший вариант,",
        "> вручную перенесите `temperature` / `top_p` / `repetition_penalty` в блок",
        "> `generation` нужного пресета.", "",
        "| benchmark | temp | top_p | rep_pen | длит. | LUFS | файл |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in ok:
        lines.append(
            f"| {r['benchmark']} | {r['temperature']:.2f} | {r['top_p']:.2f} | "
            f"{r['repetition_penalty']:.2f} | {r['duration_sec']}s | {r['lufs']} | `{r['wav']}` |"
        )
    fails = [r for r in m["results"] if not r.get("wav")]
    if fails:
        lines += ["", "## Ошибки", ""]
        for r in fails:
            lines.append(f"- {r['benchmark']}  t{r.get('temperature')}/{r.get('top_p')}/"
                         f"{r.get('repetition_penalty')}: {r.get('error')}")
    lines += ["", "## Тексты", ""]
    for name, txt in m["benchmarks"].items():
        lines.append(f"**{name}** — {txt}".replace("\n", " ") + "\n")
    (outdir / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
