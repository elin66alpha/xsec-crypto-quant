"""因子筛选编排测试（阶段 2）。验收：IC→FDR→相关剔除 端到端。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from factors.selection import analyze_factors, compute_ic_summaries
from features.cross_sectional import momentum


def _predictive_panels(seed: int = 5):
    """造动量真有预测力的小宇宙 + 两个重叠动量（高相关）+ 一个纯噪声因子。"""
    rng = np.random.default_rng(seed)
    n_days, n_coins = 180, 15
    dates = pd.date_range("2023-01-01", periods=n_days, freq="D", tz="UTC")
    coins = [f"C{i:02d}" for i in range(n_coins)]
    mu = rng.normal(0.0, 0.003, n_coins)            # 持续 drift = 动量预测力之源
    market = rng.normal(0.0005, 0.02, n_days)
    ret = mu[None, :] + market[:, None] + rng.normal(0, 0.02, (n_days, n_coins))
    close = pd.DataFrame(100 * np.cumprod(1 + ret, axis=0), index=dates, columns=coins)
    panels = {
        "momentum_10": momentum(close, 10),
        "momentum_20": momentum(close, 20),
        "noise": pd.DataFrame(rng.normal(size=close.shape), index=dates, columns=coins),
    }
    return panels, close


def test_compute_ic_summaries_keys():
    panels, close = _predictive_panels()
    summ = compute_ic_summaries(panels, close, horizons=(1, 5))
    assert set(summ) == {f"{n}@{h}d" for n in panels for h in (1, 5)}
    for s in summ.values():
        assert {"mean_ic", "icir", "t_stat", "n_obs"}.issubset(s)


def test_analyze_factors_momentum_survives_and_pruned():
    panels, close = _predictive_panels()
    res = analyze_factors(panels, close, horizons=(1, 5), q=0.10)
    # 动量应经 FDR 后存活
    assert any(b.startswith("momentum") for b in res.surviving_bases)
    # 两个动量高相关 → 精选池里至多一个动量
    n_mom_selected = sum(1 for s in res.selected if s.startswith("momentum"))
    assert n_mom_selected == 1
    # 精选池非空且不超上限
    assert 0 < len(res.selected) <= 10
    # 校正后通过数不多于校正前
    assert res.fdr_counts["n_fdr"] <= res.fdr_counts["n_naive"]


def test_analyze_factors_selected_have_quantile_stats():
    panels, close = _predictive_panels()
    res = analyze_factors(panels, close, horizons=(1, 5))
    for b in res.selected:
        assert b in res.quantile_means
        assert "monotonicity" in res.quantile_stats[b]
        assert b in res.best_horizon


def test_analyze_factors_no_survivors_graceful():
    """纯噪声因子 → 可能无人通过,结果应优雅为空而非报错。"""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2023-01-01", periods=120, freq="D", tz="UTC")
    cols = [f"C{i}" for i in range(12)]
    close = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.02, (120, 12)), axis=0), index=dates, columns=cols
    )
    panels = {"pure_noise": pd.DataFrame(rng.normal(size=(120, 12)), index=dates, columns=cols)}
    res = analyze_factors(panels, close, horizons=(1, 5))
    assert isinstance(res.selected, list)
    assert res.fdr_counts["n_total"] == 2  # 1 因子 × 2 horizon
