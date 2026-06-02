"""订单流特征：CVD / Trade Delta（**仅二次确认，非主力 alpha**）。

‼️ 重要定位（CLAUDE.md + ``docs/data_availability_audit.md``）：

  1. **不是主力因子**：横截面相对强弱（动量/carry/波动率）才是 alpha 来源；订单流
     最多是入场的**二次确认**，且在日级别其增量信息很可能很弱（需阶段 2 用数据证伪，
     而非先验假定）。
  2. **不进历史回测**：逐笔 trades 历史量极大、成本高（TB 级），审计表已将其**降级**为
     "**从现在起录制，仅供未来 paper trading**"。**严禁**用它做历史回测或声称历史 edge。
  3. L2 盘口（OBI/WMP/Depth）已**整体删除**，本模块不实现任何依赖历史盘口的特征。

因此本模块面向"**实时录制的成交流**"，输入是一段已记录的逐笔成交（trades），
而非历史回测面板。所有产出必须在使用处标注"确认项，非历史可回测"。

输入约定：单标的逐笔成交 DataFrame，至少包含列：
    - ``side``   : "buy" / "sell"（taker 方向；buy = 主动买）
    - ``amount`` : 成交量（基础资产，>=0）
索引为成交时间（tz-aware UTC）；若无索引而有 ``timestamp``(ms) 列，先自行转好再传入。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["signed_volume", "cvd", "daily_trade_delta", "CONFIRMATION_ONLY"]

# 模块级常量：任何调用方都应据此把产物标注为"确认项、非历史可回测"
CONFIRMATION_ONLY = True

_SIDE_SIGN = {"buy": 1.0, "sell": -1.0}


def signed_volume(trades: pd.DataFrame) -> pd.Series:
    """逐笔**带符号成交量**：主动买 +amount，主动卖 -amount。

    这是 CVD / delta 的基础量。未知的 side 值视为数据错误，直接报错（不静默吞）。

    Returns
    -------
    Series
        与 ``trades`` 同索引，值为带符号成交量。
    """
    if trades.empty:
        return pd.Series(dtype="float64", index=trades.index)
    side = trades["side"].astype(str).str.lower()
    unknown = set(side.unique()) - set(_SIDE_SIGN)
    if unknown:
        raise ValueError(f"未知 side 值：{sorted(unknown)}（应为 buy/sell）")
    sign = side.map(_SIDE_SIGN)
    return sign * trades["amount"].astype("float64")


def cvd(trades: pd.DataFrame) -> pd.Series:
    """CVD（Cumulative Volume Delta，累计成交量差）：带符号成交量的累计和。

    直觉：CVD 上行 = 主动买持续多于主动卖。**确认项**，非历史可回测。
    """
    sv = signed_volume(trades)
    return sv.cumsum()


def daily_trade_delta(trades: pd.DataFrame) -> pd.Series:
    """把逐笔带符号成交量按日聚合 = 当日**净主动买量**（Trade Delta）。

    便于把录制的成交流落成日度确认信号，与日频主力因子对齐。**确认项**，非历史可回测：
    只能用于"从现在起录制"的数据，不得回填历史声称 edge。

    Returns
    -------
    Series
        index = 日期（UTC，按日归整），值 = 当日净主动买量；当日无成交则为 NaN。
    """
    if trades.empty:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([], name=trades.index.name))
    sv = signed_volume(trades)
    daily = sv.resample("1D").sum(min_count=1)
    return daily.replace([np.inf, -np.inf], np.nan)
