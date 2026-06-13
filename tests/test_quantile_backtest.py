"""分层回测测试（阶段 2）。验收：均分层、单调性、顶-底价差。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.quantile_backtest import (
    assign_quantiles,
    daily_long_short_spread,
    long_short_spread,
    monotonicity,
    quantile_backtest,
    quantile_returns,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


def test_assign_quantiles_even_split():
    """10 标的、值 1..10、5 层 → 每层 2 个：[1,1,2,2,3,3,4,4,5,5]。"""
    cols = [f"S{i}" for i in range(10)]
    factor = pd.DataFrame([list(range(1, 11))], index=_dates(1), columns=cols).astype(float)
    q = assign_quantiles(factor, n_quantiles=5)
    assert q.iloc[0].tolist() == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]


def test_assign_quantiles_insufficient_row_nan():
    cols = [f"S{i}" for i in range(4)]
    factor = pd.DataFrame([[1.0, 2.0, 3.0, 4.0]], index=_dates(1), columns=cols)
    q = assign_quantiles(factor, n_quantiles=5)  # 仅 4 个 < 5 层
    assert q.iloc[0].isna().all()


def test_assign_quantiles_bad_n():
    factor = pd.DataFrame([[1.0, 2.0]], index=_dates(1), columns=["A", "B"])
    with pytest.raises(ValueError):
        assign_quantiles(factor, n_quantiles=1)


@pytest.fixture
def monotonic_case():
    """因子值越高、未来收益越高 → 应单调递增。10 标的、20 天。"""
    cols = [f"S{i}" for i in range(10)]
    idx = _dates(20)
    base = np.tile(np.arange(1, 11, dtype=float), (20, 1))
    factor = pd.DataFrame(base, index=idx, columns=cols)
    # 前向收益 = 因子值 × 0.01 + 小噪声（高分→高收益）
    rng = np.random.default_rng(0)
    fwd = pd.DataFrame(base * 0.01 + rng.normal(0, 0.0005, base.shape), index=idx, columns=cols)
    return factor, fwd


def test_quantile_returns_monotonic(monotonic_case):
    factor, fwd = monotonic_case
    means = quantile_returns(factor, fwd, n_quantiles=5)
    assert len(means) == 5
    assert means.iloc[-1] > means.iloc[0]          # 顶层 > 底层
    assert means.is_monotonic_increasing           # 逐层递增
    assert monotonicity(means) == pytest.approx(1.0)
    assert long_short_spread(means) > 0


def test_quantile_returns_reversed():
    """因子值越高、未来收益越低 → 单调递减,monotonicity≈-1。"""
    cols = [f"S{i}" for i in range(10)]
    idx = _dates(10)
    base = np.tile(np.arange(1, 11, dtype=float), (10, 1))
    factor = pd.DataFrame(base, index=idx, columns=cols)
    fwd = pd.DataFrame(-base * 0.01, index=idx, columns=cols)
    means = quantile_returns(factor, fwd, n_quantiles=5)
    assert monotonicity(means) == pytest.approx(-1.0)
    assert long_short_spread(means) < 0


def test_quantile_backtest_bundle(monotonic_case):
    factor, fwd = monotonic_case
    res = quantile_backtest(factor, fwd, n_quantiles=5)
    assert set(res) == {"quantile_means", "monotonicity", "spread"}
    assert res["monotonicity"] == pytest.approx(1.0)


def test_daily_long_short_spread_series(monotonic_case):
    """每日 top−bottom 价差：单调正向案例下应逐日为正，时间均值≈标量 spread。"""
    factor, fwd = monotonic_case
    daily = daily_long_short_spread(factor, fwd, n_quantiles=5)
    assert daily.name == "ls_spread"
    assert (daily.dropna() > 0).all()  # 顶层每日均 > 底层
    means = quantile_returns(factor, fwd, n_quantiles=5)
    # 日度价差的时间均值应与"先时间平均再相减"的标量 spread 一致
    assert daily.mean() == pytest.approx(long_short_spread(means), rel=1e-6)


def test_monotonicity_empty():
    assert np.isnan(monotonicity(pd.Series([1.0])))
