"""多重检验 FDR 校正测试（阶段 2）。验收：BH 已知值 + 校正前后对比。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.multiple_testing import (
    benjamini_hochberg,
    fdr_report,
    fdr_summary,
    pvalue_from_tstat,
)

# ------------------------------------------------------- pvalue_from_tstat


def test_pvalue_tstat_zero_is_one():
    assert pvalue_from_tstat(0.0, n_obs=100) == pytest.approx(1.0)


def test_pvalue_tstat_large_is_small():
    p = pvalue_from_tstat(10.0, n_obs=100)
    assert 0.0 <= p < 1e-6


def test_pvalue_tstat_insufficient_n():
    assert np.isnan(pvalue_from_tstat(3.0, n_obs=1))
    assert np.isnan(pvalue_from_tstat(float("nan"), n_obs=100))


# --------------------------------------------------------- benjamini_hochberg


def test_bh_classic_example():
    """经典例子：p=[.005,.009,.05,.1,.2], m=5, q=0.10 → 拒绝前 3 个。"""
    p = pd.Series([0.005, 0.009, 0.05, 0.1, 0.2], index=list("ABCDE"))
    res = benjamini_hochberg(p, q=0.10)
    # 结果按 p 升序
    assert list(res.index) == ["A", "B", "C", "D", "E"]
    assert res["reject"].tolist() == [True, True, True, False, False]
    # BH 临界线 (rank/m)*q
    assert res["bh_critical"].tolist() == pytest.approx([0.02, 0.04, 0.06, 0.08, 0.10])
    # q 值（单调累计最小）
    assert res["qvalue"].tolist() == pytest.approx(
        [0.0225, 0.0225, 0.0833333, 0.125, 0.2], abs=1e-6
    )


def test_bh_none_significant():
    p = pd.Series([0.5, 0.6, 0.7], index=list("ABC"))
    res = benjamini_hochberg(p, q=0.10)
    assert not res["reject"].any()


def test_bh_drops_nan_and_empty():
    res = benjamini_hochberg(pd.Series([np.nan, np.nan], index=["A", "B"]))
    assert res.empty


def test_bh_invalid_q():
    with pytest.raises(ValueError):
        benjamini_hochberg(pd.Series([0.01]), q=1.5)


def test_bh_is_less_lenient_than_naive():
    """FDR 不变量：被拒的 p 值都 ≤ q,且拒绝集是 {p ≤ q} 的子集（防假阳性）。"""
    q = 0.05
    p = pd.Series(np.linspace(0.001, 0.2, 20))
    res = benjamini_hochberg(p, q=q)
    rejected = res.loc[res["reject"], "pvalue"]
    assert (rejected <= q).all()                       # 拒绝的都 ≤ q
    n_naive = int((p <= q).sum())                      # 同一比较口径
    assert int(res["reject"].sum()) <= n_naive         # BH 是 {p≤q} 的子集


# ------------------------------------------------------------- fdr_report


def test_fdr_report_strong_vs_weak():
    """强因子（t 大）被判显著,弱因子（t≈0）不显著。"""
    summaries = {
        "strong": {"mean_ic": 0.05, "icir": 0.5, "t_stat": 8.0, "n_obs": 250},
        "weak": {"mean_ic": 0.001, "icir": 0.01, "t_stat": 0.1, "n_obs": 250},
    }
    table = fdr_report(summaries, q=0.10)
    assert table.loc["strong", "reject"]
    assert not table.loc["weak", "reject"]
    assert set(["mean_ic", "icir", "t_stat", "n_obs", "pvalue", "qvalue", "reject"]).issubset(
        table.columns
    )


def test_fdr_summary_before_after():
    summaries = {
        "f1": {"t_stat": 8.0, "n_obs": 250, "mean_ic": 0.05, "icir": 0.5},
        "f2": {"t_stat": 2.2, "n_obs": 250, "mean_ic": 0.01, "icir": 0.14},
        "f3": {"t_stat": 0.2, "n_obs": 250, "mean_ic": 0.001, "icir": 0.01},
    }
    table = fdr_report(summaries, q=0.10)
    s = fdr_summary(table)
    assert s["n_total"] == 3
    assert s["n_fdr"] <= s["n_naive"]  # 校正后不多于校正前
    assert s["n_fdr"] >= 1            # 强因子应当通过
