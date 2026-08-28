"""LocalTTS Studio server (Flask). Qwen3-TTS 1.7B, Russian, one warm model, local only."""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from . import capabilities as caps
from . import config as cfg
from . import hardware, netguard, presets
from . import voices as voices_mod
from .analyze import analyze_text
from .estimate import metrics as estimate_metrics
from .logging_setup import setup_logging
from .pipeline import GenerationRequest, Pipeline
from .projects import get_or_create
from .tts import EngineError, ModelNotInstalled, create_provider
from .voices import VoiceRegistry

log = logging.getLogger("localtts.server")

WORKSPACE = "workspace"

_state: dict = {"provider": None, "voices": None, "pipeline": None, "config": None,
                "ready": False, "loading": False, "error": None, "started_at": time.time()}
_load_lock = threading.Lock()
_jobs: dict = {}          # job_id -> {state, done, total, variants, error, payload}
_jobs_lock = threading.Lock()


def _load_engine(conf: cfg.Config) -> None:
    with _load_lock:
        if _state["ready"]:
            return
        _state["loading"] = True
        _state["error"] = None
        try:
            installed, msg = caps.is_installed("qwen")
            if not installed:
                raise ModelNotInstalled(msg)
            log.info("Loading Qwen3-TTS (device=%s)...", conf["device"])
            provider = create_provider("qwen", conf["device"])
            provider.initialize()
            voices = VoiceRegistry(provider)
            _state.update(provider=provider, voices=voices,
                          pipeline=Pipeline(provider, voices, conf.data), ready=True)
            log.info("Готово. Голоса: %s", ", ".join(voices.ids()))
        except Exception as e:
            log.exception("engine load failed")
            _state["error"] = str(e)
        finally:
            _state["loading"] = False


def _ws():
    """The single workspace project (created on first access)."""
    try:
        from .projects import get_project

        return get_project(WORKSPACE)
    except KeyError:
        from .projects import create_project

        return create_project("Рабочая область", script="", slug=WORKSPACE)


