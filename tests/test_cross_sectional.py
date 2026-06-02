"""横截面因子测试（阶段 1）。验收：动量/波动率/成交量因子 + 无前视（决策 9）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.cross_sectional import (
    CANDIDATE_WINDOWS,
    build_factor_panels,
    momentum,
    realized_volatility,
    volume_change,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


# ---------------------------------------------------------------- momentum


def test_momentum_window_1():
    """每日 +10% → 1 日动量恒为 0.10，首行 NaN。"""
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=_dates(3))
    out = momentum(close, window=1)
    assert np.isnan(out["A"].iloc[0])
    assert out["A"].iloc[1] == pytest.approx(0.10)
    assert out["A"].iloc[2] == pytest.approx(0.10)


def test_momentum_window_2_cumulative():
    """2 日累计收益：121/100 - 1 = 0.21，前两行 NaN。"""
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=_dates(3))
    out = momentum(close, window=2)
    assert out["A"].iloc[:2].isna().all()
    assert out["A"].iloc[2] == pytest.approx(0.21)


def test_momentum_columns_independent():
    """每个标的的动量只依赖自身价格序列，篡改另一列不影响本列。"""
    close = pd.DataFrame(
        {"A": [100.0, 110.0, 121.0], "B": [50.0, 55.0, 60.5]}, index=_dates(3)
    )
    base = momentum(close, window=1)
    tampered = close.copy()
    tampered["B"] = [1.0, 999.0, 7.0]
    after = momentum(tampered, window=1)
    pd.testing.assert_series_equal(base["A"], after["A"])


def test_momentum_nan_propagates():
    """价格缺失（不在池/无数据）→ 该点动量 NaN。"""
    close = pd.DataFrame({"A": [100.0, np.nan, 121.0]}, index=_dates(3))
    out = momentum(close, window=1)
    assert np.isnan(out["A"].iloc[1])
    assert np.isnan(out["A"].iloc[2])  # 由 NaN 上一格传播


# ------------------------------------------------------ realized_volatility


def test_realized_vol_constant_growth_is_zero():
    """恒定日收益（每日同比例增长）→ 对数收益恒定 → 波动率 0。"""
    close = pd.DataFrame({"A": 100.0 * 1.05 ** np.arange(5)}, index=_dates(5))
    out = realized_volatility(close, window=3)
    assert out["A"].iloc[-1] == pytest.approx(0.0, abs=1e-12)


def test_realized_vol_known_value():
    """对数收益 [_, 0.1, 0.3]，window=2 的样本标准差 = sqrt(0.02)。"""
    close = pd.DataFrame(
        {"A": np.exp(np.cumsum([0.0, 0.1, 0.3]))}, index=_dates(3)
    )
    out = realized_volatility(close, window=2)
    assert out["A"].iloc[:2].isna().all()  # 首两行历史不足
    assert out["A"].iloc[2] == pytest.approx(np.sqrt(0.02))


# ----------------------------------------------------------- volume_change


def test_volume_change_constant_is_nan():
    """成交量恒定 → 滚动标准差 0 → z 无定义（NaN）。"""
    vol = pd.DataFrame({"A": [10.0, 10.0, 10.0, 10.0]}, index=_dates(4))
    out = volume_change(vol, window=2)
    assert out["A"].isna().all()


def test_volume_change_known_zscore():
    """volume [10,10,20]，window=2 末点 z = (20-15)/sqrt(50)。"""
    vol = pd.DataFrame({"A": [10.0, 10.0, 20.0]}, index=_dates(3))
    out = volume_change(vol, window=2)
    assert out["A"].iloc[2] == pytest.approx(5.0 / np.sqrt(50.0))


# ---------------------------------------------------------------- 其他


def test_build_factor_panels_keys_and_shape():
    close = pd.DataFrame(
        {"A": np.linspace(100, 120, 40), "B": np.linspace(50, 80, 40)}, index=_dates(40)
    )
    vol = pd.DataFrame(
        {"A": np.linspace(10, 30, 40), "B": np.linspace(5, 25, 40)}, index=_dates(40)
    )
    panels = build_factor_panels(close, vol, window=7)
    assert set(panels) == {"momentum_7", "realized_volatility_7", "volume_change_7"}
    for p in panels.values():
        assert p.shape == close.shape


def test_invalid_window_raises():
    close = pd.DataFrame({"A": [100.0, 110.0]}, index=_dates(2))
    with pytest.raises(ValueError):
        momentum(close, window=0)
    with pytest.raises(ValueError):
        realized_volatility(close, window=-1)


def test_candidate_windows_documented():
    """候选窗口集存在且为正整数升序（真正的 N 留待 Walk-Forward 选）。"""
    assert CANDIDATE_WINDOWS == (7, 14, 30, 60, 90)
    assert list(CANDIDATE_WINDOWS) == sorted(CANDIDATE_WINDOWS)
