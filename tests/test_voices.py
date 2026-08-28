import numpy as np
import pytest
import soundfile as sf

from app import voices as vmod
from app.tts.mock_engine import MockProvider


def _wav(path, seconds=8, sr=22050):
    t = np.arange(int(sr * seconds)) / sr
    sig = (0.2 * np.sin(2 * np.pi * 180 * t)).astype("float32")
    sf.write(str(path), sig, sr)
    return path


def test_slug_voice_id():
    assert vmod.slug_voice_id("Ru My Voice!") == "ru_my_voice"
    assert vmod.slug_voice_id("  ") == "voice"


@pytest.mark.skipif(not __import__("app.audio", fromlist=["have_ffmpeg"]).have_ffmpeg(),
                    reason="ffmpeg missing")
def test_add_voice_cleans_and_writes(tmp_path):
    src = _wav(tmp_path / "in.wav", seconds=10)
    res = vmod.add_voice("test_v", "Test V", "female", src,
                         reference_text="привет это тест", languages=["ru"])
    v = res["voice"]
    assert v["id"] == "test_v" and v["reference_text"] == "привет это тест"
    ref = vmod.VOICES_DIR / "test_v" / "reference.wav"
    info = sf.info(str(ref))
    assert info.samplerate == 24000 and info.channels == 1
    assert not res["warnings"]  # had transcript + long enough


@pytest.mark.skipif(not __import__("app.audio", fromlist=["have_ffmpeg"]).have_ffmpeg(),
                    reason="ffmpeg missing")
def test_add_voice_combines_multiple_clips(tmp_path):
    clips = [
        _wav(tmp_path / "a.wav", seconds=12, sr=22050),
        _wav(tmp_path / "b.wav", seconds=13, sr=16000),
        _wav(tmp_path / "c.wav", seconds=11, sr=44100),
    ]
    res = vmod.add_voice("combo", "Combo", "male", clips,
                         reference_text="one. two. three.", languages=["ru"])
    assert res["sources"] == 3
    assert any("Combined 3 clips" in w for w in res["warnings"])
    info = sf.info(str(vmod.VOICES_DIR / "combo" / "reference.wav"))
    assert info.samplerate == 24000 and info.channels == 1
    assert 25 < info.frames / info.samplerate <= 45   # trimmed/capped


@pytest.mark.skipif(not __import__("app.audio", fromlist=["have_ffmpeg"]).have_ffmpeg(),
                    reason="ffmpeg missing")
def test_add_voice_warns_without_transcript(tmp_path):
    res = vmod.add_voice("no_txt", "No Text", "male", _wav(tmp_path / "a.wav"))
    assert any("Qwen" in w for w in res["warnings"])


@pytest.mark.skipif(not __import__("app.audio", fromlist=["have_ffmpeg"]).have_ffmpeg(),
                    reason="ffmpeg missing")
def test_add_voice_same_id_always_replaces(tmp_path):
    a = _wav(tmp_path / "a.wav")
    b = _wav(tmp_path / "b.wav", seconds=12)
    vmod.add_voice("dup_v", "Dup", "female", a, reference_text="первый", overwrite=True)
    r2 = vmod.add_voice("dup_v", "Dup2", "female", b, reference_text="второй", overwrite=True)
    assert r2["voice"]["reference_text"] == "второй" and r2["voice"]["label"] == "Dup2"


@pytest.mark.skipif(not __import__("app.audio", fromlist=["have_ffmpeg"]).have_ffmpeg(),
                    reason="ffmpeg missing")
def test_update_voice_meta(tmp_path):
    vmod.add_voice("edit_v", "Edit", "female", _wav(tmp_path / "a.wav"), overwrite=True)
    v = vmod.update_voice_meta("edit_v", label="Отредактировано", reference_text="добавил текст")
    assert v["label"] == "Отредактировано" and v["reference_text"] == "добавил текст"
    assert "reference_wav" in v


def test_delete_voice(tmp_path):
    (vmod.VOICES_DIR / "gone").mkdir(parents=True)
    (vmod.VOICES_DIR / "gone" / "voice.json").write_text('{"id":"gone","label":"g"}', encoding="utf-8")
    vmod.delete_voice("gone")
    with pytest.raises(FileNotFoundError):
        vmod.delete_voice("gone")


@pytest.mark.skipif(not __import__("app.audio", fromlist=["have_ffmpeg"]).have_ffmpeg(),
                    reason="ffmpeg missing")
def test_list_all_flags(tmp_path):
    prov = MockProvider()
    prov.initialize()
    vmod.add_voice("lv", "LV", "female", _wav(tmp_path / "a.wav"), reference_text="t", overwrite=True)
    rows = {r["id"]: r for r in vmod.list_all(prov)}
    assert rows["lv"]["kind"] == "custom" and rows["lv"]["has_transcript"] is True
