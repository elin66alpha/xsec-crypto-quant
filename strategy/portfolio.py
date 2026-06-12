"""组合构建：分层等权 + 美元中性 + 杠杆约束（阶段 4,决策 6/7/8）。

输入截面打分（strategy.signal）,输出每日**目标权重**：
    - 做多打分最高的一档(分位 quantile),做空最低的一档(决策 6 分层等权)
    - 每条腿内**等权**;多腿名义和 +1、空腿名义和 -1 → **美元中性**(决策 7),
      基础合计名义 = 2x(单边各 1x)
    - 再乘 Regime/相关飙升的杠杆乘子(regime.risk_params),并**强制裁剪到红线**：
      单边 ≤ 1x、合计 ≤ 2x(决策 8,写死,不可放开)

无前视：权重只由"当日已知"的打分决定;打分用收盘后因子,下一根 bar 开盘成交在
rebalance 层落实(决策 9)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from regime.risk_params import MAX_GROSS, MAX_SINGLE_SIDE, effective_gross_multiplier

DEFAULT_QUANTILE = 0.2


def _row_weights(score_row: pd.Series, quantile: float) -> pd.Series:
    """单日：top 分位等权做多(和+1)、bottom 分位等权做空(和-1),其余 0。"""
    valid = score_row.dropna()
    n = len(valid)
    out = pd.Series(0.0, index=score_row.index)
    if n < 2:
        return out  # 不足以构成多空两腿 → 空仓
    n_side = max(1, int(np.floor(n * quantile)))
    n_side = min(n_side, n // 2)  # 防多空两档重叠
    ordered = valid.sort_values(ascending=False)
    longs = ordered.index[:n_side]
    shorts = ordered.index[-n_side:]
    out[longs] = 1.0 / n_side    # 多腿等权,和 = +1
    out[shorts] = -1.0 / n_side  # 空腿等权,和 = -1
    return out


def _n_side(n_assets: int, quantile: float) -> int:
    """给定有效标的数和分位宽度,返回单侧入选/保留数量。"""
    n_side = max(1, int(np.floor(n_assets * quantile)))
    return min(n_side, n_assets // 2)


def _hysteresis_weights(
    score: pd.DataFrame,
    quantile: float,
    exit_quantile: float,
) -> pd.DataFrame:
    """逐日排名缓冲带：入选看 quantile,已持仓跌出 exit_quantile 才剔除。"""
    held_longs: set[str] = set()
    held_shorts: set[str] = set()
    rows: dict[pd.Timestamp, pd.Series] = {}

    for ts in score.index:
        score_row = score.loc[ts]
        valid = score_row.dropna()
        out = pd.Series(0.0, index=score.columns)
        if len(valid) < 2:
            held_longs = set()
            held_shorts = set()
            rows[ts] = out
            continue

        entry_n = _n_side(len(valid), quantile)
        exit_n = _n_side(len(valid), exit_quantile)
        ordered = valid.sort_values(ascending=False)

        entry_longs = set(ordered.index[:entry_n])
        entry_shorts = set(ordered.index[-entry_n:])
        exit_longs = set(ordered.index[:exit_n])
        exit_shorts = set(ordered.index[-exit_n:])

        longs = (held_longs & exit_longs) | entry_longs
        shorts = (held_shorts & exit_shorts) | entry_shorts

        # 若排名穿越到对侧入选区,直接换侧；同一标的不得同时属于两腿。
        longs -= entry_shorts
        shorts -= entry_longs
        overlap = longs & shorts
        if overlap:
            longs -= overlap
            shorts -= overlap

        if longs:
            out[list(longs)] = 1.0 / len(longs)
        if shorts:
            out[list(shorts)] = -1.0 / len(shorts)

        held_longs = longs
        held_shorts = shorts
        rows[ts] = out

    return pd.DataFrame(rows).T.reindex(columns=score.columns)


def build_target_weights(
    score: pd.DataFrame,
    quantile: float = DEFAULT_QUANTILE,
    exit_quantile: float | None = None,
) -> pd.DataFrame:
    """逐日构建美元中性、分层等权的目标权重（基础合计名义 2x）。

    ``exit_quantile`` 为 ``None`` 时保持旧行为：每日只持有 top/bottom ``quantile``。
    传入 ``exit_quantile >= quantile`` 时启用排名缓冲带：新标的必须进入入选档才开仓,
    已持有标的只要仍在更宽的退出档内就保留,以减少分位边界来回横跳带来的换手。

    Returns
    -------
    DataFrame
        同形状;每行多腿和≈+1、空腿和≈-1(净≈0),未选标的为 0。
    """
    if not 0 < quantile <= 0.5:
        raise ValueError(f"quantile 必须在 (0, 0.5],收到 {quantile}")
    if exit_quantile is not None and not quantile <= exit_quantile <= 0.5:
        raise ValueError(
            "exit_quantile 必须满足 quantile <= exit_quantile <= 0.5,"
            f"收到 quantile={quantile}, exit_quantile={exit_quantile}"
        )
    if score.empty:
        return score.copy()
    if exit_quantile is not None:
        return _hysteresis_weights(score, quantile, exit_quantile)
    rows = {ts: _row_weights(score.loc[ts], quantile) for ts in score.index}
    return pd.DataFrame(rows).T.reindex(columns=score.columns)


def _clamp_row(w: pd.Series) -> pd.Series:
    """把单日权重裁剪进红线：单边 ≤ 1x、合计 ≤ 2x（决策 8）。"""
    longs = w[w > 0]
    shorts = w[w < 0]
    long_sum = float(longs.sum())
    short_sum = float(-shorts.sum())
    w = w.copy()
    if long_sum > MAX_SINGLE_SIDE:           # 多腿超单边红线 → 等比缩
        w[w > 0] *= MAX_SINGLE_SIDE / long_sum
    if short_sum > MAX_SINGLE_SIDE:          # 空腿超单边红线 → 等比缩
        w[w < 0] *= MAX_SINGLE_SIDE / short_sum
    gross = float(w.abs().sum())
    if gross > MAX_GROSS:                     # 合计超红线 → 整体等比缩
        w *= MAX_GROSS / gross
    return w


def apply_regime_leverage(
    weights: pd.DataFrame,
    regime: pd.Series | None = None,
    corr_spike: pd.Series | None = None,
) -> pd.DataFrame:
    """对目标权重逐日套用 Regime/相关飙升杠杆乘子,再裁剪到红线内。

    ``regime`` / ``corr_spike`` 缺省视为无降杠杆(乘子 1.0 / 无飙升)。乘子来自
    ``regime.risk_params.effective_gross_multiplier``（HMM 与飙升取更保守者）。
    """
    if weights.empty:
        return weights.copy()
    out = {}
    for ts in weights.index:
        reg = None if regime is None else regime.get(ts, np.nan)
        spike = False if corr_spike is None else bool(corr_spike.get(ts, False))
        mult = effective_gross_multiplier(reg, spike)
        out[ts] = _clamp_row(weights.loc[ts] * mult)
    return pd.DataFrame(out).T.reindex(columns=weights.columns)
