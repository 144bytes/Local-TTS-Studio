"""Calibration harness — pure-function tests (no model, no audio)."""
from scripts.calibrate_tts import BENCHMARKS, GRIDS, _combos, _tag, main


def test_grids_are_well_formed():
    for name, grid in GRIDS.items():
        assert set(grid) == {"temperature", "top_p", "repetition_penalty"}, name
        assert all(grid.values())


def test_combos_cover_the_cartesian_product():
    grid = GRIDS["coarse"]
    combos = _combos(grid)
    assert len(combos) == 3 * 2 * 2
    assert {c["temperature"] for c in combos} == set(grid["temperature"])
    # tags must be unique and filename-safe
    tags = {_tag(c) for c in combos}
    assert len(tags) == len(combos)
    assert all("." not in t and "/" not in t for t in tags)


def test_benchmarks_are_russian_and_nonempty():
    assert {"technical", "numbers", "question", "long"} <= set(BENCHMARKS)
    for name, text in BENCHMARKS.items():
        assert text.strip()
        assert any("а" <= ch.lower() <= "я" for ch in text), name


def test_dry_run_renders_nothing(capsys):
    rc = main(["--dry-run", "--grid", "quick", "--texts", "numbers,technical"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "renders=4" in out and "nothing rendered" in out


def test_unknown_benchmark_is_rejected():
    import pytest
    with pytest.raises(SystemExit):
        main(["--dry-run", "--texts", "does-not-exist"])
