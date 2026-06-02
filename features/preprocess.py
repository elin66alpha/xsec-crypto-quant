"""截面 rank 预处理（决策 5 / 决策 11）。

决策 5：每个时点对池内所有标的做**截面 rank**（rank 天然抗极值），作为因子横截面
标准化的起点。决策 11：暂不 winsorize、不做对市值/波动率的中性化回归——记录在案，
将来若改用 z-score 再补。本模块因此只实现"截面 rank"这一种标准化。

输入约定（与 data 层宽面板一致）：DataFrame，index = 日期（UTC 升序），
columns = 标的 symbol，values = 当日原始因子值。NaN 表示该标的当日不在池内 / 无数据，
**不参与当日截面排名**，输出仍为 NaN（决策 1：每个时点只用当时真实可交易的标的集合）。

输出：同形状面板，值为截面 rank（默认归一到 (0, 1] 的百分位）。

无前视（决策 9）：截面 rank 只在**同一日**的标的之间比较，不跨时间聚合，
天然无 look-ahead——某日的 rank 不依赖任何未来数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from loguru import logger

# 截面有效标的数下限：少于该数无法构成有意义的横截面，当日整行置 NaN。
# 这是数据卫生阈值（非 alpha 参数）；动态池 Top N（决策 1，默认 30）远大于它。
DEFAULT_MIN_VALID = 2


@dataclass(frozen=True)
class RankConfig:
    """截面 rank 参数。默认值即决策 5 的"百分位截面 rank"。"""

    method: str = "average"   # 并列处理方式，与 pandas.DataFrame.rank 一致
    ascending: bool = True    # True = 原始值越大 → rank 越高（百分位越接近 1）
    pct: bool = True          # 归一到 (0, 1]，消除池大小差异，便于跨期 / 跨因子比较
    center: bool = False      # True = 再减去当日截面均值，使每行和≈0（多空打分友好）
    min_valid: int = DEFAULT_MIN_VALID


def cross_sectional_rank(panel: pd.DataFrame, config: RankConfig | None = None) -> pd.DataFrame:
    """对宽因子面板逐日做截面 rank（决策 5）。

    Parameters
    ----------
    panel : DataFrame
        index = 日期（UTC 升序），columns = 标的，values = 原始因子值。
    config : RankConfig, optional
        缺省即百分位截面 rank。

    Returns
    -------
    DataFrame
        同形状；每行是该日的截面 rank。``pct=True`` 时取值落在 (0, 1]；
        若同时 ``center=True``，再减去该行均值（每行和≈0，适合直接做多空权重）。
        当日有效（非 NaN）标的数 < ``min_valid`` 的行整行置 NaN。
    """
    config = config or RankConfig()
    if panel.empty:
        return panel.copy()

    ranked = panel.rank(
        axis=1,                 # 沿列方向 = 同一日的标的之间排名
        method=config.method,
        ascending=config.ascending,
        pct=config.pct,
        na_option="keep",       # NaN 保持 NaN，不占名次、不影响其他标的
    )

    # 截面有效标的不足 → 整行置 NaN（排 1~2 个标的无统计意义）
    insufficient = panel.notna().sum(axis=1) < config.min_valid
    if bool(insufficient.any()):
        ranked.loc[insufficient] = float("nan")
        logger.debug(
            "cross_sectional_rank: {} 行截面有效标的 < {}，已整行置 NaN",
            int(insufficient.sum()),
            config.min_valid,
        )

    if config.center:
        # 减去当日截面均值：每行（非 NaN 部分）和≈0，便于直接作美元中性多空权重（决策 7）
        ranked = ranked.sub(ranked.mean(axis=1), axis=0)

    return ranked


def rank_normalize(panel: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    """便捷入口：用关键字覆盖 ``RankConfig`` 默认值后做截面 rank。

    示例：``rank_normalize(panel, center=True)`` 得到每行和≈0 的多空打分。
    """
    return cross_sectional_rank(panel, RankConfig(**kwargs))
