"""组合层风控校验测试（阶段 4）。验收：红线 error、美元中性 warning。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy.risk import (
    assert_within_limits,
    check_portfolio,
    gross_exposure,
    net_exposure,
    short_exposure,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


def _neutral_2x():
    return pd.DataFrame({"A": [0.5], "B": [0.5], "C": [-0.5], "D": [-0.5]}, index=_dates(1))


def test_exposures():
    w = _neutral_2x()
    assert gross_exposure(w).iloc[0] == pytest.approx(2.0)
    assert net_exposure(w).iloc[0] == pytest.approx(0.0)
    assert short_exposure(w).iloc[0] == pytest.approx(1.0)


def test_clean_portfolio_ok():
    rep = check_portfolio(_neutral_2x())
    assert rep.ok


def test_gross_over_limit_errors():
    w = pd.DataFrame({"A": [0.8], "B": [0.8], "C": [-0.8], "D": [-0.8]}, index=_dates(1))  # gross 3.2
    rep = check_portfolio(w)
    assert not rep.ok
    assert any("合计名义超红线" in e for e in rep.errors)


def test_single_side_over_limit_errors():
    w = pd.DataFrame({"A": [0.7], "B": [0.7], "C": [-0.2]}, index=_dates(1))  # 多腿 1.4 > 1
    rep = check_portfolio(w)
    assert any("多腿单边超红线" in e for e in rep.errors)


def test_off_neutral_warns_not_errors():
    w = pd.DataFrame({"A": [0.6], "B": [0.4], "C": [-0.2]}, index=_dates(1))  # 净 +0.8
    rep = check_portfolio(w, neutrality_tol=0.05)
    assert rep.ok  # 仅 warning,不算 error
    assert any("偏离美元中性" in wn for wn in rep.warnings)


def test_nan_errors():
    w = pd.DataFrame({"A": [np.nan], "B": [0.5]}, index=_dates(1))
    rep = check_portfolio(w)
    assert not rep.ok
    assert any("NaN" in e for e in rep.errors)


def test_assert_within_limits_raises():
    w = pd.DataFrame({"A": [1.5], "B": [-1.5]}, index=_dates(1))  # gross 3, 单边 1.5
    with pytest.raises(ValueError):
        assert_within_limits(w)


def test_assert_within_limits_passes_clean():
    assert_within_limits(_neutral_2x())  # 不抛