def create_app(conf: cfg.Config | None = None, *, load_engine: bool = True) -> Flask:
    conf = conf or cfg.load_config()
    setup_logging()
    netguard.apply(conf.offline)
    cfg.ensure_config_file()

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    try:
        app.json.sort_keys = False   # keep preset / dict ordering as authored
    except Exception:
        app.config["JSON_SORT_KEYS"] = False
    _state["config"] = conf
    web = cfg.WEB_DIR

    if load_engine:
        threading.Thread(target=_load_engine, args=(conf,), daemon=True).start()

    @app.errorhandler(Exception)
    def _json_errors(e):
        from werkzeug.exceptions import HTTPException

        code = e.code if isinstance(e, HTTPException) else 500
        if request.path.startswith("/api/"):
            if code == 413:
                msg = "Файл слишком большой. Загрузите меньше клипов за раз."
            elif code == 404:
                msg = f"Нет такого endpoint: {request.path}. Перезапустите студию (LocalTTS.bat)."
            elif isinstance(e, HTTPException):
                msg = e.description
            else:
                log.exception("unhandled error on %s", request.path)
                msg = f"{type(e).__name__}: {e}"
            return jsonify(error=msg), code
        if isinstance(e, HTTPException):
            return e
        log.exception("unhandled error on %s", request.path)
        return "Internal error — see logs/localtts.log", 500

    # -- static UI ---------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(web / "templates", "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(web / "static", name)

    # -- info ------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify(ready=_state["ready"], loading=_state["loading"], error=_state["error"])

    @app.get("/api/info")
    def info():
        from .audio import have_ffmpeg

        p = _ws()
        payload = {
            "app": "LocalTTS Studio",
            "ready": _state["ready"], "loading": _state["loading"], "error": _state["error"],
            "offline_mode": conf.offline,
            "ffmpeg": have_ffmpeg(),
            "language": conf.get("language", "ru"),
            "presets": presets.list_presets(conf),
            "mp3_bitrate_choices": conf.get("audio", {}).get("mp3_bitrate_choices", ["128k", "160k"]),
            "mp3_bitrate": conf.get("audio", {}).get("mp3_bitrate", "160k"),
            "audio_post": conf.get("audio", {}).get("post", {}),
            "stress_marker": conf.get("stress", {}).get("marker", "+"),
            "defaults": {
                "voice": conf.get("default_voice"),
                "preset": conf.get("default_preset", "обычный"),
                "variants": conf.get("default_variants", 1),
            },
            "hardware": hardware.probe(),
            "hardware_summary": hardware.summary_line(),
            "script": p.read_script(),
            "settings": _ws_settings(p),
            "history": p.generations,
        }
        if _state["ready"]:
            prov = _state["provider"]
            payload.update(
                sample_rate=prov.sample_rate,
                voices=voices_mod.list_all(prov),
            )
        else:
            payload["voices"] = []
        return jsonify(payload)

    @app.get("/api/hardware")
    def hw():
        return jsonify(hardware.probe())

    # -- workspace (script autosave) -----------------------------------
    @app.put("/api/workspace")
    def workspace_save():
        data = request.get_json(force=True, silent=True) or {}
        p = _ws()
        if "script" in data:
            p.write_script(data["script"])
        s = data.get("settings") or {}
        for k in ("voice", "preset", "speed", "mp3_bitrate", "post_enabled", "n_variants"):
            if k in s and hasattr(p.settings, k):
                setattr(p.settings, k, s[k])
        p.save()
        return jsonify(ok=True)

    # -- analyze / estimate -------------------------------------------
    @app.post("/api/analyze")
    def analyze():
        data = request.get_json(force=True, silent=True) or {}
        text = data.get("text", "")
        preset = data.get("preset") or conf.get("default_preset", "обычный")
        speed = float(data.get("speed", 1.0) or 1.0)
        out = {"metrics": estimate_metrics(text, conf.data, preset, speed),
               "analysis": analyze_text(text, conf.data)}
        if _state["ready"] and data.get("plan"):
            req = _req_from({**data, "text": text})
            _segs, warns, ctx = _state["pipeline"].plan(req)
            out["plan"] = {"segments": ctx["plan"], "warnings": warns,
                           "normalized": ctx["normalized"], "replacements": ctx["replacements"]}
        return jsonify(out)

    @app.post("/api/apply-suggestions")
    def apply_suggestions():
        data = request.get_json(force=True, silent=True) or {}
        return jsonify(text=analyze_text(data.get("text", ""), conf.data)["auto_text"])

    # -- generate (background job so multi-variant shows live progress) -----
    def _result_payload(result, project):
        gid = result.generation_id
        vout = []
        for v in result.variants:
            if v.error:
                vout.append({"index": v.index, "error": v.error})
                continue
            sub = f"variant_{v.index}/"
            fn = v.final_mp3 or v.final_wav
            vout.append({
                "index": v.index, "seed": v.seed,
                "duration_clock": v.duration_clock, "duration_sec": v.duration_sec, "lufs": v.lufs,
                "audio_url": f"/api/gen/{gid}/{sub}{fn}",
                "download_url": f"/api/gen/{gid}/{sub}{fn}?download=1",
                "wav_url": f"/api/gen/{gid}/{sub}{v.final_wav}?download=1" if v.final_wav else None,
                "raw_url": f"/api/gen/{gid}/{sub}{v.raw_wav}?download=1" if v.raw_wav else None,
            })
        return dict(
            generation_id=gid, variants=vout,
            n_speech=result.n_speech, n_silence=result.n_silence,
            preset=result.preset, voice=result.voice,
            realtime_factor=result.realtime_factor, elapsed_sec=result.elapsed_sec,
            post_applied=result.post_applied, warnings=result.warnings,
            normalized_text=result.normalized_text, original_text=result.original_text,
            history=project.generations if project else [],
        )

    def _run_job(job_id, req, project):
        try:
            result = _state["pipeline"].generate(req, project=project)
            with _jobs_lock:
                _jobs[job_id].update(state="done", done=len(result.variants),
                                     payload=_result_payload(result, project))
        except EngineError as e:
            with _jobs_lock:
                _jobs[job_id].update(state="error", error=str(e))
        except Exception as e:
            log.exception("generation job crashed")
            with _jobs_lock:
                _jobs[job_id].update(state="error", error=f"Ошибка: {e}. См. logs/localtts.log.")

    @app.post("/api/generate")
    def generate():
        if not _state["ready"]:
            return jsonify(error=_state["error"] or "Модель ещё загружается…"), 503
        data = request.get_json(force=True, silent=True) or {}
        p = _ws()
        if "script" in data:
            p.write_script(data["script"])
        for k in ("voice", "preset", "speed", "mp3_bitrate", "post_enabled", "n_variants"):
            if k in data and hasattr(p.settings, k):
                setattr(p.settings, k, data[k])
        p.save()
        req = _req_from({**data, "text": data.get("script", p.read_script())})
        if not (req.text or "").strip():
            return jsonify(error="Пустой сценарий."), 400
        job_id = _dt.datetime.now().strftime("%H%M%S_") + str(int(time.time() * 1000) % 100000)
        with _jobs_lock:
            _jobs[job_id] = {"state": "running", "done": 0, "total": req.n_variants,
                             "variants": [], "error": None, "payload": None}
            for k in list(_jobs):  # keep the last 20 jobs
                if len(_jobs) > 20:
                    _jobs.pop(next(iter(_jobs)))
        threading.Thread(target=_run_job, args=(job_id, req, p), daemon=True).start()
        return jsonify(job_id=job_id, total=req.n_variants)

    @app.get("/api/generate/status/<job_id>")
    def generate_status(job_id):
        with _jobs_lock:
            j = _jobs.get(job_id)
        if j is None:
            return jsonify(error="Задача не найдена (перезапуск сервера?)"), 404
        # peek at partial progress from the pipeline log-less way: count files
        if j["state"] == "running":
            gdirs = sorted((cfg.PROJECTS_DIR / WORKSPACE / "generations").glob("*/"),
                           key=lambda d: d.stat().st_mtime, reverse=True)
            if gdirs:
                done = len(list(gdirs[0].glob("variant_*/final.wav")))
                j = {**j, "done": min(done, j["total"])}
        return jsonify(state=j["state"], done=j["done"], total=j["total"],
                       error=j["error"], **(j["payload"] or {}))

    @app.get("/api/gen/<gid>/<path:name>")
    def gen_file(gid, name):
        from werkzeug.security import safe_join

        base = cfg.PROJECTS_DIR / WORKSPACE / "generations" / gid
        full = safe_join(str(base), name)
        if full is None or not Path(full).exists():
            abort(404)
        return send_from_directory(base, name, as_attachment=request.args.get("download") == "1")

    @app.post("/api/keep/<gid>")
    @app.post("/api/keep/<gid>/<int:vi>")
    def keep(gid, vi=1):
        base = cfg.PROJECTS_DIR / WORKSPACE / "generations" / gid
        vdir = base / f"variant_{vi}"
        src = vdir if vdir.is_dir() else base
        finals = list(base.parent.parent.glob("final")) or [cfg.PROJECTS_DIR / WORKSPACE / "final"]
        dest = cfg.PROJECTS_DIR / WORKSPACE / "final"
        dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for name in ("final.mp3", "final.wav"):
            f = src / name
            if f.exists():
                import shutil
                out = dest / f"{gid}_v{vi}_{name}"
                shutil.copyfile(f, out)
                copied.append(out.name)
        if not copied:
            abort(404)
        try:
            pj = _ws()
            for g in pj.generations:
                if g.get("id") == gid:
                    g["selected"] = vi
            pj.save()
        except Exception:
            pass
        return jsonify(ok=True, files=copied)

    # -- voices --------------------------------------------------
    @app.get("/api/voices")
    def voices_list():
        if not _state["ready"]:
            return jsonify(voices=[], error=_state["error"] or "загрузка модели"), 200
        return jsonify(voices=voices_mod.list_all(_state["provider"]))

    @app.post("/api/voices")
    def voices_add():
        if not _state["ready"]:
            return jsonify(error="Модель ещё загружается…"), 503
        files = [f for f in request.files.getlist("audio") if f and f.filename]
        if not files:
            return jsonify(error="Прикрепите хотя бы один аудиофайл (MP3/WAV/…)."), 400
        form = request.form
        vid = voices_mod.slug_voice_id(form.get("id") or Path(files[0].filename).stem)
        langs = [x.strip() for x in (form.get("languages") or "ru").replace(";", ",").split(",") if x.strip()]

        import shutil as _sh
        import tempfile

        tmpdir = Path(tempfile.mkdtemp(prefix="localtts_voice_"))
        try:
            saved = []
            for i, f in enumerate(files):
                pth = tmpdir / f"{i:03d}{Path(f.filename).suffix or '.wav'}"
                f.save(str(pth))
                saved.append(pth)
            res = voices_mod.add_voice(
                vid, form.get("label") or vid, form.get("gender") or "unknown",
                saved if len(saved) > 1 else saved[0],
                reference_text=form.get("reference_text"), languages=langs,
                clean=(form.get("clean", "1") != "0"),
                max_seconds=int(form.get("max_seconds", 45) or 45),
                overwrite=True,   # same id always replaces
            )
        except (FileNotFoundError, RuntimeError, ValueError) as e:
            return jsonify(error=str(e)), 400
        finally:
            _sh.rmtree(tmpdir, ignore_errors=True)
        _after_voice_change(res["voice"].get("reference_wav"))
        return jsonify(ok=True, **res, voices=voices_mod.list_all(_state["provider"]))

    @app.put("/api/voices/<vid>")
    def voices_update(vid):
        data = request.get_json(force=True, silent=True) or {}
        try:
            v = voices_mod.update_voice_meta(
                vid, label=data.get("label"), gender=data.get("gender"),
                languages=data.get("languages"), reference_text=data.get("reference_text"))
        except FileNotFoundError:
            abort(404)
        _after_voice_change(v.get("reference_wav"))
        return jsonify(ok=True, voice=v, voices=voices_mod.list_all(_state["provider"]) if _state["ready"] else [])

    @app.delete("/api/voices/<vid>")
    def voices_delete(vid):
        try:
            ref = None
            from .config import VOICES_DIR

            import json as _j
            vj = VOICES_DIR / vid / "voice.json"
            if vj.exists():
                ref = str((VOICES_DIR / vid / "reference.wav").resolve())
            voices_mod.delete_voice(vid)
        except FileNotFoundError:
            abort(404)
        _after_voice_change(ref)
        return jsonify(ok=True, voices=voices_mod.list_all(_state["provider"]) if _state["ready"] else [])

    @app.get("/api/voices/<vid>/preview.wav")
    def voices_preview(vid):
        folder = cfg.VOICES_DIR / vid
        if not (folder / "reference.wav").exists():
            abort(404)
        return send_from_directory(folder, "reference.wav")

    # -- presets ------------------------------------------------
    @app.get("/api/presets")
    def presets_list():
        return jsonify(presets=presets.list_presets(conf))

    @app.post("/api/presets")
    def presets_save():
        data = request.get_json(force=True, silent=True) or {}
        try:
            v = presets.save_preset(conf, data.get("name", ""), data.get("values", data))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True, preset=v, presets=presets.list_presets(conf))

    @app.delete("/api/presets/<name>")
    def presets_delete(name):
        presets.delete_preset(conf, name)
        return jsonify(ok=True, presets=presets.list_presets(conf))

    # -- lifecycle --------------------------------------------
    @app.post("/api/shutdown")
    def shutdown():
        prov = _state.get("provider")
        if prov is not None:
            try:
                prov.shutdown()
            except Exception:
                pass
        log.info("shutdown requested")
        func = request.environ.get("werkzeug.server.shutdown")
        if func:
            func()
        else:
            threading.Timer(0.3, _hard_exit).start()
        return jsonify(ok=True)

    return app


