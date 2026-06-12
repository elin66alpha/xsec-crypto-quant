"""日频再平衡 + 无操作带 + 信号时点（阶段 4,决策 4/4b/9）。

- 日频再平衡(决策 4)：每天根据最新目标权重调仓。
- 无操作带(决策 4b)：某标的目标权重相对当前持仓的变化 ≤ band 时**不调**,抑制无谓换手、
  省手续费。band 是**可调参数**(默认小值),真正取值留待阶段 5 walk-forward 选并记录原因。
- 信号时点(决策 9)：因子/打分用**收盘后**算,持仓在**下一根 bar 开盘**生效 →
  ``to_execution`` 把权重 shift 一格,杜绝"当根收盘算信号又当根成交"的前视。
- 退市/离池(决策 10)：标的离开动态池(目标为 NaN)时**强制平掉该腿**,不受无操作带保护。

纯逻辑,可离线单测。
"""

from __future__ import annotations

import pandas as pd

DEFAULT_NO_TRADE_BAND = 0.05  # 目标权重变化 ≤ 5%(占净值) 则不调;可调,阶段 5 定


def apply_no_trade_band(
    target_weights: pd.DataFrame, band: float = DEFAULT_NO_TRADE_BAND
) -> pd.DataFrame:
    """对目标权重施加无操作带,返回**实际持仓**权重序列（有状态,逐日推进）。

    规则：逐日逐标的,若 |目标 - 当前持仓| ≤ band 则保持当前持仓(不调),否则采用目标值。
    标的离池(目标 NaN)则强制平为 0(决策 10),不受 band 保护。

    Parameters
    ----------
    target_weights : DataFrame
        index=日期(升序), columns=标的, 目标权重(可含 NaN 表示当日不在池)。
    band : float
        无操作带阈值(权重绝对变化)。band=0 即每日完全跟随目标。
    """
    if band < 0:
        raise ValueError(f"band 必须 >= 0,收到 {band}")
    if target_weights.empty:
        return target_weights.copy()

    cols = target_weights.columns
    held = pd.Series(0.0, index=cols)
    rows: dict[pd.Timestamp, pd.Series] = {}
    for ts in target_weights.index:
        tw = target_weights.loc[ts]
        tw_filled = tw.fillna(0.0)
        # 变化 ≤ band → 维持持仓;否则采用目标
        new = held.where((tw_filled - held).abs() <= band, tw_filled)
        # 离池(目标 NaN)→ 强制平仓,不受 band 保护(决策 10)
        new = new.where(~tw.isna(), 0.0)
        rows[ts] = new
        held = new
    return pd.DataFrame(rows).T.reindex(columns=cols)


def to_execution(weights: pd.DataFrame) -> pd.DataFrame:
    """把"收盘后决定"的权重移到"下一根 bar 生效"（决策 9：shift 一格,首行清空）。

    返回的第 t 行 = 第 t-1 日收盘后决定、在第 t 日开盘起持有的权重。
    """
    if weights.empty:
        return weights.copy()
    shifted = weights.shift(1)
    shifted.iloc[0] = 0.0  # 首日开盘前无持仓
    return shifted


def turnover(weights: pd.DataFrame) -> pd.Series:
    """逐日换手 = Σ|Δ权重|（两边合计,含建/平仓）。首日相对空仓计。"""
    if weights.empty:
        return pd.Series(dtype=float)
    prev = weights.shift(1).fillna(0.0)
    return (weights.fillna(0.0) - prev).abs().sum(axis=1).rename("turnover")


def turnover_reduction(
    target_weights: pd.DataFrame, band: float = DEFAULT_NO_TRADE_BAND
) -> dict[str, float]:
    """对比无操作带前后的平均换手,量化 band 的省换手效果（供调参参考）。"""
    base = float(turnover(target_weights.fillna(0.0)).mean())
    banded = float(turnover(apply_no_trade_band(target_weights, band)).mean())
    saved = (base - banded) / base if base > 0 else 0.0
    return {"turnover_no_band": base, "turnover_with_band": banded, "fraction_saved": saved}
