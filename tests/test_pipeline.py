import numpy as np
import pytest
import soundfile as sf

from app.config import DEFAULTS
from app.pipeline import GenerationRequest, Pipeline, _safe, _unique
from app.tts.mock_engine import MockProvider
from app.voices import VoiceRegistry, add_voice


def _voice(tmp_path, vid="ru_test", text="это тест", seconds=8):
    ref = tmp_path / f"{vid}.wav"
    sf.write(str(ref), (0.1 * np.sin(np.linspace(0, 400, 24000 * seconds))).astype("float32"), 24000)
    add_voice(vid, vid, "female", ref, reference_text=text, clean=False, overwrite=True)


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    import app.pipeline as pl
    monkeypatch.setattr(pl, "OUTPUT_DIR", tmp_path)
    _voice(tmp_path)
    prov = MockProvider()
    prov.initialize()
    return Pipeline(prov, VoiceRegistry(prov), DEFAULTS), prov, tmp_path


def test_safe_and_unique(tmp_path):
    assert _safe("Видео 1!") == "Видео_1" or _safe("Video 1!") == "Video_1"
    p = tmp_path / "a.wav"
    p.write_bytes(b"1")
    assert _unique(p).name == "a_2.wav"


def test_empty_raises(pipe):
    pl, _, _ = pipe
    with pytest.raises(Exception):
        pl.generate(GenerationRequest(text="  ", voice_id="ru_test"))


def test_single_variant(pipe):
    pl, _, _ = pipe
    r = pl.generate(GenerationRequest(text="Привет. Это тест озвучки.", voice_id="ru_test",
                                      preset="обычный", make_mp3=False, post_enabled=False))
    assert len(r.variants) == 1
    v = r.variants[0]
    assert v.error is None and v.duration_sec > 0


def test_five_variants_distinct_seeds(pipe):
    pl, _, _ = pipe
    r = pl.generate(GenerationRequest(text="Один и тот же текст для всех вариантов.",
                                      voice_id="ru_test", preset="радостный", n_variants=5,
                                      make_mp3=False, post_enabled=False, seed=1000))
    assert len(r.variants) == 5
    seeds = [v.seed for v in r.variants]
    assert seeds == [1000, 1001, 1002, 1003, 1004]
    assert all(v.error is None for v in r.variants)


def test_variants_clamped_to_range(pipe):
    pl, _, _ = pipe
    r = pl.generate(GenerationRequest(text="Текст.", voice_id="ru_test", n_variants=99,
                                      make_mp3=False, post_enabled=False))
    assert len(r.variants) == 5
    r2 = pl.generate(GenerationRequest(text="Текст.", voice_id="ru_test", n_variants=0,
                                       make_mp3=False, post_enabled=False))
    assert len(r2.variants) == 1


def test_normalizer_and_report(pipe):
    pl, _, td = pipe
    r = pl.generate(GenerationRequest(text="Сайт на C++ за 5 рублей.", voice_id="ru_test",
                                      preset="обычный", make_mp3=False, post_enabled=False))
    assert "си плюс плюс" in r.normalized_text and "пять" in r.normalized_text
    assert r.report_path and __import__("pathlib").Path(r.report_path).exists()


def test_paragraph_pause_in_output(pipe):
    pl, _, _ = pipe
    r = pl.generate(GenerationRequest(text="Первый абзац.\n\nВторой абзац.", voice_id="ru_test",
                                      preset="обычный", make_mp3=False, post_enabled=False))
    assert r.n_silence == 1


def test_preset_params_reach_style(pipe):
    pl, _, _ = pipe
    segs, _w, _ctx = pl.plan(GenerationRequest(text="Текст.", voice_id="ru_test", preset="грустный"))
    st = [s for s in segs if s.is_speech][0].style
    assert st.temperature == pytest.approx(DEFAULTS["presets"]["грустный"]["generation"]["temperature"])


def test_voice_without_transcript_raises(pipe, tmp_path):
    pl, _, _ = pipe
    _voice(tmp_path, "notxt", text="")
    import json
    (tmp_path.parent / "does_not_matter")  # noop
    import app.voices as V
    vj = V.VOICES_DIR / "notxt" / "voice.json"
    d = json.loads(vj.read_text(encoding="utf-8"))
    d.pop("reference_text", None)
    vj.write_text(json.dumps(d), encoding="utf-8")
    pl.voices.reload()
    with pytest.raises(Exception):
        pl.generate(GenerationRequest(text="Привет.", voice_id="notxt", preset="обычный"))


def test_records_history(pipe):
    pl, _, _ = pipe
    from app import projects as prj
    p = prj.create_project("W", slug="workspace")
    r = pl.generate(GenerationRequest(text="Запись в историю.", voice_id="ru_test",
                                      preset="обычный", make_mp3=True, post_enabled=False,
                                      n_variants=2), project=p)
    g = prj.get_project("workspace").generations[0]
    assert g["id"] == r.generation_id and g["n_variants"] == 2
