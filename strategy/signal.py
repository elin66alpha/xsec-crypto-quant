"""横截面打分（阶段 4,决策 5/6 起步：等权合成）。

把阶段 2 筛出的多个因子,合成一个**截面打分**：分越高 = 预期相对越强（该做多）,
分越低 = 预期相对越弱（该做空）。流程：

    1. 每个因子做截面 rank 并**居中**（preprocess.cross_sectional_rank, center=True）,
       使每日截面零均值——天然适配后续美元中性多空（决策 7）。
    2. 用该因子的 **IC 符号**统一方向：IC 为负的因子（值越大未来越弱）乘 -1,
       使所有因子都"高分=预期强"。
    3. **等权**加权平均（决策 6 起步;权重做成参数,默认等权,改权重需记录原因）。

NaN 友好：某标的当日缺某因子,只用其余可用因子求加权平均（不把缺失当 0）。
无前视：全程只在同一日截面内操作 + 用历史 IC 符号,不引用未来（决策 9）。
"""

from __future__ import annotations

from functools import reduce

import numpy as np
import pandas as pd

from features.preprocess import RankConfig, cross_sectional_rank


def signs_from_mean_ic(mean_ic: dict[str, float]) -> dict[str, float]:
    """由各因子的平均 IC 定方向：>0 → +1,<0 → -1,0/NaN → +1（中性默认）。

    方向来自阶段 2 的真实 IC 符号,不是先验猜测。
    """
    out: dict[str, float] = {}
    for name, ic in mean_ic.items():
        out[name] = -1.0 if (ic is not None and np.isfinite(ic) and ic < 0) else 1.0
    return out


def equal_weights(names: list[str]) -> dict[str, float]:
    """等权权重（决策 6 起步）。"""
    if not names:
        return {}
    w = 1.0 / len(names)
    return {n: w for n in names}


def cross_sectional_score(
    factor_panels: dict[str, pd.DataFrame],
    signs: dict[str, float],
    weights: dict[str, float] | None = None,
) -> pd.DataFrame:
    """多因子合成截面打分（居中 rank → 方向统一 → 加权平均）。

    Parameters
    ----------
    factor_panels : dict
        {因子名: 因子原始值面板（index=日期, columns=标的）}。
    signs : dict
        {因子名: +1/-1} 方向（见 ``signs_from_mean_ic``）。
    weights : dict, optional
        {因子名: 权重};缺省等权。

    Returns
    -------
    DataFrame
        同形状的截面打分;每日近似零均值,高分=预期相对强。
    """
    if not factor_panels:
        return pd.DataFrame()
    weights = weights or equal_weights(list(factor_panels))
    ref = next(iter(factor_panels.values()))

    contribs: list[pd.DataFrame] = []
    presents: list[pd.DataFrame] = []
    for name, panel in factor_panels.items():
        w = weights.get(name, 0.0)
        if w == 0.0:
            continue
        ranked = cross_sectional_rank(panel, RankConfig(center=True)) * signs.get(name, 1.0)
        contribs.append(ranked * w)
        presents.append(ranked.notna().astype(float) * w)  # 该格有效时计入的权重

    if not contribs:  # 所有权重为 0
        return pd.DataFrame(np.nan, index=ref.index, columns=ref.columns)

    # add(fill_value=0) 会把缺失值视为 0 → NaN 友好：只按"实际计入的权重和"平均
    def _sum(frames: list[pd.DataFrame]) -> pd.DataFrame:
        return reduce(lambda a, b: a.add(b, fill_value=0.0), frames)

    weighted_sum = _sum(contribs)
    denom = _sum(presents).replace(0.0, np.nan)  # 全缺 → NaN
    return weighted_sum / denom
