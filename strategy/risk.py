"""组合层风控校验（阶段 4）。

在下单前核验目标/实际权重是否守住地基铁律（决策 7/8）：
    - 合计名义敞口 ≤ 2x（MAX_GROSS）         —— error（硬红线）
    - 单边名义敞口 ≤ 1x（MAX_SINGLE_SIDE）   —— error（硬红线）
    - 美元中性：每日净敞口 ≈ 0               —— warning（偏离需人看,非硬错）
    - 权重无 NaN                              —— error

复用 data 层的 ``ValidationReport``,与数据校验同一套"error/warning"语义。
任何违反红线的实现都视为 bug（守住地基）。
"""

from __future__ import annotations

import pandas as pd

from data.validate import ValidationReport
from regime.risk_params import MAX_GROSS, MAX_SINGLE_SIDE

DEFAULT_NEUTRALITY_TOL = 0.05  # 净敞口绝对值超过它即提示"偏离美元中性"
_TOL = 1e-6                     # 浮点容差


def gross_exposure(weights: pd.DataFrame) -> pd.Series:
    """逐日合计名义敞口 = Σ|权重|。"""
    return weights.abs().sum(axis=1).rename("gross")


def net_exposure(weights: pd.DataFrame) -> pd.Series:
    """逐日净敞口 = Σ权重（美元中性应 ≈ 0）。"""
    return weights.sum(axis=1).rename("net")


def long_exposure(weights: pd.DataFrame) -> pd.Series:
    """逐日多腿名义 = Σ正权重。"""
    return weights.clip(lower=0).sum(axis=1).rename("long")


def short_exposure(weights: pd.DataFrame) -> pd.Series:
    """逐日空腿名义 = Σ|负权重|。"""
    return (-weights.clip(upper=0)).sum(axis=1).rename("short")


def check_portfolio(
    weights: pd.DataFrame, neutrality_tol: float = DEFAULT_NEUTRALITY_TOL
) -> ValidationReport:
    """核验权重是否守住红线与美元中性,返回 ValidationReport。"""
    rep = ValidationReport()
    if weights.empty:
        rep.add_warning("空权重,跳过组合风控校验")
        return rep

    if weights.isna().any().any():
        rep.add_error("权重存在 NaN")

    gross = gross_exposure(weights)
    longs = long_exposure(weights)
    shorts = short_exposure(weights)
    net = net_exposure(weights)

    over_gross = gross[gross > MAX_GROSS + _TOL]
    if len(over_gross) > 0:
        rep.add_error(f"合计名义超红线 {MAX_GROSS}x 共 {len(over_gross)} 天,"
                      f"最大 {over_gross.max():.4f}x（例 {over_gross.index[0].date()}）")
    over_long = longs[longs > MAX_SINGLE_SIDE + _TOL]
    over_short = shorts[shorts > MAX_SINGLE_SIDE + _TOL]
    if len(over_long) > 0:
        rep.add_error(f"多腿单边超红线 {MAX_SINGLE_SIDE}x 共 {len(over_long)} 天,"
                      f"最大 {over_long.max():.4f}x")
    if len(over_short) > 0:
        rep.add_error(f"空腿单边超红线 {MAX_SINGLE_SIDE}x 共 {len(over_short)} 天,"
                      f"最大 {over_short.max():.4f}x")

    off_neutral = net[net.abs() > neutrality_tol]
    if len(off_neutral) > 0:
        rep.add_warning(f"偏离美元中性(|净|>{neutrality_tol}) 共 {len(off_neutral)} 天,"
                        f"最大 |净| {off_neutral.abs().max():.4f}")
    return rep


def assert_within_limits(weights: pd.DataFrame) -> None:
    """硬断言：违反红线即抛 ValueError（守住地基,用于流水线关键节点）。"""
    rep = check_portfolio(weights)
    if not rep.ok:
        raise ValueError("组合违反风控红线：\n" + rep.summary())
