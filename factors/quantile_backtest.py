"""分层回测：按因子打分分 N 层,看未来收益是否单调（阶段 2）。

这是**判断因子真伪最直观的一张图**,比单个 IC 数字更可信：每天把池内标的按因子打分
分成 N 层（默认 5 层）,算各层未来收益,在时间上平均。若因子真有预测力,高分层应
**系统性地**比低分层收益高（Q1→QN 单调递增）；若是噪声,五层会乱成一团。

判读：
    monotonicity —— 层序号与各层平均收益的 Spearman 相关,+1=完美单调递增,0=无序
    spread       —— 顶层减底层的平均收益差（多空腿的雏形）

无前视（决策 9）：因子在 t 已知,与 t 的前向收益（``ic_analysis.forward_return``）配对,
因子只用 t 及以前信息。纯逻辑,可离线单测。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_N_QUANTILES = 5


def assign_quantiles(factor: pd.DataFrame, n_quantiles: int = DEFAULT_N_QUANTILES) -> pd.DataFrame:
    """逐日把截面按因子值分成 1..n_quantiles 层（1=最低分,n=最高分）。

    用截面百分位 rank 分层,抗极值且分布均匀。当日有效标的 < n_quantiles 的行整行 NaN
    （凑不满 N 层无意义）。

    Returns
    -------
    DataFrame
        同形状,值为层号（1..n 的浮点）或 NaN。
    """
    if n_quantiles < 2:
        raise ValueError(f"n_quantiles 必须 >= 2,收到 {n_quantiles}")
    pct = factor.rank(axis=1, pct=True, na_option="keep")  # (0, 1]
    q = np.ceil(pct * n_quantiles).clip(lower=1, upper=n_quantiles)
    insufficient = factor.notna().sum(axis=1) < n_quantiles
    if bool(insufficient.any()):
        q.loc[insufficient] = np.nan
    return q


def quantile_returns(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    n_quantiles: int = DEFAULT_N_QUANTILES,
) -> pd.Series:
    """各层的平均前向收益（先每日按层取均值,再在时间上平均）。

    Returns
    -------
    Series
        index = 层号 1..n,values = 该层平均前向收益,name = "mean_fwd_return"。
    """
    q = assign_quantiles(factor, n_quantiles)
    qa, fr = q.align(forward_returns, join="inner")
    means: dict[int, float] = {}
    for b in range(1, n_quantiles + 1):
        daily_bucket_mean = fr.where(qa == b).mean(axis=1)  # 每日该层均值
        means[b] = float(daily_bucket_mean.mean())          # 时间上平均
    return pd.Series(means, name="mean_fwd_return")


def monotonicity(quantile_means: pd.Series) -> float:
    """层序号与各层平均收益的 Spearman 相关。+1=完美单调递增,-1=单调递减,0=无序。"""
    valid = quantile_means.dropna()
    if len(valid) < 2:
        return float("nan")
    levels = pd.Series(valid.index, index=valid.index, dtype=float)
    return float(levels.corr(valid, method="spearman"))


def long_short_spread(quantile_means: pd.Series) -> float:
    """顶层减底层的平均收益差（多空腿雏形）。"""
    valid = quantile_means.dropna()
    if valid.empty:
        return float("nan")
    return float(valid.iloc[-1] - valid.iloc[0])


def quantile_backtest(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    n_quantiles: int = DEFAULT_N_QUANTILES,
) -> dict[str, object]:
    """一站式分层回测：返回各层均值、单调性、顶-底价差。"""
    means = quantile_returns(factor, forward_returns, n_quantiles)
    return {
        "quantile_means": means,
        "monotonicity": monotonicity(means),
        "spread": long_short_spread(means),
    }
