"""阶段 3 真实数据驱动里的新增纯逻辑测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.run_regime_analysis import (
    REGIME_CRISIS,
    gross_multiplier_timeline,
    state_intervals,
    validate_requested_window,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")


def test_gross_multiplier_timeline_uses_more_conservative_spike_path():
    idx = _dates(4)
    regime = pd.Series([0.0, 1.0, 2.0, float("nan")], index=idx)
    spike = pd.Series([False, True, False, False], index=idx)

    out = gross_multiplier_timeline(regime, spike)

    assert out.name == "gross_multiplier"
    assert out.tolist() == [1.0, 0.2, 0.2, 1.0]


def test_state_intervals_splits_contiguous_crisis_runs():
    idx = _dates(6)
    regime = pd.Series([0.0, 2.0, 2.0, 1.0, 2.0, 2.0], index=idx)

    intervals = state_intervals(regime, REGIME_CRISIS)

    assert [(s.date().isoformat(), e.date().isoformat(), d) for s, e, d in intervals] == [
        ("2024-01-02", "2024-01-03", 2),
        ("2024-01-05", "2024-01-06", 2),
    ]


def test_validate_requested_window_rejects_holdout_access():
    with pytest.raises(ValueError, match="holdout"):
        validate_requested_window(
            pd.Timestamp("2025-01-01", tz="UTC"),
            pd.Timestamp("2025-10-01", tz="UTC"),
        )