def _after_voice_change(reference_wav: str | None) -> None:
    if _state.get("voices"):
        _state["voices"].reload()
    prov = _state.get("provider")
    if prov is not None and hasattr(prov, "invalidate_voice"):
        try:
            prov.invalidate_voice(reference_wav)
        except Exception:
            pass


def _ws_settings(p) -> dict:
    from dataclasses import asdict

    return asdict(p.settings)


def _hard_exit():
    import os

    os._exit(0)


def _req_from(data: dict) -> GenerationRequest:
    c = _state.get("config")
    acfg = c.get("audio", {}) if c else {}
    try:
        n_var = int(data.get("n_variants", c.get("default_variants", 1) if c else 1) or 1)
    except (TypeError, ValueError):
        n_var = 1
    seed = data.get("seed")
    return GenerationRequest(
        text=data.get("text") or data.get("script") or "",
        voice_id=data.get("voice") or (c.get("default_voice") if c else "ru_female"),
        preset=data.get("preset") or (c.get("default_preset", "обычный") if c else "обычный"),
        language=(c.get("language", "ru") if c else "ru"),
        speed=float(data.get("speed", 1.0) or 1.0),
        n_variants=max(1, min(5, n_var)),
        seed=int(seed) if seed not in (None, "") else None,
        mp3_bitrate=str(data.get("mp3_bitrate", acfg.get("mp3_bitrate", "192k"))),
        post_enabled=bool(data.get("post_enabled", True)),
        make_mp3=bool(data.get("mp3", True)),
        filename_hint=data.get("filename") or "narration",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="localtts-server")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--device")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)

    setup_logging()
    conf = cfg.load_config()
    if args.host:
        conf.data["server"]["host"] = args.host
    if args.port:
        conf.data["server"]["port"] = args.port
    if args.device:
        conf.data["device"] = args.device

    if args.selftest:
        from scripts.check_env import offline_selftest

        return 0 if offline_selftest(conf) else 1

    host, port = conf.host, conf.port
    app = create_app(conf, load_engine=True)
    url = f"http://{host}:{port}"
    if conf.get("server", {}).get("open_browser", True) and not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    log.info("LocalTTS Studio на %s  (Ctrl+C — остановить)", url)
    from werkzeug.serving import run_simple

    run_simple(host, port, app, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
