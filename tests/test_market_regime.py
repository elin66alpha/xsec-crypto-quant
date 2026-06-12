"""市场层面 Regime 测试（阶段 3）。验收：市场特征（纯逻辑）+ HMM 标注 + 无前视。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from regime.market_regime import (
    DEFAULT_N_STATES,
    REGIME_CRISIS,
    REGIME_LOW_VOL,
    _vol_order_mapping,
    compute_market_features,
    cross_sectional_corr_median,
    equal_weight_pool_return,
    label_regimes_expanding,
    label_regimes_full,
    realized_vol,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


# ----------------------------------------------------------- 纯逻辑特征层


def test_equal_weight_pool_return():
    close = pd.DataFrame({"A": [100.0, 110.0], "B": [100.0, 90.0]}, index=_dates(2))
    r = equal_weight_pool_return(close)
    assert np.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.0)  # (+0.1 + -0.1)/2


def test_realized_vol_constant_zero():
    r = pd.Series([np.nan, 0.01, 0.01, 0.01], index=_dates(4))
    v = realized_vol(r, window=3)
    assert v.iloc[-1] == pytest.approx(0.0)


def test_xs_corr_median_high_when_coins_move_together():
    """三个币几乎完全同向 → 横截面相关中位数接近 1。"""
    rng = np.random.default_rng(0)
    n = 60
    common = rng.normal(0, 0.02, n)
    close = pd.DataFrame(
        {c: 100 * np.cumprod(1 + common + rng.normal(0, 1e-4, n)) for c in ["A", "B", "C"]},
        index=_dates(n),
    )
    m = cross_sectional_corr_median(close, window=20)
    assert m.iloc[-1] > 0.9
    assert np.isnan(m.iloc[0])  # 窗口不足


def test_compute_market_features_columns_and_no_lookahead():
    rng = np.random.default_rng(1)
    n = 80
    close = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.02, (n, 4)), axis=0),
        index=_dates(n), columns=list("ABCD"),
    )
    feats = compute_market_features(close, vol_window=14, corr_window=30)
    assert list(feats.columns) == ["market_vol", "xs_corr_median", "mean_abs_return"]
    # 无前视：篡改未来不改变早期特征
    base = compute_market_features(close).iloc[40]
    tampered = close.copy()
    tampered.iloc[60:] *= 1.5
    after = compute_market_features(tampered).iloc[40]
    pd.testing.assert_series_equal(base, after)


# ------------------------------------------------------- 状态排序映射


def test_vol_order_mapping_sorts_by_volatility():
    raw = np.array([0, 0, 1, 1, 2, 2])
    vols = np.array([0.05, 0.05, 0.01, 0.01, 0.10, 0.10])  # state1 最低, state2 最高
    mapping = _vol_order_mapping(raw, vols, n_states=3)
    assert mapping[1] == REGIME_LOW_VOL   # 最低波动 → 0
    assert mapping[2] == REGIME_CRISIS    # 最高波动 → 2


# ----------------------------------------------------------- HMM 标注


def _two_regime_close(seed: int = 7):
    """造一段明确的'平静→危机→平静'市场：危机段波动放大数倍且各币齐跌（高相关）。"""
    rng = np.random.default_rng(seed)
    n_coins = 6
    calm = 0.012
    blocks = [(120, calm, 0.0), (60, 0.05, -0.01), (120, calm, 0.0)]  # (天数, 波动, 共同漂移)
    rows = []
    for days, sig, drift in blocks:
        common = rng.normal(drift, sig, days)
        idio = rng.normal(0, sig * 0.3, (days, n_coins))
        rows.append(common[:, None] + idio)
    ret = np.vstack(rows)
    n = ret.shape[0]
    close = pd.DataFrame(
        100 * np.cumprod(1 + ret, axis=0), index=_dates(n), columns=[f"C{i}" for i in range(n_coins)]
    )
    return close, (120, 180)  # 危机段在 [120,180)


def test_label_regimes_full_identifies_crisis():
    close, (c0, c1) = _two_regime_close()
    feats = compute_market_features(close, vol_window=14, corr_window=20)
    regimes = label_regimes_full(feats, n_states=DEFAULT_N_STATES)
    assert set(regimes.dropna().unique()).issubset({0, 1, 2})

    # 核心性质：危机 regime 的平均波动远高于低波 regime（状态确实按波动率排序）
    vol_by_regime = feats.loc[regimes.index, "market_vol"].groupby(regimes).mean()
    assert vol_by_regime[REGIME_CRISIS] > 2 * vol_by_regime[REGIME_LOW_VOL]

    # 危机标签应集中在危机段附近（允许 trailing-window 的自然滞后）
    crisis_idx = regimes[regimes == REGIME_CRISIS].index
    assert len(crisis_idx) > 0
    in_window = (crisis_idx >= _dates(300)[c0]) & (crisis_idx < _dates(300)[c1 + 25])
    assert in_window.mean() > 0.6


def test_label_regimes_expanding_no_lookahead_and_valid_labels():
    close, _ = _two_regime_close()
    feats = compute_market_features(close, vol_window=14, corr_window=20)
    regimes = label_regimes_expanding(
        feats, n_states=DEFAULT_N_STATES, min_train=150, refit_every=30
    )
    valid = regimes.dropna()
    assert valid.isin([0, 1, 2]).all()
    # 前 min_train 段无模型 → NaN
    assert regimes.iloc[:150].isna().all()
    assert len(valid) > 0
