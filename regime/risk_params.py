"""Regime → 风控参数（阶段 3）。

把市场状态（HMM regime）与相关性飙升信号,翻译成**杠杆乘子**,并在任何情况下都
**再次裁剪到风控红线内**：单边 ≤ 1x、合计名义 ≤ 2x（决策 8 / 风控铁律 1,写死,不可放开）。

铁律落地（CLAUDE.md 阶段 3）：
    - 高波动危机 regime → 杠杆乘子大幅下调（默认 0.2）。
    - 相关性飙升（correlation_spike）→ **强制**降到危机级乘子,即使 HMM 尚未判危机
      （两道防线取更保守者,不单靠 HMM）。
    - 乘子作用后仍须满足单边 ≤ 1x、合计 ≤ 2x。
"""

from __future__ import annotations

import numpy as np

from .market_regime import REGIME_CRISIS, REGIME_LOW_VOL, REGIME_TREND

# regime → 杠杆乘子（CLAUDE.md 阶段 3 默认值）
REGIME_RISK_PARAMS: dict[int, dict[str, float]] = {
    REGIME_LOW_VOL: {"gross_leverage_multiplier": 1.0},  # 低波动
    REGIME_TREND: {"gross_leverage_multiplier": 1.0},    # 趋势
    REGIME_CRISIS: {"gross_leverage_multiplier": 0.2},   # 高波动危机：大幅降杠杆
}

# 风控红线（决策 8,写死,不可放开）
MAX_SINGLE_SIDE = 1.0   # 单边名义 ≤ 账户净值 1x
MAX_GROSS = 2.0         # 多空合计名义 ≤ 2x
CRISIS_MULTIPLIER = REGIME_RISK_PARAMS[REGIME_CRISIS]["gross_leverage_multiplier"]

# regime 未知（如扩展窗口预热期 NaN）时的默认乘子：取正常 1.0（预热不是已检测到的危机）
DEFAULT_MULTIPLIER = 1.0


def gross_multiplier_for_regime(regime: float | int | None) -> float:
    """regime 标签 → 杠杆乘子；未知/NaN 用 ``DEFAULT_MULTIPLIER``。"""
    if regime is None or (isinstance(regime, float) and np.isnan(regime)):
        return DEFAULT_MULTIPLIER
    params = REGIME_RISK_PARAMS.get(int(regime))
    return params["gross_leverage_multiplier"] if params else DEFAULT_MULTIPLIER


def effective_gross_multiplier(regime: float | int | None, corr_spike: bool) -> float:
    """综合 HMM regime 与相关性飙升,取**更保守**的杠杆乘子。

    相关性飙升强制降到危机级乘子（不单靠 HMM,风控铁律 2）。
    """
    mult = gross_multiplier_for_regime(regime)
    if corr_spike:
        mult = min(mult, CRISIS_MULTIPLIER)
    return mult


def clamp_gross(gross_nominal: float) -> float:
    """把合计名义敞口裁剪到 ≤ MAX_GROSS（红线,写死）。"""
    return float(min(max(gross_nominal, 0.0), MAX_GROSS))


def clamp_single_side(single_side_nominal: float) -> float:
    """把单边名义敞口裁剪到 ≤ MAX_SINGLE_SIDE（红线,写死）。"""
    return float(min(max(single_side_nominal, 0.0), MAX_SINGLE_SIDE))


def apply_regime_leverage(
    base_gross: float,
    regime: float | int | None,
    corr_spike: bool = False,
) -> float:
    """对目标合计杠杆套用 regime/飙升乘子,再裁剪到红线内,返回最终合计杠杆。

    例：base_gross=2.0,危机 regime → 2.0×0.2=0.4;base_gross=3.0,正常 → 裁剪到 2.0。
    """
    mult = effective_gross_multiplier(regime, corr_spike)
    return clamp_gross(base_gross * mult)
