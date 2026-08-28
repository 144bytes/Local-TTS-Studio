import pytest

from app.config import DEFAULTS
from app.prosody import Style, TextPrep

CFG = DEFAULTS


@pytest.fixture
def tp():
    return TextPrep(CFG)


def test_plain_text_one_segment(tp):
    pt = tp.prepare("Привет мир. Это тест.", "обычный", "ru")
    assert [s.kind for s in pt.segments] == ["speech"]
    assert pt.warnings == []


@pytest.mark.parametrize("txt", ["", "   ", "\n\n", None])
def test_empty(tp, txt):
    assert tp.prepare(txt, "обычный", "ru").segments == []


def test_short_paragraphs_stay_whole(tp):
    pt = tp.prepare("Первое предложение. Второе предложение. Третье.", "обычный", "ru")
    # under target_chars -> ONE chunk, no inner pauses
    assert [s.kind for s in pt.segments] == ["speech"]


def test_blank_line_is_paragraph_pause(tp):
    pt = tp.prepare("Первый абзац.\n\nВторой абзац.", "обычный", "ru")
    assert [s.kind for s in pt.segments] == ["speech", "silence", "speech"]
    assert pt.segments[1].boundary == "paragraph"
    assert pt.segments[1].duration_ms > 0


def test_pause_scale_from_preset(tp):
    base = tp.prepare("А.\n\nБ.", "обычный", "ru", seed=1).segments[1].duration_ms
    sad = tp.prepare("А.\n\nБ.", "грустный", "ru", seed=1).segments[1].duration_ms
    assert sad > base


def test_pause_jitter_varies(tp):
    a = tp.prepare("А.\n\nБ.", "радостный", "ru", seed=1).segments[1].duration_ms
    b = tp.prepare("А.\n\nБ.", "радостный", "ru", seed=2).segments[1].duration_ms
    assert a != b  # jitter uses the seed


def test_long_paragraph_chunks_at_sentences(tp):
    text = " ".join(f"Это предложение номер {i}, оно среднего размера." for i in range(60))
    pt = tp.prepare(text, "обычный", "ru")
    speech = [s for s in pt.segments if s.is_speech]
    assert len(speech) > 1
    assert all(len(s.text) <= CFG["chunking"]["max_chars"] for s in speech)
    # chunks are big — not per-sentence
    assert all(len(s.text) > 200 for s in speech[:-1])


def test_preset_params_on_style(tp):
    st = tp.prepare("Текст.", "взволнованный", "ru").segments[0].style
    assert st.temperature == pytest.approx(CFG["presets"]["взволнованный"]["generation"]["temperature"])


def test_stress_plus_marker_becomes_accent(tp):
    txt = [s.text for s in tp.prepare("Это проц+ессор.", "обычный", "ru").segments if s.is_speech][0]
    assert "́" in txt and "+" not in txt


def test_stress_dictionary_applied(tp):
    txt = [s.text for s in tp.prepare("Мой комп завис.", "обычный", "ru").segments if s.is_speech][0]
    assert "́" in txt


def test_leftover_brackets_stripped_with_warning(tp):
    pt = tp.prepare("Старый [emphasis] текст [pause] тут.", "обычный", "ru")
    assert any("квадратн" in w for w in pt.warnings)
    assert "[" not in pt.spoken_text and "emphasis" not in pt.spoken_text


def test_normalizer_runs_inside_prepare(tp):
    pt = tp.prepare("Сайт на C++ и JavaScript.", "обычный", "ru")
    assert "си плюс плюс" in pt.normalized and "джаваскрипт" in pt.normalized
    assert any(r["kind"] == "lexicon" for r in pt.replacements)


def test_style_clamped():
    s = Style(temperature=5, top_p=2, repetition_penalty=9).clamped()
    assert s.temperature <= 1.3 and s.top_p <= 1.0 and s.repetition_penalty <= 1.5


def test_known_preset_names_ordered(tp):
    names = tp.known_preset_names()
    assert names[0] == "обычный" and "документальный" in names
