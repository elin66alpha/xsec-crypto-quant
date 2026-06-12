"""订单流确认项测试（阶段 1）。提醒：CVD/Delta 仅二次确认、非历史可回测。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.orderflow import (
    CONFIRMATION_ONLY,
    cvd,
    daily_trade_delta,
    signed_volume,
)


@pytest.fixture
def trades() -> pd.DataFrame:
    """6 笔逐笔成交，跨两天。"""
    idx = pd.DatetimeIndex(
        [
            "2023-01-01 00:00",
            "2023-01-01 06:00",
            "2023-01-01 12:00",
            "2023-01-02 00:00",
            "2023-01-02 06:00",
            "2023-01-02 12:00",
        ],
        tz="UTC",
        name="datetime",
    )
    return pd.DataFrame(
        {
            "side": ["buy", "sell", "buy", "sell", "sell", "buy"],
            "amount": [1.0, 2.0, 3.0, 1.0, 1.0, 5.0],
        },
        index=idx,
    )


def test_module_marked_confirmation_only():
    assert CONFIRMATION_ONLY is True


def test_signed_volume_signs(trades):
    sv = signed_volume(trades)
    assert sv.iloc[0] == pytest.approx(1.0)   # buy +1
    assert sv.iloc[1] == pytest.approx(-2.0)  # sell -2
    assert sv.iloc[2] == pytest.approx(3.0)


def test_signed_volume_case_insensitive():
    idx = pd.date_range("2023-01-01", periods=2, freq="h", tz="UTC")
    df = pd.DataFrame({"side": ["BUY", "Sell"], "amount": [1.0, 1.0]}, index=idx)
    sv = signed_volume(df)
    assert sv.tolist() == [1.0, -1.0]


def test_signed_volume_unknown_side_raises():
    idx = pd.date_range("2023-01-01", periods=1, freq="h", tz="UTC")
    df = pd.DataFrame({"side": ["liquidation"], "amount": [1.0]}, index=idx)
    with pytest.raises(ValueError):
        signed_volume(df)


def test_cvd_is_cumulative(trades):
    c = cvd(trades)
    # 累计：1, -1, 2, 1, 0, 5
    assert c.tolist() == pytest.approx([1.0, -1.0, 2.0, 1.0, 0.0, 5.0])


def test_daily_trade_delta(trades):
    d = daily_trade_delta(trades)
    assert len(d) == 2
    assert d.iloc[0] == pytest.approx(1.0 - 2.0 + 3.0)   # day1 = 2.0
    assert d.iloc[1] == pytest.approx(-1.0 - 1.0 + 5.0)  # day2 = 3.0


def test_empty_trades():
    empty = pd.DataFrame(
        {"side": pd.Series(dtype=str), "amount": pd.Series(dtype=float)},
        index=pd.DatetimeIndex([], tz="UTC", name="datetime"),
    )
    assert signed_volume(empty).empty
    assert daily_trade_delta(empty).empty


def test_daily_trade_delta_no_inf():
    """聚合结果不应出现 inf。"""
    idx = pd.date_range("2023-01-01", periods=3, freq="6h", tz="UTC")
    df = pd.DataFrame({"side": ["buy", "buy", "sell"], "amount": [2.0, 2.0, 1.0]}, index=idx)
    d = daily_trade_delta(df)
    assert np.isfinite(d.dropna()).all()
