"""Funding rate carry 因子（决策 3，一等公民）。

决策 3：永续合约的 funding rate 既是**逐期损益结算项**（回测里要扣/收），也是一个
**carry 因子**——资金费率极高通常意味着多头拥挤、有均值回归倾向（资金费率极高的币
往往被过度做多）。本模块只产出 carry 因子的**原始值**；截面标准化交给
``features.preprocess.cross_sectional_rank``（决策 5）。

> ⚠️ 这个"funding 高 = 拥挤 = 均值回归"只是**假设**，必须在阶段 2 用 IC 验证，
> 不是先验断定。因子值本身不含方向；做多低 funding / 做空高 funding（或反之）的
> 方向由策略层（阶段 4）决定。

数据口径：OKX 永续 funding **每 8 小时结算一次**（一天 3 次，见
``docs/data_availability_audit.md``），而我们的因子是**日频**的。因此先把日内多次
funding 聚合成**日度**（``to_daily_funding``），再在日度上做滚动（``funding_carry``）。

输入约定：宽面板 DataFrame，index = funding 结算时点（UTC 升序），columns = 标的，
values = 该时点 fundingRate（小数，如 0.0001 = 0.01%）。

无前视（决策 9）：聚合与滚动都只用"截至当日"的 funding，不引用未来结算点。
"""

from __future__ import annotations

import pandas as pd

from .cross_sectional import CANDIDATE_WINDOWS, _check_window

__all__ = ["CANDIDATE_WINDOWS", "to_daily_funding", "funding_carry"]


def to_daily_funding(funding: pd.DataFrame, how: str = "sum") -> pd.DataFrame:
    """把 8h（或任意日内频率）funding 聚合成日度面板。

    默认 ``how="sum"``：当日 funding 之和 = 当日**总持仓成本**，对"拥挤度 / 损益"
    最直观（一天付了 3 次资金费，加总即当日净成本）。``how="mean"`` 则取当日均值。

    Parameters
    ----------
    funding : DataFrame
        index = funding 结算时点（UTC），columns = 标的，values = fundingRate。
    how : {"sum", "mean"}

    Returns
    -------
    DataFrame
        index = 日期（UTC，按日归整），columns = 标的，values = 当日聚合 funding。
        当日无任何 funding 记录的格子为 NaN（不补 0，避免把"没数据"当成"零成本"）。
    """
    if funding.empty:
        return funding.copy()
    resampler = funding.resample("1D")
    if how == "sum":
        # min_count=1：当日全 NaN → NaN（而非 0），区分"没数据"与"零 funding"
        daily = resampler.sum(min_count=1)
    elif how == "mean":
        daily = resampler.mean()
    else:
        raise ValueError(f"how 仅支持 'sum'/'mean'，收到 {how!r}")
    return daily


def funding_carry(funding_daily: pd.DataFrame, window: int) -> pd.DataFrame:
    """carry 因子：日度 funding 的滚动均值（当前 / 滚动 funding）。

    ``window=1`` 即"当前当日 funding"；更大的窗口平滑掉单日噪声，反映"持续拥挤度"。
    窗口内需满 ``window`` 个有效日才出值（``min_periods=window``）。

    Parameters
    ----------
    funding_daily : DataFrame
        ``to_daily_funding`` 的输出（日度 funding 宽面板）。
    window : int
        回看天数（候选集见 ``CANDIDATE_WINDOWS``；真正的 N 留待阶段 5 Walk-Forward）。

    Returns
    -------
    DataFrame
        同形状的原始 carry 因子；用 ``preprocess.cross_sectional_rank`` 做截面标准化。
    """
    _check_window(window)
    return funding_daily.rolling(window=window, min_periods=window).mean()
