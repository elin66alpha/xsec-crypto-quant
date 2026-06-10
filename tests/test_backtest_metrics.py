"""阶段 5 绩效指标测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.metrics import (
    bootstrap_ci,
    calmar_ratio,
    max_drawdown,
    performance_summary,
    realized_beta,
    sharpe_ratio,
    win_rate,
)


def test_drawdown_and_calmar_are_finite_for_nonflat_series():
    returns = pd.Series([0.10, -0.20, 0.05, 0.05])
    assert max_drawdown(returns) == pytest.approx(-0.20)
    assert calmar_ratio(returns, periods_per_year=4) == pytest.approx(
        ((1.10 * 0.80 * 1.05 * 1.05) ** 1 - 1) / 0.20
    )


def test_sharpe_and_win_rate():
    returns = pd.Series([0.01, 0.02, -0.01, 0.03])
    assert sharpe_ratio(returns, periods_per_year=4) > 0
    assert win_rate(returns) == pytest.approx(0.75)


def test_bootstrap_ci_is_reproducible():
    returns = pd.Series([0.01, 0.02, -0.01, 0.03, 0.00])
    ci1 = bootstrap_ci(returns, lambda x: float(x.mean()), n_bootstrap=200, seed=7)
    ci2 = bootstrap_ci(returns, lambda x: float(x.mean()), n_bootstrap=200, seed=7)
    assert ci1 == pytest.approx(ci2)
    assert ci1[0] <= ci1[1]


def test_realized_beta():
    strategy = pd.Series([0.01, 0.02, -0.01, 0.00])
    benchmark = pd.Series([0.02, 0.04, -0.02, 0.00])
    assert realized_beta(strategy, benchmark) == pytest.approx(0.5)


def test_performance_summary_includes_ci_turnover_beta():
    returns = pd.Series([0.01, 0.02, -0.01, 0.03, 0.00])
    turnover = pd.Series([0.0, 1.0, 0.5, 0.2, 0.1])
    benchmark = pd.Series([0.01, 0.01, -0.02, 0.02, 0.00])
    summary = performance_summary(
        returns,
        turnover=turnover,
        benchmark_returns=benchmark,
        periods_per_year=5,
        n_bootstrap=100,
        seed=1,
    )
    for key in ["sharpe_ci_low", "sharpe_ci_high", "avg_turnover", "realized_beta"]:
        assert key in summary
