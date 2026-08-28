import pytest

from app.text_normalizer import TextNormalizer

TN = TextNormalizer({})


def n(s):
    return TN.normalize(s).normalized


# --- technical terms --------------------------------------------------------
@pytest.mark.parametrize("src,want", [
    ("Пишу на C++.", "си плюс плюс"),
    ("Люблю JavaScript.", "джаваскрипт"),
    ("Backend на PHP.", "пи эйч пи"),
    ("Node JS и React.", "нод джей эс"),
    ("Технология CGI.", "си джи ай"),
    ("Через HTTP запрос.", "эйч ти ти пи"),
    ("Формат JSON.", "джейсон"),
    ("Пишу на Python.", "пайтон"),
    ("Проект на C#.", "си шарп"),
])
def test_lexicon(src, want):
    assert want in n(src)


def test_lexicon_case_insensitive_and_boundary():
    assert "джаваскрипт" in n("JAVASCRIPT это язык")
    assert "джаваскрипт-разработчик" in n("Нужен JavaScript-разработчик")
    # a normal russian word containing latin-ish letters isn't broken
    assert "javascript" not in n("Я знаю JavaScript").lower()


def test_english_sentence_left_alone():
    s = "This is a JavaScript application built with React and Node."
    assert n(s) == s  # untouched


def test_mixed_ru_keeps_terms_translated():
    assert "джаваскрипт" in n("Я использую JavaScript каждый день.")


# --- numbers / dates / time ----------------------------------------------
@pytest.mark.parametrize("src,want", [
    ("Мне 5 лет.", "пять"),
    ("Ей 21 год.", "двадцать один"),
    ("Скидка 15%.", "пятнадцать процентов"),
    ("Версия 3.14.", "три целых четырнадцать сотых"),
    ("28.08.2026", "двадцать восьмое августа"),
    ("28.08.2026", "две тысячи двадцать шестого"),
    ("Встреча в 15:30.", "пятнадцать часов тридцать минут"),
    ("Ровно 9:00.", "девять часов"),
    ("на 3-5 позиций", "от трёх до пяти"),
    ("в 1999 году", "тысяча девятьсот девяносто девятого"),
])
def test_numbers(src, want):
    assert want in n(src)


def test_acronym_spellout():
    out = n("Работает через FTP и по TCP.")
    assert "эф ти пи" in out and "ти си пи" in out


def test_acronym_keep_policy():
    tn = TextNormalizer({"normalizer": {"acronyms": {"gost": "keep"}}})
    # unknown 4-letter uppercase would be spelled; 'keep' suppresses it
    assert "ГОСТ" in tn.normalize("Стандарт ГОСТ.").normalized or "гост" in tn.normalize("Стандарт GOST.").normalized.lower()


def test_original_preserved():
    nt = TN.normalize("C++ и 5 рублей")
    assert nt.original == "C++ и 5 рублей"
    assert nt.normalized != nt.original
    assert nt.replacements  # recorded what changed


def test_final_period_added():
    assert n("Без точки").endswith(".")
    assert n("С точкой.") == "С точкой."
    assert n("Вопрос?") == "Вопрос?"


def test_config_lexicon_override():
    tn = TextNormalizer({"normalizer": {"lexicon": {"бот": "бот тестовый"}}})
    assert "бот тестовый" in tn.normalize("Это бот.").normalized


def test_disabled_normalizer_passthrough():
    tn = TextNormalizer({"normalizer": {"enabled": False}})
    assert tn.normalize("C++ и 5").normalized == "C++ и 5"
