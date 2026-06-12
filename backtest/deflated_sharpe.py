"""Deflated Sharpe Ratio（阶段 5）。

实现目标是把“试了很多参数组合后最高 Sharpe”打折，而不是把裸 Sharpe 当结论。
这里用 Bailey/Lopez de Prado DSR 的常见近似形式：以试验次数估计噪声最大 Sharpe
门槛，并按偏度/峰度调整 Sharpe 的标准误。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

from .metrics import TRADING_DAYS_CRYPTO, _clean_returns, sharpe_ratio


@dataclass(frozen=True)
class DeflatedSharpeResult:
    sharpe: float
    benchmark_sharpe: float
    z_stat: float
    pvalue: float
    n_trials: int
    n_observations: int
    passed: bool


def expected_max_noise_sharpe(n_trials: int, n_observations: int) -> float:
    """估计 n 次独立试验中纯噪声策略可能出现的最高单期 Sharpe。"""
    if n_trials < 1:
        raise ValueError("n_trials 必须 >= 1")
    if n_observations < 2:
        return float("nan")
    if n_trials == 1:
        return 0.0
    return float(norm.ppf(1.0 - 1.0 / n_trials) / np.sqrt(n_observations - 1))


def deflated_sharpe_ratio(
    returns: pd.Series,
    n_trials: int,
    periods_per_year: int = TRADING_DAYS_CRYPTO,
    alpha: float = 0.05,
) -> DeflatedSharpeResult:
    """返回经多重试验惩罚后的 Sharpe 显著性结果。

    ``sharpe`` 字段为年化 Sharpe；``benchmark_sharpe`` 为同口径的噪声最高门槛。
    显著性检验内部使用单期 Sharpe，避免样本量和年化系数重复计入。
    """
    clean = _clean_returns(returns)
    n = len(clean)
    if n < 3:
        return DeflatedSharpeResult(
            sharpe=float("nan"),
            benchmark_sharpe=float("nan"),
            z_stat=float("nan"),
            pvalue=float("nan"),
            n_trials=n_trials,
            n_observations=n,
            passed=False,
        )

    period_std = float(clean.std(ddof=1))
    if period_std == 0.0:
        period_sr = float("nan")
    else:
        period_sr = float(clean.mean() / period_std)

    sr_star = expected_max_noise_sharpe(n_trials, n)
    skew = float(clean.skew())
    kurt = float(clean.kurtosis() + 3.0)  # pandas 返回 excess kurtosis
    denom = np.sqrt(max(1e-12, 1.0 - skew * period_sr + ((kurt - 1.0) / 4.0) * period_sr**2))
    z_stat = float((period_sr - sr_star) * np.sqrt(n - 1) / denom)
    pvalue = float(1.0 - norm.cdf(z_stat))
    ann_factor = np.sqrt(periods_per_year)
    return DeflatedSharpeResult(
        sharpe=sharpe_ratio(clean, periods_per_year),
        benchmark_sharpe=sr_star * ann_factor,
        z_stat=z_stat,
        pvalue=pvalue,
        n_trials=n_trials,
        n_observations=n,
        passed=bool(pvalue < alpha and period_sr > sr_star),
    )
