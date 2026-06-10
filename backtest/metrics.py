"""回测绩效指标与 bootstrap 置信区间（阶段 5）。"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from factors.quantile_backtest import monotonicity as quantile_monotonicity

TRADING_DAYS_CRYPTO = 365


def _clean_returns(returns: pd.Series) -> pd.Series:
    return pd.Series(returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna()


def equity_curve(returns: pd.Series) -> pd.Series:
    clean = pd.Series(returns, dtype=float).fillna(0.0)
    return (1.0 + clean).cumprod().rename("equity")


def annualized_return(returns: pd.Series, periods_per_year: int = TRADING_DAYS_CRYPTO) -> float:
    clean = _clean_returns(returns)
    if clean.empty:
        return float("nan")
    final = float((1.0 + clean).prod())
    if final <= 0:
        return -1.0
    return float(final ** (periods_per_year / len(clean)) - 1.0)


def annualized_volatility(returns: pd.Series, periods_per_year: int = TRADING_DAYS_CRYPTO) -> float:
    clean = _clean_returns(returns)
    if len(clean) < 2:
        return float("nan")
    return float(clean.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: pd.Series,
    periods_per_year: int = TRADING_DAYS_CRYPTO,
    risk_free_per_period: float = 0.0,
) -> float:
    clean = _clean_returns(returns) - risk_free_per_period
    if len(clean) < 2:
        return float("nan")
    std = float(clean.std(ddof=1))
    if std == 0.0:
        return float("nan")
    return float(clean.mean() / std * np.sqrt(periods_per_year))


def sortino_ratio(returns: pd.Series, periods_per_year: int = TRADING_DAYS_CRYPTO) -> float:
    clean = _clean_returns(returns)
    if len(clean) < 2:
        return float("nan")
    downside = clean[clean < 0.0]
    if len(downside) < 2:
        return float("nan")
    downside_std = float(downside.std(ddof=1))
    if downside_std == 0.0:
        return float("nan")
    return float(clean.mean() / downside_std * np.sqrt(periods_per_year))


def max_drawdown(returns: pd.Series) -> float:
    eq = equity_curve(returns)
    if eq.empty:
        return float("nan")
    drawdown = eq / eq.cummax() - 1.0
    return float(drawdown.min())


def calmar_ratio(returns: pd.Series, periods_per_year: int = TRADING_DAYS_CRYPTO) -> float:
    ann = annualized_return(returns, periods_per_year)
    mdd = max_drawdown(returns)
    if not np.isfinite(ann) or not np.isfinite(mdd) or mdd == 0.0:
        return float("nan")
    return float(ann / abs(mdd))


def win_rate(returns: pd.Series) -> float:
    clean = _clean_returns(returns)
    if clean.empty:
        return float("nan")
    return float((clean > 0.0).mean())


def realized_beta(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    aligned = pd.concat([strategy_returns, benchmark_returns], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return float("nan")
    x = aligned.iloc[:, 1]
    var = float(x.var(ddof=1))
    if var == 0.0:
        return float("nan")
    return float(aligned.iloc[:, 0].cov(x) / var)


def bootstrap_ci(
    values: pd.Series,
    statistic: Callable[[pd.Series], float],
    n_bootstrap: int = 1000,
    ci: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """IID bootstrap 置信区间；用于点估计旁边的保守不确定性显示。"""
    clean = _clean_returns(values)
    if clean.empty:
        return (float("nan"), float("nan"))
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap 必须为正")
    if not 0.0 < ci < 1.0:
        raise ValueError("ci 必须在 (0, 1)")

    rng = np.random.default_rng(seed)
    stats = np.empty(n_bootstrap, dtype=float)
    arr = clean.to_numpy()
    for i in range(n_bootstrap):
        sample = pd.Series(rng.choice(arr, size=len(arr), replace=True))
        stats[i] = statistic(sample)
    alpha = (1.0 - ci) / 2.0
    return (float(np.nanquantile(stats, alpha)), float(np.nanquantile(stats, 1.0 - alpha)))


def performance_summary(
    returns: pd.Series,
    turnover: pd.Series | None = None,
    benchmark_returns: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS_CRYPTO,
    n_bootstrap: int = 1000,
    seed: int = 0,
) -> dict[str, float]:
    """组合绩效摘要。CI 使用 bootstrap，不只报告裸点估计。"""
    summary = {
        "annualized_return": annualized_return(returns, periods_per_year),
        "annualized_volatility": annualized_volatility(returns, periods_per_year),
        "sharpe": sharpe_ratio(returns, periods_per_year),
        "sortino": sortino_ratio(returns, periods_per_year),
        "calmar": calmar_ratio(returns, periods_per_year),
        "max_drawdown": max_drawdown(returns),
        "win_rate": win_rate(returns),
    }
    lo, hi = bootstrap_ci(
        returns,
        lambda x: sharpe_ratio(x, periods_per_year),
        n_bootstrap=n_bootstrap,
        seed=seed,
    )
    summary["sharpe_ci_low"] = lo
    summary["sharpe_ci_high"] = hi
    if turnover is not None:
        summary["avg_turnover"] = float(_clean_returns(turnover).mean())
    if benchmark_returns is not None:
        summary["realized_beta"] = realized_beta(returns, benchmark_returns)
    return summary


def quantile_monotonicity_score(quantile_means: pd.Series) -> float:
    """包装 Phase 2 的分层单调性指标，供回测报告统一调用。"""
    return quantile_monotonicity(quantile_means)
