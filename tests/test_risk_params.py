"""Regime → 风控参数测试（阶段 3）。验收：降杠杆乘子 + 红线裁剪（决策 8）。"""

from __future__ import annotations

import numpy as np

from regime.market_regime import REGIME_CRISIS, REGIME_LOW_VOL, REGIME_TREND
from regime.risk_params import (
    MAX_GROSS,
    MAX_SINGLE_SIDE,
    apply_regime_leverage,
    clamp_gross,
    clamp_single_side,
    effective_gross_multiplier,
    gross_multiplier_for_regime,
)


def test_multiplier_by_regime():
    assert gross_multiplier_for_regime(REGIME_LOW_VOL) == 1.0
    assert gross_multiplier_for_regime(REGIME_TREND) == 1.0
    assert gross_multiplier_for_regime(REGIME_CRISIS) == 0.2


def test_multiplier_unknown_defaults():
    assert gross_multiplier_for_regime(None) == 1.0
    assert gross_multiplier_for_regime(float("nan")) == 1.0


def test_corr_spike_forces_crisis_multiplier():
    """相关性飙升强制降到危机级,即使 HMM 判低波。"""
    assert effective_gross_multiplier(REGIME_LOW_VOL, corr_spike=True) == 0.2
    assert effective_gross_multiplier(REGIME_TREND, corr_spike=True) == 0.2
    # 无飙升时按 regime
    assert effective_gross_multiplier(REGIME_LOW_VOL, corr_spike=False) == 1.0


def test_clamps_enforce_hard_limits():
    assert clamp_gross(3.0) == MAX_GROSS          # 超红线 → 2.0
    assert clamp_gross(1.5) == 1.5
    assert clamp_gross(-1.0) == 0.0
    assert clamp_single_side(2.5) == MAX_SINGLE_SIDE  # 单边超红线 → 1.0


def test_apply_regime_leverage():
    # 危机：2.0 × 0.2 = 0.4
    assert apply_regime_leverage(2.0, REGIME_CRISIS) == 0.4
    # 正常但目标过高：3.0 → 裁剪到 2.0（红线）
    assert apply_regime_leverage(3.0, REGIME_LOW_VOL) == MAX_GROSS
    # 飙升强制危机级：2.0 × 0.2 = 0.4
    assert apply_regime_leverage(2.0, REGIME_TREND, corr_spike=True) == 0.4
    # 预热期未知 regime → 正常乘子,仍受红线
    assert apply_regime_leverage(2.0, float("nan")) == 2.0


def test_apply_never_exceeds_gross_limit():
    """任意输入下,最终合计杠杆都 ≤ MAX_GROSS。"""
    for base in [0.0, 1.0, 2.0, 5.0, 100.0]:
        for reg in [REGIME_LOW_VOL, REGIME_TREND, REGIME_CRISIS, float("nan")]:
            for spike in [True, False]:
                out = apply_regime_leverage(base, reg, corr_spike=spike)
                assert 0.0 <= out <= MAX_GROSS
                assert not np.isnan(out)
