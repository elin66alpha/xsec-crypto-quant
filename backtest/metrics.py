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


def realized_beta_by_regime(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    regime: pd.Series,
) -> dict[int, float]:
    """Compute realized beta separately within each non-missing regime label."""
    aligned = pd.concat(
        {
            "strategy": strategy_returns,
            "benchmark": benchmark_returns,
            "regime": regime,
        },
        axis=1,
        join="inner",
    ).dropna()
    if aligned.empty:
        return {}

    betas: dict[int, float] = {}
    for regime_value, group in aligned.groupby("regime"):
        betas[int(regime_value)] = realized_beta(group["strategy"], group["benchmark"])
    return betas


def _validate_bootstrap_args(n_bootstrap: int, ci: float) -> None:
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap 必须为正")
    if not 0.0 < ci < 1.0:
        raise ValueError("ci 必须在 (0, 1)")


def iid_bootstrap_ci(
    values: pd.Series,
    statistic: Callable[[pd.Series], float],
    n_bootstrap: int = 1000,
    ci: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """IID bootstrap 置信区间；仅作与 block bootstrap 对照。

    日收益常有自相关与波动率聚集,IID 重采样会打散这种结构,通常低估 Sharpe/均值统计量
    的不确定性。阶段 5 的正式 CI 默认使用 ``bootstrap_ci`` 的 circular block bootstrap。
    """
    clean = _clean_returns(values)
    if clean.empty:
        return (float("nan"), float("nan"))
    _validate_bootstrap_args(n_bootstrap, ci)

    rng = np.random.default_rng(seed)
    stats = np.empty(n_bootstrap, dtype=float)
    arr = clean.to_numpy()
    for i in range(n_bootstrap):
        sample = pd.Series(rng.choice(arr, size=len(arr), replace=True))
        stats[i] = statistic(sample)
    alpha = (1.0 - ci) / 2.0
    return (float(np.nanquantile(stats, alpha)), float(np.nanquantile(stats, 1.0 - alpha)))


def _circular_block_sample(arr: np.ndarray, rng: np.random.Generator, block_length: int) -> np.ndarray:
    """从一维数组抽 circular fixed-length blocks,拼到原样本长度。"""
    n = int(len(arr))
    effective_block = min(block_length, n)
    n_blocks = int(np.ceil(n / effective_block))
    starts = rng.integers(0, n, size=n_blocks)
    offsets = np.arange(effective_block)
    idx = (starts[:, None] + offsets[None, :]) % n
    sample: np.ndarray = arr[idx.ravel()[:n]]
    return sample


def bootstrap_ci(
    values: pd.Series,
    statistic: Callable[[pd.Series], float],
    n_bootstrap: int = 1000,
    ci: float = 0.95,
    seed: int = 0,
    block_length: int = 20,
) -> tuple[float, float]:
    """Circular block bootstrap 置信区间,用于保留收益序列的时间依赖结构。

    日收益通常存在自相关和波动率聚集；若用 IID bootstrap 打散顺序,Sharpe/均值统计量的
    CI 往往过窄。这里用固定长度 circular block bootstrap：随机抽取连续块,到序列末尾
    时从开头环绕。``block_length`` 是显式研究参数,建议从 10--20 天起步并在报告中记录。
    """
    clean = _clean_returns(values)
    if clean.empty:
        return (float("nan"), float("nan"))
    _validate_bootstrap_args(n_bootstrap, ci)
    if block_length <= 0:
        raise ValueError("block_length 必须为正")

    rng = np.random.default_rng(seed)
    stats = np.empty(n_bootstrap, dtype=float)
    arr = clean.to_numpy()
    for i in range(n_bootstrap):
        sample = pd.Series(_circular_block_sample(arr, rng, block_length))
        stats[i] = statistic(sample)
    alpha = (1.0 - ci) / 2.0
    return (float(np.nanquantile(stats, alpha)), float(np.nanquantile(stats, 1.0 - alpha)))


def performance_summary(
    returns: pd.Series,
    turnover: pd.Series | None = None,
    benchmark_returns: pd.Series | None = None,
    regime: pd.Series | None = None,
    periods_per_year: int = TRADING_DAYS_CRYPTO,
    n_bootstrap: int = 1000,
    seed: int = 0,
    bootstrap_block_length: int = 20,
) -> dict[str, float]:
    """组合绩效摘要。Sharpe CI 使用 circular block bootstrap，不只报告裸点估计。"""
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
        block_length=bootstrap_block_length,
    )
    summary["sharpe_ci_low"] = lo
    summary["sharpe_ci_high"] = hi
    summary["bootstrap_block_length"] = float(bootstrap_block_length)
    if turnover is not None:
        summary["avg_turnover"] = float(_clean_returns(turnover).mean())
    if benchmark_returns is not None:
        summary["realized_beta"] = realized_beta(returns, benchmark_returns)
        if regime is not None:
            for regime_value, beta in realized_beta_by_regime(
                returns, benchmark_returns, regime
            ).items():
                summary[f"beta_regime_{regime_value}"] = beta
    return summary


def quantile_monotonicity_score(quantile_means: pd.Series) -> float:
    """包装 Phase 2 的分层单调性指标，供回测报告统一调用。"""
    return quantile_monotonicity(quantile_means)
