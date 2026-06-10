"""横截面多空回测器（阶段 5）。

核心时点约定：
    - ``score`` 是收盘后已知的横截面打分。
    - 目标权重先经过 no-trade band，再由 regime/correlation spike 做最终降杠杆。
    - ``to_execution`` 把权重 shift 一格，表示下一根 bar 开盘才成交。
    - PnL 使用 open-to-open 收益，避免用当根收盘信号在当根收盘成交的前视偏差。

成本约定：
    - 交易成本 = 换手 × (taker_fee + slippage)。
    - funding PnL = -Σ(weight × funding_rate)。正 funding 下，多头付费、空头收取。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategy.portfolio import DEFAULT_QUANTILE, apply_regime_leverage, build_target_weights
from strategy.rebalance import DEFAULT_NO_TRADE_BAND, apply_no_trade_band, to_execution, turnover


@dataclass(frozen=True)
class BacktestConfig:
    """回测参数；Phase 5 walk-forward 负责最终选择这些参数。"""

    quantile: float = DEFAULT_QUANTILE
    no_trade_band: float = DEFAULT_NO_TRADE_BAND
    taker_fee: float = 0.0005
    slippage: float = 0.0005

    @property
    def cost_per_turnover(self) -> float:
        return self.taker_fee + self.slippage


@dataclass(frozen=True)
class BacktestResult:
    """回测结果容器。所有序列都按持仓生效期的起始 open 对齐。"""

    target_weights: pd.DataFrame
    banded_weights: pd.DataFrame
    execution_weights: pd.DataFrame
    asset_returns: pd.DataFrame
    gross_return: pd.Series
    funding_pnl: pd.Series
    trading_cost: pd.Series
    net_return: pd.Series
    equity_curve: pd.Series
    turnover: pd.Series

    def to_frame(self) -> pd.DataFrame:
        """组合层逐期结果表。"""
        return pd.DataFrame(
            {
                "gross_return": self.gross_return,
                "funding_pnl": self.funding_pnl,
                "trading_cost": self.trading_cost,
                "net_return": self.net_return,
                "equity": self.equity_curve,
                "turnover": self.turnover,
            }
        )


def open_to_open_returns(open_prices: pd.DataFrame) -> pd.DataFrame:
    """下一根开盘相对当前开盘的收益，行 t 表示从 open[t] 持有到 open[t+1]。"""
    if open_prices.empty:
        return open_prices.copy()
    returns = open_prices.shift(-1) / open_prices - 1.0
    return returns.replace([np.inf, -np.inf], np.nan)


def _align_like_weights(data: pd.DataFrame | None, weights: pd.DataFrame, fill: float) -> pd.DataFrame:
    if data is None:
        return pd.DataFrame(fill, index=weights.index, columns=weights.columns)
    aligned = data.reindex(index=weights.index, columns=weights.columns)
    return aligned.fillna(fill)


def run_backtest(
    score: pd.DataFrame,
    open_prices: pd.DataFrame,
    funding_rates: pd.DataFrame | None = None,
    regime: pd.Series | None = None,
    corr_spike: pd.Series | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """从横截面打分跑到扣成本净收益。

    Parameters
    ----------
    score
        index=日期, columns=标的；高分做多、低分做空。
    open_prices
        同一标的池的开盘价面板。收益使用 open-to-open。
    funding_rates
        每期 funding rate 面板；缺省视为 0。正值代表多头付费、空头收取。
    regime / corr_spike
        Phase 3 风控输入。缺省视为不降杠杆。
    config
        回测参数。
    """
    config = config or BacktestConfig()
    score, open_prices = score.align(open_prices, join="inner", axis=0)
    score, open_prices = score.align(open_prices, join="inner", axis=1)
    if score.empty:
        empty_series = pd.Series(dtype=float)
        return BacktestResult(
            target_weights=score.copy(),
            banded_weights=score.copy(),
            execution_weights=score.copy(),
            asset_returns=score.copy(),
            gross_return=empty_series,
            funding_pnl=empty_series,
            trading_cost=empty_series,
            net_return=empty_series,
            equity_curve=empty_series,
            turnover=empty_series,
        )

    target = build_target_weights(score, quantile=config.quantile)
    banded = apply_no_trade_band(target, band=config.no_trade_band)
    risk_adjusted = apply_regime_leverage(banded, regime=regime, corr_spike=corr_spike)
    execution = to_execution(risk_adjusted).fillna(0.0)

    asset_returns = open_to_open_returns(open_prices).reindex_like(execution)
    gross_return = (execution * asset_returns.fillna(0.0)).sum(axis=1).rename("gross_return")

    funding = _align_like_weights(funding_rates, execution, fill=0.0)
    funding_pnl = (-(execution * funding).sum(axis=1)).rename("funding_pnl")

    trade_turnover = turnover(execution).rename("turnover")
    trading_cost = (trade_turnover * config.cost_per_turnover).rename("trading_cost")

    net_return = (gross_return + funding_pnl - trading_cost).rename("net_return")
    equity = (1.0 + net_return.fillna(0.0)).cumprod().rename("equity")

    return BacktestResult(
        target_weights=target,
        banded_weights=risk_adjusted,
        execution_weights=execution,
        asset_returns=asset_returns,
        gross_return=gross_return,
        funding_pnl=funding_pnl,
        trading_cost=trading_cost,
        net_return=net_return,
        equity_curve=equity,
        turnover=trade_turnover,
    )
