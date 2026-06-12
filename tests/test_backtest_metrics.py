"""阶段 5 绩效指标测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import (
    bootstrap_ci,
    calmar_ratio,
    iid_bootstrap_ci,
    max_drawdown,
    performance_summary,
    realized_beta,
    realized_beta_by_regime,
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


def test_block_bootstrap_ci_wider_than_iid_for_autocorrelated_returns():
    """AR(1) 正收益序列有时间依赖；block CI 应显著宽于 IID CI。"""
    rng = np.random.default_rng(123)
    n = 900
    mean = 0.001
    phi = 0.80
    eps = rng.normal(0.0, 0.01, size=n)
    values = np.empty(n)
    values[0] = mean + eps[0]
    for i in range(1, n):
        values[i] = mean + phi * (values[i - 1] - mean) + eps[i]
    returns = pd.Series(values)

    def statistic(x: pd.Series) -> float:
        return float(x.mean())

    iid = iid_bootstrap_ci(returns, statistic, n_bootstrap=800, seed=4)
    block = bootstrap_ci(returns, statistic, n_bootstrap=800, seed=4, block_length=20)
    iid_width = iid[1] - iid[0]
    block_width = block[1] - block[0]
    assert block_width > iid_width * 1.5


def test_realized_beta():
    strategy = pd.Series([0.01, 0.02, -0.01, 0.00])
    benchmark = pd.Series([0.02, 0.04, -0.02, 0.00])
    assert realized_beta(strategy, benchmark) == pytest.approx(0.5)


def test_realized_beta_by_regime_aligns_and_skips_missing_regime_days():
    idx = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")
    benchmark = pd.Series([0.01, 0.02, 0.03, -0.01, -0.02, -0.03], index=idx)
    strategy = pd.Series([0.005, 0.010, 9.999, 0.01, 0.02, 0.03], index=idx)
    regime = pd.Series([0, 0, None, 1, 1, 1], index=idx)

    betas = realized_beta_by_regime(strategy, benchmark, regime)

    assert set(betas) == {0, 1}
    assert betas[0] == pytest.approx(0.5)
    assert betas[1] == pytest.approx(-1.0)


def test_performance_summary_includes_ci_turnover_beta():
    returns = pd.Series([0.01, 0.02, -0.01, 0.03, 0.00])
    turnover = pd.Series([0.0, 1.0, 0.5, 0.2, 0.1])
    benchmark = pd.Series([0.01, 0.01, -0.02, 0.02, 0.00])
    regime = pd.Series([0, 0, 1, 1, None])
    summary = performance_summary(
        returns,
        turnover=turnover,
        benchmark_returns=benchmark,
        regime=regime,
        periods_per_year=5,
        n_bootstrap=100,
        seed=1,
    )
    for key in [
        "sharpe_ci_low",
        "sharpe_ci_high",
        "bootstrap_block_length",
        "avg_turnover",
        "realized_beta",
        "beta_regime_0",
        "beta_regime_1",
    ]:
        assert key in summary
    assert summary["bootstrap_block_length"] == pytest.approx(20.0)
