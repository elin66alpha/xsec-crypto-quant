"""组合构建测试（阶段 4）。验收：分层等权、美元中性、杠杆红线（决策 6/7/8）。"""

from __future__ import annotations

import pandas as pd
import pytest

from regime.market_regime import REGIME_CRISIS, REGIME_LOW_VOL
from strategy.portfolio import apply_regime_leverage, build_target_weights
from strategy.rebalance import turnover


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


def test_build_target_weights_dollar_neutral_and_equal():
    cols = [f"S{i}" for i in range(10)]
    score = pd.DataFrame([list(range(10))], index=_dates(1), columns=cols).astype(float)
    w = build_target_weights(score, quantile=0.2).iloc[0]
    # 10 标的 × 0.2 → 每腿 2 个
    assert (w > 0).sum() == 2 and (w < 0).sum() == 2
    assert w[w > 0].sum() == pytest.approx(1.0)    # 多腿名义 +1
    assert w[w < 0].sum() == pytest.approx(-1.0)   # 空腿名义 -1
    assert w.sum() == pytest.approx(0.0)           # 美元中性
    assert w.abs().sum() == pytest.approx(2.0)     # 合计 2x
    # 最高分做多、最低分做空,等权
    assert w["S9"] == pytest.approx(0.5) and w["S0"] == pytest.approx(-0.5)


def test_insufficient_names_flat():
    score = pd.DataFrame([[1.0]], index=_dates(1), columns=["A"])
    w = build_target_weights(score).iloc[0]
    assert (w == 0).all()


def test_invalid_quantile():
    score = pd.DataFrame([[1.0, 2.0]], index=_dates(1), columns=["A", "B"])
    with pytest.raises(ValueError):
        build_target_weights(score, quantile=0.8)
    with pytest.raises(ValueError):
        build_target_weights(score, quantile=0.2, exit_quantile=0.1)
    with pytest.raises(ValueError):
        build_target_weights(score, quantile=0.2, exit_quantile=0.6)


def test_exit_quantile_none_matches_legacy_output():
    cols = [f"S{i}" for i in range(10)]
    score = pd.DataFrame(
        [list(range(10)), list(range(9, -1, -1))],
        index=_dates(2),
        columns=cols,
    ).astype(float)
    legacy = build_target_weights(score, quantile=0.2)
    explicit_none = build_target_weights(score, quantile=0.2, exit_quantile=None)
    pd.testing.assert_frame_equal(explicit_none, legacy)


def test_rank_hysteresis_reduces_boundary_turnover():
    """边界标的在 top2/top3 间震荡：缓冲带保留持仓,减少反复进出。"""
    cols = list("ABCDEFGHIJ")
    score = pd.DataFrame(
        [
            [10, 8, 9, 7, 6, 5, 4, 3, 2, 1],
            [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
            [10, 8, 9, 7, 6, 5, 4, 3, 2, 1],
            [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
        ],
        index=_dates(4),
        columns=cols,
        dtype=float,
    )
    legacy = build_target_weights(score, quantile=0.2)
    buffered = build_target_weights(score, quantile=0.2, exit_quantile=0.3)

    assert legacy["B"].tolist() == pytest.approx([0.0, 0.5, 0.0, 0.5])
    assert legacy["C"].tolist() == pytest.approx([0.5, 0.0, 0.5, 0.0])
    assert buffered["B"].iloc[1:].tolist() == pytest.approx([1 / 3, 1 / 3, 1 / 3])
    assert buffered["C"].iloc[1:].tolist() == pytest.approx([1 / 3, 1 / 3, 1 / 3])
    assert turnover(buffered).iloc[1:].sum() < turnover(legacy).iloc[1:].sum()


def test_rank_hysteresis_preserves_neutrality_and_hard_limits():
    cols = list("ABCDEFGHIJ")
    score = pd.DataFrame(
        [
            [10, 8, 9, 7, 6, 5, 4, 3, 2, 1],
            [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
            [10, 8, 9, 7, 6, 5, 4, 3, 2, 1],
        ],
        index=_dates(3),
        columns=cols,
        dtype=float,
    )
    buffered = build_target_weights(score, quantile=0.2, exit_quantile=0.3)
    for _, row in buffered.iterrows():
        assert row[row > 0].sum() == pytest.approx(1.0)
        assert row[row < 0].sum() == pytest.approx(-1.0)
        assert row.sum() == pytest.approx(0.0)
        assert row.abs().sum() <= 2.0 + 1e-9


def test_rank_hysteresis_drops_nan_symbol_even_if_in_exit_band():
    cols = list("ABCDEFGHIJ")
    score = pd.DataFrame(
        [
            [10, 8, 9, 7, 6, 5, 4, 3, 2, 1],
            [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
            [10, 9, None, 8, 7, 6, 5, 4, 3, 2],
        ],
        index=_dates(3),
        columns=cols,
        dtype=float,
    )
    buffered = build_target_weights(score, quantile=0.2, exit_quantile=0.3)
    assert buffered["C"].iloc[1] > 0.0
    assert buffered["C"].iloc[2] == pytest.approx(0.0)


def test_apply_regime_leverage_crisis_scales_down():
    cols = [f"S{i}" for i in range(10)]
    score = pd.DataFrame([list(range(10))] * 2, index=_dates(2), columns=cols).astype(float)
    w = build_target_weights(score, quantile=0.2)
    regime = pd.Series([REGIME_LOW_VOL, REGIME_CRISIS], index=_dates(2))
    scaled = apply_regime_leverage(w, regime=regime)
    # 低波：合计仍 2x；危机：×0.2 → 0.4x
    assert scaled.iloc[0].abs().sum() == pytest.approx(2.0)
    assert scaled.iloc[1].abs().sum() == pytest.approx(0.4)
    # 仍美元中性
    assert scaled.iloc[1].sum() == pytest.approx(0.0)


def test_corr_spike_forces_deleverage():
    cols = [f"S{i}" for i in range(10)]
    score = pd.DataFrame([list(range(10))], index=_dates(1), columns=cols).astype(float)
    w = build_target_weights(score, quantile=0.2)
    spike = pd.Series([True], index=_dates(1))
    scaled = apply_regime_leverage(w, regime=pd.Series([REGIME_LOW_VOL], index=_dates(1)),
                                   corr_spike=spike)
    assert scaled.iloc[0].abs().sum() == pytest.approx(0.4)  # 飙升强制危机级 0.2


def test_never_breaches_hard_limits():
    """任意乘子下,单边 ≤ 1x、合计 ≤ 2x 永不突破。"""
    cols = [f"S{i}" for i in range(10)]
    score = pd.DataFrame([list(range(10))], index=_dates(1), columns=cols).astype(float)
    w = build_target_weights(score, quantile=0.2)
    scaled = apply_regime_leverage(w, regime=pd.Series([REGIME_LOW_VOL], index=_dates(1)))
    row = scaled.iloc[0]
    assert row[row > 0].sum() <= 1.0 + 1e-9      # 单边 ≤ 1x
    assert -row[row < 0].sum() <= 1.0 + 1e-9
    assert row.abs().sum() <= 2.0 + 1e-9         # 合计 ≤ 2x


def test_no_regime_defaults_to_full():
    cols = [f"S{i}" for i in range(10)]
    score = pd.DataFrame([list(range(10))], index=_dates(1), columns=cols).astype(float)
    w = build_target_weights(score, quantile=0.2)
    scaled = apply_regime_leverage(w)  # 无 regime/spike → 不降杠杆
    assert scaled.iloc[0].abs().sum() == pytest.approx(2.0)
