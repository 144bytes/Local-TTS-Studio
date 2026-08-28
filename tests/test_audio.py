import numpy as np
import pytest

from app import audio


def test_silence_len():
    s = audio.silence(500, 24000)
    assert s.shape == (12000,) and not s.any() and s.dtype == np.float32


def test_silence_nonpositive_empty():
    assert audio.silence(0, 24000).size == 0
    assert audio.silence(-5, 24000).size == 0


def test_gain_db():
    x = np.ones(8, dtype=np.float32)
    assert audio.apply_gain_db(x, 6.0)[0] == pytest.approx(1.9953, rel=1e-3)
    assert audio.apply_gain_db(x, 0.0) is x


def test_concat_order_and_empty():
    out = audio.concat([np.array([1, 2], np.float32), np.zeros(0, np.float32), np.array([3], np.float32)])
    assert out.tolist() == [1.0, 2.0, 3.0]
    assert audio.concat([None, np.zeros(0)]).size == 0


def test_peak_normalize():
    x = (np.random.default_rng(0).standard_normal(2000) * 0.03).astype(np.float32)
    assert np.max(np.abs(audio.peak_normalize(x, -1.0))) == pytest.approx(10 ** (-1 / 20), rel=1e-4)


def test_pause_between_speech_is_silent():
    sr = 24000
    out = audio.concat([np.ones(sr, np.float32), audio.silence(400, sr), np.ones(sr // 2, np.float32)])
    assert out.size == sr + int(sr * 0.4) + sr // 2
    assert not out[sr:sr + int(sr * 0.4)].any()


def test_build_post_filter_respects_toggles():
    f = audio.build_post_filter({"enabled": True, "loudness_normalize": True, "limiter": True}, 24000)
    assert any("loudnorm" in c for c in f) and any("alimiter" in c for c in f)
    assert audio.build_post_filter({"enabled": False}, 24000) == []


def test_wav_roundtrip(tmp_path):
    import soundfile as sf

    x = np.sin(np.linspace(0, 20, 24000)).astype(np.float32)
    p = audio.write_wav(tmp_path / "x.wav", x, 24000)
    back, sr = sf.read(str(p))
    assert sr == 24000 and len(back) == 24000


@pytest.mark.skipif(not audio.have_ffmpeg(), reason="ffmpeg missing")
def test_time_stretch_faster_shortens():
    sr = 24000
    x = np.sin(np.linspace(0, 40, sr)).astype(np.float32)
    out = audio.time_stretch(x, sr, 1.25)
    assert 0.7 * sr < out.size < 0.9 * sr


def test_time_stretch_identity_noop():
    x = np.linspace(-1, 1, 500).astype(np.float32)
    assert audio.time_stretch(x, 24000, 1.0) is x


@pytest.mark.skipif(not audio.have_ffmpeg(), reason="ffmpeg missing")
def test_post_process_and_mp3(tmp_path):
    sr = 24000
    x = (np.random.default_rng(1).standard_normal(sr * 2) * 0.2).astype(np.float32)
    out, applied = audio.post_process(x, sr, {"enabled": True, "loudness_normalize": True,
                                              "target_lufs": -16.0, "true_peak_db": -1.5, "limiter": True})
    assert out.size > 0 and applied
    p = audio.write_mp3(tmp_path / "x.mp3", out, sr, bitrate="128k")
    assert p.exists() and p.stat().st_size > 200
