"""相关性飙升检测测试（阶段 3,风控铁律 2）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from regime.correlation_spike import (
    SpikeConfig,
    correlation_spike_signal,
    detect_correlation_spike,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")


def test_absolute_high_triggers_spike():
    cm = pd.Series([0.2, 0.3, 0.8, 0.9], index=_dates(4))
    # z_threshold 设极大 → 只走绝对阈值这条判据
    sig = correlation_spike_signal(cm, SpikeConfig(abs_threshold=0.7, z_threshold=1e9))
    assert sig.tolist() == [False, False, True, True]


def test_relative_jump_triggers_spike():
    """长期低位基线 + 突然跳高 → 相对 z 触发（即使未到绝对阈值）。"""
    cm = pd.Series([0.1] * 40 + [0.45], index=_dates(41))
    sig = correlation_spike_signal(
        cm, SpikeConfig(abs_threshold=0.9, baseline_window=30, z_threshold=2.0, min_baseline=20)
    )
    assert bool(sig.iloc[-1]) is True
    assert not sig.iloc[:40].any()


def test_no_spike_when_stable_and_low():
    rng = np.random.default_rng(0)
    cm = pd.Series(0.2 + rng.normal(0, 0.01, 80), index=_dates(80))
    sig = correlation_spike_signal(cm, SpikeConfig(abs_threshold=0.7, z_threshold=3.0))
    assert not sig.any()


def test_no_lookahead():
    """篡改未来不改变早期信号（基线用 shift(1) 严格只含过去）。"""
    cm = pd.Series([0.1] * 40 + [0.2] * 10, index=_dates(50))
    cfg = SpikeConfig(baseline_window=30)
    base = correlation_spike_signal(cm, cfg)
    tampered = cm.copy()
    tampered.iloc[45:] = 0.99
    after = correlation_spike_signal(tampered, cfg)
    pd.testing.assert_series_equal(base.iloc[:45], after.iloc[:45])


def test_detect_correlation_spike_bundle():
    rng = np.random.default_rng(1)
    n = 60
    common = rng.normal(0, 0.02, n)
    close = pd.DataFrame(
        {c: 100 * np.cumprod(1 + common + rng.normal(0, 1e-4, n)) for c in ["A", "B", "C"]},
        index=_dates(n),
    )
    out = detect_correlation_spike(close, corr_window=20)
    assert list(out.columns) == ["xs_corr_median", "corr_spike"]
    assert out["corr_spike"].dtype == bool
    # 三币几乎完全同向 → 相关中位数高位 → 末段应触发
    assert bool(out["corr_spike"].iloc[-1])
