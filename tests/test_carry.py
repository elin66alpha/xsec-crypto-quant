"""Funding carry 因子测试（阶段 1，决策 3）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.carry import funding_carry, to_daily_funding


@pytest.fixture
def funding_8h() -> pd.DataFrame:
    """2 标的、2 天 × 每天 3 次（8h）的 funding 面板。"""
    idx = pd.date_range("2023-01-01", periods=6, freq="8h", tz="UTC")
    return pd.DataFrame(
        {
            "A": [0.0001, 0.0002, 0.0003, 0.0001, 0.0001, 0.0001],  # day1 和=0.0006
            "B": [0.0010, 0.0010, 0.0010, 0.0005, 0.0005, 0.0005],  # day1 和=0.0030
        },
        index=idx,
    )


def test_to_daily_funding_sum(funding_8h):
    """默认 sum：当日 3 次 funding 加总。"""
    daily = to_daily_funding(funding_8h)
    assert len(daily) == 2
    assert daily["A"].iloc[0] == pytest.approx(0.0006)
    assert daily["B"].iloc[0] == pytest.approx(0.0030)
    assert daily["A"].iloc[1] == pytest.approx(0.0003)


def test_to_daily_funding_mean(funding_8h):
    daily = to_daily_funding(funding_8h, how="mean")
    assert daily["A"].iloc[0] == pytest.approx(0.0002)  # 0.0006/3


def test_to_daily_funding_nan_not_zero():
    """当日全无 funding 记录 → NaN（不补 0，区分'没数据'与'零成本'）。"""
    idx = pd.DatetimeIndex(
        ["2023-01-01 00:00", "2023-01-03 00:00"], tz="UTC"  # 缺 1/2 整天
    )
    f = pd.DataFrame({"A": [0.0001, 0.0002]}, index=idx)
    daily = to_daily_funding(f)
    assert daily.loc["2023-01-02"]["A"] != daily.loc["2023-01-02"]["A"]  # NaN


def test_to_daily_funding_bad_how(funding_8h):
    with pytest.raises(ValueError):
        to_daily_funding(funding_8h, how="median")


def test_funding_carry_window_1_is_identity(funding_8h):
    """window=1：carry 即当日 funding 本身。"""
    daily = to_daily_funding(funding_8h)
    carry = funding_carry(daily, window=1)
    pd.testing.assert_frame_equal(carry, daily)


def test_funding_carry_rolling_mean():
    """window=2 滚动均值，首行 NaN。"""
    idx = pd.date_range("2023-01-01", periods=3, freq="D", tz="UTC")
    daily = pd.DataFrame({"A": [0.001, 0.003, 0.005]}, index=idx)
    carry = funding_carry(daily, window=2)
    assert np.isnan(carry["A"].iloc[0])
    assert carry["A"].iloc[1] == pytest.approx(0.002)  # (0.001+0.003)/2
    assert carry["A"].iloc[2] == pytest.approx(0.004)  # (0.003+0.005)/2


def test_funding_carry_no_lookahead():
    """无前视：篡改未来某日 funding 不改变之前的 carry。"""
    idx = pd.date_range("2023-01-01", periods=4, freq="D", tz="UTC")
    daily = pd.DataFrame({"A": [0.001, 0.002, 0.003, 0.004]}, index=idx)
    base = funding_carry(daily, window=2)
    tampered = daily.copy()
    tampered.loc[tampered.index[3], "A"] = 9.9
    after = funding_carry(tampered, window=2)
    pd.testing.assert_series_equal(base["A"].iloc[:3], after["A"].iloc[:3])
