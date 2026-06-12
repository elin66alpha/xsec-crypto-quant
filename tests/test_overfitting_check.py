"""阶段 5 过拟合检查工具测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.overfitting_check import (
    deflated_sharpe_report,
    expand_parameter_grid,
    monte_carlo_bootstrap,
    parameter_sensitivity,
    select_validation_candidate,
    walk_forward_summary,
)


def test_expand_parameter_grid_counts_trials():
    grid = {"window": [7, 14], "band": [0.0, 0.05, 0.1]}
    combos = expand_parameter_grid(grid)
    assert len(combos) == 6
    assert {"window": 7, "band": 0.0} in combos


def test_parameter_sensitivity_and_selection():
    results = pd.DataFrame(
        {
            "window": [7, 7, 14, 14],
            "band": [0.0, 0.05, 0.0, 0.05],
            "sharpe": [1.0, 1.1, 0.5, 0.6],
        }
    )
    sens = parameter_sensitivity(results, ["window", "band"], "sharpe")
    assert set(sens["parameter"]) == {"window", "band"}
    chosen = select_validation_candidate(results, "sharpe")
    assert chosen["window"] == 7
    assert chosen["band"] == pytest.approx(0.05)


def test_bootstrap_and_dsr_report():
    returns = pd.Series([0.01, 0.02, -0.01, 0.03, 0.00, 0.01])
    boot = monte_carlo_bootstrap(returns, n_bootstrap=100, seed=2, block_length=10)
    assert boot["ci_low"] <= boot["ci_high"]
    assert boot["block_length"] == pytest.approx(10.0)
    dsr = deflated_sharpe_report(returns, parameter_grid={"a": [1, 2], "b": [3, 4]})
    assert dsr.n_trials == 4


def test_dsr_report_can_read_trial_count_from_ledger(tmp_path):
    ledger = tmp_path / "trials.md"
    ledger.write_text(
        "| date | batch | n_combos | note |\n"
        "|---|---|---:|---|\n"
        "| 2026-06-11 | phase2 | 8 | factors |\n"
        "| 2026-06-11 | phase5 | 12 | params |\n",
        encoding="utf-8",
    )
    returns = pd.Series([0.01, 0.02, -0.01, 0.03, 0.00, 0.01])

    dsr = deflated_sharpe_report(
        returns,
        parameter_grid={"ignored": [1]},
        n_trials_from_ledger=ledger,
    )

    assert dsr.n_trials == 20


def test_walk_forward_summary():
    results = pd.DataFrame({"sharpe": [1.0, -0.5, 0.25]})
    summary = walk_forward_summary(results, "sharpe")
    assert summary["n_windows"] == pytest.approx(3.0)
    assert summary["hit_rate_positive"] == pytest.approx(2 / 3)
