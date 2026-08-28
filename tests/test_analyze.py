from app.analyze import analyze_text, auto_fix
from app.config import DEFAULTS


def test_shows_replacements():
    a = analyze_text("Сайт на C++ и Python. Цена 5 рублей.", DEFAULTS)
    kinds = {r["kind"] for r in a["replacements"]}
    assert "lexicon" in kinds and "number" in kinds
    assert a["counts"]["changed"] >= 3


def test_flags_unknown_latin():
    a = analyze_text("Использую Blazor каждый день.", DEFAULTS)
    assert a["counts"]["latin"] >= 1
    assert any(i["type"] == "latin" for i in a["issues"])


def test_flags_english_sentence():
    a = analyze_text("This is an English sentence here.", DEFAULTS)
    assert any(i["type"] == "english" for i in a["issues"])


def test_flags_no_vowel_word():
    a = analyze_text("Это ГТРК и ФСБ.", DEFAULTS)
    assert any(i["type"] == "no-vowel" for i in a["issues"])


def test_long_sentence_flagged():
    long_s = "И " + "ещё много слов " * 40 + "конец."
    a = analyze_text(long_s, DEFAULTS)
    assert a["counts"]["long"] >= 1


def test_auto_text_is_normalized():
    a = analyze_text("Сайт на C++.", DEFAULTS)
    assert "си плюс плюс" in a["auto_text"] and a["auto_text"].endswith(".")


def test_auto_fix_helper():
    assert "джаваскрипт" in auto_fix("Пишу на JavaScript", DEFAULTS)
