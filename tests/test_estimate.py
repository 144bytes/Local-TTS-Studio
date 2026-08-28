from app.config import DEFAULTS
from app.estimate import _clock, metrics


def test_counts_words():
    m = metrics("Привет мир. Ещё три слова тут.", DEFAULTS, "обычный")
    assert m["words"] == 6


def test_paragraph_pause_time_added():
    m = metrics("слово " * 130 + "\n\n" + "слово " * 130, DEFAULTS, "обычный")
    assert m["pause_seconds"] >= DEFAULTS["pauses"]["paragraph_ms"] / 1000 * 0.7
    assert m["estimated_seconds"] > 90


def test_empty():
    m = metrics("", DEFAULTS, "обычный")
    assert m["words"] == 0 and m["estimated_seconds"] == 0.0


def test_speed_slider_shortens():
    fast = metrics("слово " * 100, DEFAULTS, "обычный", speed=1.3)["estimated_seconds"]
    slow = metrics("слово " * 100, DEFAULTS, "обычный", speed=0.8)["estimated_seconds"]
    assert slow > fast


def test_normalized_preview_present():
    m = metrics("Цена 5 рублей.", DEFAULTS, "обычный")
    assert "пять" in m["normalized_preview"]


def test_clock():
    assert _clock(0) == "0:00" and _clock(65) == "1:05" and _clock(3725) == "1:02:05"
