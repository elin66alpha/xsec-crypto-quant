"""相关性飙升检测（阶段 3,风控铁律 2 的配套）。

铁律（CLAUDE.md 风控铁律 2）：加密资产相关性在危机时趋近 1,多空对冲会瞬间失效
（如 2022-05 LUNA 崩盘,所有币齐跌,空头腿补不上多头腿的亏）。这是结构性特征,不是 bug。
**一旦检测到全市场横截面相关性中位数飙升,就整体降杠杆甚至空仓**,不得相信对冲仍在保护。

本模块独立于 HMM：它是一道**更直接、更快**的风控开关,与 market_regime 的 HMM 状态
互为补充（risk_params 里会取两者中更保守者）。

检测口径（两条任一触发即视为飙升）：
    - 绝对高位：相关中位数 > ``abs_threshold``
    - 相对飙升：相关中位数相对**过去**基线的 z 分数 > ``z_threshold``

阈值是**可调参数**（与 FDR q 类似）：默认给保守值,真实取值需在真实数据上看分布后再定,
并记录调整原因（决策清单不可中途随意漂移）。

无前视：基线用 ``shift(1)`` 的滚动窗口（严格只含过去）,某日信号不依赖未来。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .market_regime import DEFAULT_CORR_WINDOW, cross_sectional_corr_median


@dataclass(frozen=True)
class SpikeConfig:
    """相关性飙升检测参数（默认偏保守,可调,调整需记录原因）。"""

    abs_threshold: float = 0.7    # 相关中位数绝对高位即危机
    baseline_window: int = 60     # 相对基线的回看窗口
    z_threshold: float = 2.0      # 相对基线的 z 分数阈值
    min_baseline: int = 20        # 基线最少有效点,不足则相对判据不触发


def correlation_spike_signal(
    corr_median: pd.Series, config: SpikeConfig | None = None
) -> pd.Series:
    """由横截面相关中位数序列产出布尔飙升信号（True=触发降杠杆）。

    Parameters
    ----------
    corr_median : Series
        ``market_regime.cross_sectional_corr_median`` 的输出。
    config : SpikeConfig, optional

    Returns
    -------
    Series[bool]
        与输入同索引,name="corr_spike"。
    """
    config = config or SpikeConfig()
    abs_hit = corr_median > config.abs_threshold

    prior = corr_median.shift(1)  # 基线严格只含过去,避免当前值进自己的基线
    min_periods = min(config.min_baseline, config.baseline_window)  # 防 min_periods>window 报错
    base_mean = prior.rolling(config.baseline_window, min_periods=min_periods).mean()
    base_std = prior.rolling(config.baseline_window, min_periods=min_periods).std()
    z = (corr_median - base_mean) / base_std
    rel_hit = (z > config.z_threshold).fillna(False)

    spike = (abs_hit | rel_hit).astype(bool)
    return spike.rename("corr_spike")


def detect_correlation_spike(
    close: pd.DataFrame,
    corr_window: int = DEFAULT_CORR_WINDOW,
    config: SpikeConfig | None = None,
) -> pd.DataFrame:
    """一站式：池内收盘价 → 横截面相关中位数 + 飙升信号。

    Returns
    -------
    DataFrame
        列 ``xs_corr_median`` 与 ``corr_spike``。
    """
    corr_median = cross_sectional_corr_median(close, window=corr_window)
    spike = correlation_spike_signal(corr_median, config)
    return pd.DataFrame({"xs_corr_median": corr_median, "corr_spike": spike})
