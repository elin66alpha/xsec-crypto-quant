"""阶段 5 回测器测试：时点、成本、funding。"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.xsec_runner import BacktestConfig, open_to_open_returns, run_backtest
from regime.market_regime import REGIME_CRISIS, REGIME_LOW_VOL


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")


def test_open_to_open_returns_aligns_period_start():
    prices = pd.DataFrame({"A": [100.0, 110.0, 121.0]}, index=_dates(3))
    ret = open_to_open_returns(prices)
    assert ret["A"].iloc[0] == pytest.approx(0.10)
    assert ret["A"].iloc[1] == pytest.approx(0.10)
    assert pd.isna(ret["A"].iloc[2])


def test_run_backtest_uses_next_open_execution_shift():
    dates = _dates(4)
    cols = ["A", "B", "C", "D"]
    score = pd.DataFrame(
        [[4.0, 3.0, 2.0, 1.0]] * 4,
        index=dates,
        columns=cols,
    )
    # A 上涨、D 下跌；如果首日不 shift 会立即赚钱。正确做法首日空仓。
    prices = pd.DataFrame(
        {
            "A": [100.0, 110.0, 121.0, 133.1],
            "B": [100.0, 100.0, 100.0, 100.0],
            "C": [100.0, 100.0, 100.0, 100.0],
            "D": [100.0, 90.0, 81.0, 72.9],
        },
        index=dates,
    )
    result = run_backtest(
        score,
        prices,
        config=BacktestConfig(quantile=0.25, no_trade_band=0.0, taker_fee=0.0, slippage=0.0),
    )
    assert result.execution_weights.abs().sum(axis=1).iloc[0] == pytest.approx(0.0)
    assert result.net_return.iloc[0] == pytest.approx(0.0)
    assert result.execution_weights["A"].iloc[1] == pytest.approx(1.0)
    assert result.execution_weights["D"].iloc[1] == pytest.approx(-1.0)
    assert result.gross_return.iloc[1] == pytest.approx(0.20)


def test_cost_and_funding_are_deducted_from_returns():
    dates = _dates(3)
    cols = ["A", "B", "C", "D"]
    score = pd.DataFrame([[4, 3, 2, 1]] * 3, index=dates, columns=cols, dtype=float)
    prices = pd.DataFrame(100.0, index=dates, columns=cols)
    funding = pd.DataFrame(0.001, index=dates, columns=cols)
    result = run_backtest(
        score,
        prices,
        funding_rates=funding,
        config=BacktestConfig(quantile=0.25, no_trade_band=0.0, taker_fee=0.001, slippage=0.0),
    )
    # day1 建一多一空，turnover=2，成本=0.002；正 funding 多付空收，美元中性下净 funding≈0。
    assert result.turnover.iloc[1] == pytest.approx(2.0)
    assert result.trading_cost.iloc[1] == pytest.approx(0.002)
    assert result.funding_pnl.iloc[1] == pytest.approx(0.0)
    assert result.net_return.iloc[1] == pytest.approx(-0.002)


def test_regime_deleverage_is_applied_after_band():
    dates = _dates(3)
    cols = ["A", "B", "C", "D"]
    score = pd.DataFrame([[4, 3, 2, 1]] * 3, index=dates, columns=cols, dtype=float)
    prices = pd.DataFrame(100.0, index=dates, columns=cols)
    regime = pd.Series([REGIME_LOW_VOL, REGIME_CRISIS, REGIME_CRISIS], index=dates)
    result = run_backtest(
        score,
        prices,
        regime=regime,
        config=BacktestConfig(quantile=0.25, no_trade_band=0.0, taker_fee=0.0, slippage=0.0),
    )
    # signal day1 的危机权重在 execution day2 生效，总 gross 从 2x 降到 0.4x。
    assert result.banded_weights.abs().sum(axis=1).iloc[1] == pytest.approx(0.4)
    assert result.execution_weights.abs().sum(axis=1).iloc[2] == pytest.approx(0.4)


def test_missing_returns_are_reported_and_force_closed_next_bar():
    dates = _dates(5)
    cols = ["A", "B", "C", "D"]
    score = pd.DataFrame([[4, 3, 2, 1]] * 5, index=dates, columns=cols, dtype=float)
    prices = pd.DataFrame(
        {
            "A": [100.0, 110.0, None, 121.0, 133.1],  # day1 持仓后下一 open 缺失
            "B": [100.0, 100.0, 100.0, 100.0, 100.0],
            "C": [100.0, 100.0, 100.0, 100.0, 100.0],
            "D": [100.0, 90.0, 81.0, 72.9, 65.61],
        },
        index=dates,
    )

    with pytest.warns(RuntimeWarning, match="缺失 open-to-open 收益"):
        result = run_backtest(
            score,
            prices,
            config=BacktestConfig(
                quantile=0.25, no_trade_band=0.0, taker_fee=0.0, slippage=0.0
            ),
        )

    assert result.missing_return_exposure.iloc[1] == pytest.approx(1.0)
    assert result.execution_weights["A"].iloc[1] == pytest.approx(1.0)
    assert result.execution_weights["A"].iloc[2] == pytest.approx(0.0)
    # A 用最后有效价格标记为 0% 收益；D 空头继续从 -10% 中获利。
    assert result.gross_return.iloc[1] == pytest.approx(0.10)
    assert "missing_return_exposure" in result.to_frame().columns


def test_terminal_missing_forward_return_is_not_flagged():
    dates = _dates(3)
    cols = ["A", "B", "C", "D"]
    score = pd.DataFrame([[4, 3, 2, 1]] * 3, index=dates, columns=cols, dtype=float)
    prices = pd.DataFrame(
        {
            "A": [100.0, 110.0, 121.0],
            "B": [100.0, 100.0, 100.0],
            "C": [100.0, 100.0, 100.0],
            "D": [100.0, 90.0, 81.0],
        },
        index=dates,
    )
    result = run_backtest(
        score,
        prices,
        config=BacktestConfig(quantile=0.25, no_trade_band=0.0, taker_fee=0.0, slippage=0.0),
    )
    assert result.missing_return_exposure.sum() == pytest.approx(0.0)
