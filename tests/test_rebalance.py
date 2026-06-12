"""再平衡测试（阶段 4）。验收：无操作带、次日成交时点、换手（决策 4b/9/10）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy.rebalance import (
    apply_no_trade_band,
    to_execution,
    turnover,
    turnover_reduction,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


def test_no_trade_band_keeps_small_changes():
    """目标微调(≤band)不调仓;大变化才调。"""
    tgt = pd.DataFrame(
        {"A": [0.5, 0.52, 0.8], "B": [-0.5, -0.52, -0.8]}, index=_dates(3)
    )
    held = apply_no_trade_band(tgt, band=0.1)
    # day0 建仓到 0.5;day1 变化 0.02≤0.1 → 维持 0.5;day2 变化 0.3>0.1 → 调到 0.8
    assert held["A"].tolist() == pytest.approx([0.5, 0.5, 0.8])
    assert held["B"].tolist() == pytest.approx([-0.5, -0.5, -0.8])


def test_band_zero_follows_target():
    tgt = pd.DataFrame({"A": [0.1, 0.2, 0.3]}, index=_dates(3))
    held = apply_no_trade_band(tgt, band=0.0)
    assert held["A"].tolist() == pytest.approx([0.1, 0.2, 0.3])


def test_delisting_forces_close_despite_band():
    """标的离池(目标 NaN)→ 强制平仓,即使变化在 band 内（决策 10）。"""
    tgt = pd.DataFrame({"A": [0.5, np.nan]}, index=_dates(2))
    held = apply_no_trade_band(tgt, band=1.0)  # band 很大本会维持
    assert held["A"].iloc[1] == pytest.approx(0.0)


def test_band_negative_raises():
    tgt = pd.DataFrame({"A": [0.1]}, index=_dates(1))
    with pytest.raises(ValueError):
        apply_no_trade_band(tgt, band=-0.1)


def test_to_execution_shifts_one_bar():
    """决策 9：收盘后决定的权重下一根 bar 才生效。"""
    w = pd.DataFrame({"A": [0.5, 0.6, 0.7]}, index=_dates(3))
    ex = to_execution(w)
    assert (ex.iloc[0] == 0.0).all()                 # 首日开盘前空仓
    assert ex["A"].iloc[1] == pytest.approx(0.5)     # 第1日决定→第2日持有
    assert ex["A"].iloc[2] == pytest.approx(0.6)


def test_turnover():
    w = pd.DataFrame({"A": [0.5, 0.5, 0.2], "B": [-0.5, -0.5, -0.2]}, index=_dates(3))
    t = turnover(w)
    assert t.iloc[0] == pytest.approx(1.0)   # 从空仓建 |0.5|+|0.5|
    assert t.iloc[1] == pytest.approx(0.0)   # 不变
    assert t.iloc[2] == pytest.approx(0.6)   # |0.2-0.5|+|−0.2−(−0.5)|


def test_turnover_reduction_saves():
    """无操作带应降低平均换手。"""
    rng = np.random.default_rng(0)
    cols = ["A", "B"]
    tgt = pd.DataFrame(0.5 + rng.normal(0, 0.03, (30, 2)), index=_dates(30), columns=cols)
    res = turnover_reduction(tgt, band=0.1)
    assert res["turnover_with_band"] < res["turnover_no_band"]
    assert 0.0 <= res["fraction_saved"] <= 1.0
