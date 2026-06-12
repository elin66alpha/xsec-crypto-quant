"""Trial ledger tests."""

from __future__ import annotations

from backtest.trials import record_trials, total_trials


def test_record_trials_appends_rows_and_returns_cumulative_total(tmp_path):
    ledger = tmp_path / "trials.md"

    assert total_trials(ledger) == 0
    assert record_trials("phase2-grid", 8, "factor windows", path=ledger) == 8
    assert record_trials("phase5-grid", 12, "validation params", path=ledger) == 20
    assert total_trials(ledger) == 20

    text = ledger.read_text(encoding="utf-8")
    assert "| date | batch | n_combos | note |" in text
    assert "phase2-grid" in text
    assert "phase5-grid" in text


def test_record_trials_sanitizes_markdown_cells(tmp_path):
    ledger = tmp_path / "nested" / "trials.md"

    assert record_trials("bad|batch", 3, "line\nbreak", path=ledger) == 3
    text = ledger.read_text(encoding="utf-8")
    assert "bad/batch" in text
    assert "line break" in text
