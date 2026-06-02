"""横截面 IC 分析测试（阶段 2）。验收：前向收益对齐、IC 方向、ICIR、无前视。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.ic_analysis import cross_sectional_ic, forward_return, ic_summary


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


# ----------------------------------------------------------- forward_return


def test_forward_return_horizon_1():
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=_dates(3))
    fr = forward_return(close, horizon=1)
    assert fr["A"].iloc[0] == pytest.approx(0.10)
    assert fr["A"].iloc[1] == pytest.approx(0.10)
    assert np.isnan(fr["A"].iloc[2])  # 末行无未来数据


def test_forward_return_horizon_2_aligned_at_t():
    close = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1]}, index=_dates(4))
    fr = forward_return(close, horizon=2)
    assert fr["A"].iloc[0] == pytest.approx(121.0 / 100.0 - 1)  # t→t+2
    assert fr["A"].iloc[-2:].isna().all()


def test_forward_return_invalid_horizon():
    close = pd.DataFrame({"A": [1.0, 2.0]}, index=_dates(2))
    with pytest.raises(ValueError):
        forward_return(close, horizon=0)


# -------------------------------------------------------- cross_sectional_ic


@pytest.fixture
def factor_and_fwd():
    """5 标的、2 日。Day1 因子与未来收益完全同序(IC=+1)，Day2 完全反序(IC=-1)。"""
    cols = ["A", "B", "C", "D", "E"]
    idx = _dates(2)
    factor = pd.DataFrame([[1, 2, 3, 4, 5], [1, 2, 3, 4, 5]], index=idx, columns=cols)
    fwd = pd.DataFrame(
        [[10, 20, 30, 40, 50], [50, 40, 30, 20, 10]], index=idx, columns=cols
    ).astype(float)
    return factor.astype(float), fwd


def test_ic_perfect_positive_and_negative(factor_and_fwd):
    factor, fwd = factor_and_fwd
    ic = cross_sectional_ic(factor, fwd)
    assert ic.iloc[0] == pytest.approx(1.0)
    assert ic.iloc[1] == pytest.approx(-1.0)
    assert ic.name == "IC_spearman"


def test_ic_min_pairs_masks_thin_day():
    """当日有效标的 < min_pairs → IC 记为 NaN。"""
    cols = ["A", "B", "C", "D", "E"]
    idx = _dates(1)
    factor = pd.DataFrame([[1, 2, np.nan, np.nan, np.nan]], index=idx, columns=cols)
    fwd = pd.DataFrame([[10, 20, 30, 40, 50]], index=idx, columns=cols).astype(float)
    ic = cross_sectional_ic(factor, fwd, min_pairs=5)
    assert np.isnan(ic.iloc[0])


def test_ic_no_lookahead_rows_independent(factor_and_fwd):
    """篡改 Day2 不改变 Day1 的 IC（截面 IC 逐日独立）。"""
    factor, fwd = factor_and_fwd
    base = cross_sectional_ic(factor, fwd)
    tampered = fwd.copy()
    tampered.iloc[1] = [1.0, 1.0, 1.0, 1.0, 999.0]
    after = cross_sectional_ic(factor, tampered)
    assert after.iloc[0] == pytest.approx(base.iloc[0])


# ------------------------------------------------------------- ic_summary


def test_ic_summary_known_values():
    ic = pd.Series([1.0, -1.0], name="IC_spearman")
    s = ic_summary(ic)
    assert s["mean_ic"] == pytest.approx(0.0)
    assert s["std_ic"] == pytest.approx(np.sqrt(2.0))  # ddof=1 of [1,-1]
    assert s["icir"] == pytest.approx(0.0)
    assert s["hit_rate"] == pytest.approx(0.5)
    assert s["t_stat"] == pytest.approx(0.0)
    assert s["n_obs"] == 2


def test_ic_summary_positive_stable():
    ic = pd.Series([0.1, 0.1, 0.1, 0.1])
    s = ic_summary(ic)
    assert s["mean_ic"] == pytest.approx(0.1)
    assert s["std_ic"] == pytest.approx(0.0)
    assert np.isnan(s["icir"])      # std=0 → ICIR 未定义
    assert s["hit_rate"] == pytest.approx(1.0)
    assert s["n_obs"] == 4


def test_ic_summary_empty():
    s = ic_summary(pd.Series([np.nan, np.nan]))
    assert s["n_obs"] == 0
    assert np.isnan(s["mean_ic"])
