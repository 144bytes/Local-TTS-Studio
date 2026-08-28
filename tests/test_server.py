"""Server API smoke tests, driven by the mock engine (no model download)."""
import io
import time

import numpy as np
import pytest
import soundfile as sf

import app.server as srv
from app.config import load_config


@pytest.fixture
def client(monkeypatch):
    conf = load_config()
    app = srv.create_app(conf, load_engine=False)
    app.config.update(TESTING=True)
    # load the mock engine synchronously
    from app.tts.mock_engine import MockProvider
    from app.pipeline import Pipeline
    from app.voices import VoiceRegistry
    prov = MockProvider()
    prov.initialize()
    vr = VoiceRegistry(prov)
    srv._state.update(provider=prov, voices=vr, pipeline=Pipeline(prov, vr, conf.data),
                      ready=True, loading=False, error=None)
    yield app.test_client()
    srv._state.update(provider=None, voices=None, pipeline=None, ready=False)


def _wav_bytes(seconds=9, sr=22050):
    buf = io.BytesIO()
    sf.write(buf, (0.2 * np.sin(np.linspace(0, 900, sr * seconds))).astype("float32"), sr, format="WAV")
    buf.seek(0)
    return buf


def _generate(client, **body):
    """POST /api/generate then poll the job to completion; returns the final payload."""
    r = client.post("/api/generate", json=body)
    if r.status_code != 200:
        return r
    job = r.get_json()
    if "job_id" not in job:
        return r
    for _ in range(200):
        s = client.get(f"/api/generate/status/{job['job_id']}").get_json()
        if s["state"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("generation job did not finish")


def _add_voice(client, vid="ru_my", text="это тестовая запись"):
    return client.post("/api/voices", data={
        "audio": (_wav_bytes(), "clip.wav"), "id": vid, "label": vid,
        "gender": "female", "languages": "ru", "reference_text": text,
    }, content_type="multipart/form-data")


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200 and b"LOCAL" in r.data.upper()


def test_info(client):
    j = client.get("/api/info").get_json()
    assert j["ready"] is True
    assert j["language"] == "ru"
    assert "обычный" in j["presets"]
    assert "script" in j and "settings" in j


def test_analyze(client):
    j = client.post("/api/analyze", json={"text": "Сайт на C++ за 5 рублей. Слово Blazor внутри."}).get_json()
    assert j["metrics"]["words"] >= 4
    assert j["analysis"]["counts"]["latin"] >= 1  # Blazor not in lexicon
    assert j["analysis"]["counts"]["changed"] >= 2  # C++ + number
    assert "auto_text" in j["analysis"]
    assert "си плюс плюс" in j["analysis"]["normalized"]


def test_apply_suggestions(client):
    j = client.post("/api/apply-suggestions", json={"text": "Сайт на JavaScript"}).get_json()
    assert "джаваскрипт" in j["text"] and j["text"].endswith(".")


def test_voice_add_edit_delete_cycle(client):
    from app.audio import have_ffmpeg
    if not have_ffmpeg():
        pytest.skip("ffmpeg missing")

    r = _add_voice(client, "cyc")
    assert r.status_code == 200, r.get_json()
    assert any(v["id"] == "cyc" for v in r.get_json()["voices"])

    # edit transcript via PUT
    e = client.put("/api/voices/cyc", json={"reference_text": "новая расшифровка", "label": "Цикл"})
    assert e.status_code == 200
    assert e.get_json()["voice"]["reference_text"] == "новая расшифровка"

    # delete then re-add same id -> should be the NEW voice (cache bug guard)
    assert client.delete("/api/voices/cyc").status_code == 200
    r2 = _add_voice(client, "cyc", "совсем другой текст")
    assert r2.status_code == 200
    v = next(v for v in r2.get_json()["voices"] if v["id"] == "cyc")
    assert v["reference_text"] == "совсем другой текст"

    client.delete("/api/voices/cyc")


def test_generate_requires_voice(client):
    s = _generate(client, script="Привет.", voice="нет-такого", preset="обычный")
    assert s["state"] == "error" and s["error"]


def test_generate_empty_script_rejected(client):
    r = client.post("/api/generate", json={"script": "   ", "voice": "x"})
    assert r.status_code == 400


def test_generate_end_to_end(client):
    from app.audio import have_ffmpeg
    if not have_ffmpeg():
        pytest.skip("ffmpeg missing")
    _add_voice(client, "gen")
    g = _generate(client, script="Первый абзац.\n\nВторой абзац.",
                  voice="gen", preset="грустный", post_enabled=False, n_variants=3)
    assert g["state"] == "done"
    assert len(g["variants"]) == 3 and g["n_silence"] == 1
    assert all(v.get("error") is None for v in g["variants"])
    a = client.get(g["variants"][0]["audio_url"])
    assert a.status_code == 200
    a.get_data(); a.close()
    assert client.post(f"/api/keep/{g['generation_id']}/2").status_code == 200
    client.delete("/api/voices/gen")


def test_generate_single_variant_default(client):
    from app.audio import have_ffmpeg
    if not have_ffmpeg():
        pytest.skip("ffmpeg missing")
    _add_voice(client, "s1")
    g = _generate(client, script="Одна фраза.", voice="s1", preset="обычный", post_enabled=False)
    assert g["state"] == "done" and len(g["variants"]) == 1
    client.delete("/api/voices/s1")


def test_variant_range_rejected_high(client):
    from app.audio import have_ffmpeg
    if not have_ffmpeg():
        pytest.skip("ffmpeg missing")
    _add_voice(client, "s2")
    g = _generate(client, script="Фраза.", voice="s2", n_variants=9, post_enabled=False)
    assert g["state"] == "done" and len(g["variants"]) == 5   # clamped
    client.delete("/api/voices/s2")


def test_presets_endpoint(client):
    j = client.get("/api/presets").get_json()["presets"]
    assert list(j)[0] == "обычный"


def test_no_engine_switch_endpoint(client):
    assert client.post("/api/engine/reload", json={"engine": "qwen"}).status_code == 404


def test_no_project_create_endpoint(client):
    assert client.post("/api/projects", json={"name": "x"}).status_code == 404


def test_api_404_is_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.is_json and "error" in r.get_json()
