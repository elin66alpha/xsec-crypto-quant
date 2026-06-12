"""横截面日级因子（阶段 1）。

本模块产出**原始因子值**面板；横截面标准化（截面 rank）是独立的一步，交给
``features.preprocess.cross_sectional_rank``（决策 5）。这样"造因子"与"标准化"
两个关注点分离，各自可单测，也方便阶段 2 对原始因子直接算 IC。

实现的三个历史可得因子（见 ``docs/data_availability_audit.md`` 进入阶段 1 的结论）：

    momentum            —— 截面动量：过去 N 天累计收益
    realized_volatility —— 已实现波动率：过去 N 天日对数收益的标准差
    volume_change       —— 成交量变化：成交量相对自身历史的滚动 z-score

输入约定（与 data 层 / preprocess 一致）：宽面板 DataFrame，
index = 日期（UTC 升序），columns = 标的 symbol。close 面板用收盘价，
volume 面板用成交量。NaN（该标的当日不在池 / 无数据）会沿计算自然传播为 NaN，
最终被截面 rank 排除（决策 1：每个时点只用当时真实可交易的标的）。

无前视（决策 9）：所有因子只用"截至当日（含当日收盘）"的数据，用 ``shift`` /
``rolling`` 严格向过去取窗口，不引用任何未来 bar。当日收盘后算出的因子值，
在下一根 bar 开盘成交。

窗口参数 N：**禁止在全量数据上挑最优 N**（CLAUDE.md 阶段 1）。因子做成按 ``window``
参数化的函数，候选窗口集 ``CANDIDATE_WINDOWS`` 仅供阶段 2 做 IC 对比、阶段 5 用
Walk-Forward 正式选 N。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 候选回看窗口（天）。日频、慢变量：从约一周到一季度。
# 真正的 N 由阶段 5 Walk-Forward 在样本外选定，这里只是候选集（禁止全量选参）。
CANDIDATE_WINDOWS: tuple[int, ...] = (7, 14, 30, 60, 90)


def _check_window(window: int) -> None:
    if window < 1:
        raise ValueError(f"window 必须 >= 1，收到 {window}")


def momentum(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """截面动量：过去 ``window`` 天的累计收益 = close_t / close_{t-window} - 1。

    前 ``window`` 行因缺少历史为 NaN。假设：近期相对强势倾向延续（需阶段 2 用 IC 验证）。

    Parameters
    ----------
    close : DataFrame
        index = 日期（UTC 升序），columns = 标的，values = 收盘价。
    window : int
        回看天数。

    Returns
    -------
    DataFrame
        同形状的原始动量因子；用 ``preprocess.cross_sectional_rank`` 做截面标准化。
    """
    _check_window(window)
    return close / close.shift(window) - 1.0


def realized_volatility(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """已实现波动率：过去 ``window`` 天日对数收益的标准差（样本标准差，ddof=1）。

    用对数收益，窗口内需满 ``window`` 个有效收益才出值（``min_periods=window``），
    不在历史不足时给"半截"波动率。波动率本身只是因子取值；做多低波 / 做空高波
    （或反之）的方向由策略层（阶段 4）决定，本模块不预设方向。

    Parameters
    ----------
    close : DataFrame
        收盘价宽面板。
    window : int
        回看天数。

    Returns
    -------
    DataFrame
        同形状的原始已实现波动率因子。
    """
    _check_window(window)
    log_ret = np.log(close).diff()
    return log_ret.rolling(window=window, min_periods=window).std()


def volume_change(volume: pd.DataFrame, window: int) -> pd.DataFrame:
    """成交量变化：成交量相对自身过去 ``window`` 天的滚动 z-score。

    z = (vol_t - 滚动均值) / 滚动标准差。捕捉"该标的当下成交量相对它自己的常态
    是否异常放大"。这是**逐标的时间序列 z-score**（每个币和自己的历史比），随后再用
    截面 rank 把各币的异常度横向排名（CLAUDE.md：成交量 z-score 的截面排名）。

    滚动标准差为 0（窗口内成交量恒定）时该点 z 无定义，置 NaN。

    Parameters
    ----------
    volume : DataFrame
        成交量宽面板（base volume）。
    window : int
        回看天数。

    Returns
    -------
    DataFrame
        同形状的原始成交量 z-score 因子。
    """
    _check_window(window)
    roll = volume.rolling(window=window, min_periods=window)
    mean = roll.mean()
    std = roll.std()
    z = (volume - mean) / std
    # std==0（成交量恒定）→ z 无定义；inf/-inf 一并清成 NaN
    return z.replace([np.inf, -np.inf], np.nan)


def build_factor_panels(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    window: int,
) -> dict[str, pd.DataFrame]:
    """给定单个窗口，一次性产出三个原始因子面板，便于阶段 2 批量算 IC。

    返回 ``{因子名: 面板}``，键含窗口后缀（如 ``momentum_30``），方便和多窗口区分。
    """
    _check_window(window)
    return {
        f"momentum_{window}": momentum(close, window),
        f"realized_volatility_{window}": realized_volatility(close, window),
        f"volume_change_{window}": volume_change(volume, window),
    }
