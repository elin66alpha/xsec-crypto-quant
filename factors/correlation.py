"""因子间相关剔除：去冗余,保留低相关的精选池（阶段 2）。

动机（CLAUDE.md 阶段 2）：四个高度相关的动量变体等于只有一个因子,却会在多重检验里
重复消耗"试错预算"。因此对通过 FDR 的因子两两算相关,**相关 > 阈值的对只保留 ICIR
更高者**,且最终池 ≤ max_factors（默认 10）。

相关口径：把每个因子面板按 (日期, 标的) 摊平成长序列,在因子维度上算 Spearman 相关
（与 IC 的秩口径一致）。判冗余用**绝对值**——强负相关同样是冗余（反着看是同一信息）。

纯逻辑,可离线单测。
"""

from __future__ import annotations

import pandas as pd

DEFAULT_CORR_THRESHOLD = 0.7
DEFAULT_MAX_FACTORS = 10


def factor_correlation_matrix(
    panels: dict[str, pd.DataFrame], method: str = "spearman"
) -> pd.DataFrame:
    """多个因子面板 → 因子×因子相关矩阵。

    Parameters
    ----------
    panels : dict
        {因子名: 因子面板（index=日期, columns=标的）}。
    method : {"spearman", "pearson", "kendall"}

    Returns
    -------
    DataFrame
        对称相关矩阵,index/columns = 因子名。
    """
    if not panels:
        return pd.DataFrame()
    # future_stack=True 保留全 (日期,标的) 组合以对齐各因子；corr 自动按成对非 NaN 计算
    cols = {name: p.stack(future_stack=True) for name, p in panels.items()}
    long = pd.DataFrame(cols)
    return long.corr(method=method)


def select_low_correlation(
    corr: pd.DataFrame,
    icir_scores: dict[str, float],
    threshold: float = DEFAULT_CORR_THRESHOLD,
    max_factors: int = DEFAULT_MAX_FACTORS,
) -> list[str]:
    """贪心选出低相关精选池：按 |ICIR| 从高到低,逐个保留与已选都不冗余者。

    Parameters
    ----------
    corr : DataFrame
        ``factor_correlation_matrix`` 的输出。
    icir_scores : dict
        {因子名: ICIR}（来自 ``ic_analysis.ic_summary``）。用 |ICIR| 排强弱：
        强负相关因子（反向预测）同样有价值,按绝对强度排序。
    threshold : float
        相关绝对值超过它即视为冗余（默认 0.7）。
    max_factors : int
        最终池上限（默认 10）。

    Returns
    -------
    list[str]
        入选因子名,按 |ICIR| 降序。
    """
    candidates = [n for n in icir_scores if n in corr.columns and pd.notna(icir_scores[n])]
    order = sorted(candidates, key=lambda n: abs(icir_scores[n]), reverse=True)

    kept: list[str] = []
    for name in order:
        if all(abs(corr.loc[name, k]) <= threshold for k in kept):
            kept.append(name)
        if len(kept) >= max_factors:
            break
    return kept
