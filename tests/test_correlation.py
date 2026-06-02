"""因子相关剔除测试（阶段 2）。验收：相关矩阵 + 贪心低相关精选。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.correlation import factor_correlation_matrix, select_low_correlation


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


def test_correlation_matrix_identical_and_independent():
    cols = ["A", "B", "C"]
    idx = _dates(30)
    rng = np.random.default_rng(1)
    base = pd.DataFrame(rng.normal(size=(30, 3)), index=idx, columns=cols)
    panels = {
        "f1": base,
        "f2": base.copy(),          # 与 f1 完全相同 → 相关 1.0
        "f3": -base,                # 与 f1 完全反向 → 相关 -1.0
    }
    corr = factor_correlation_matrix(panels)
    assert corr.loc["f1", "f2"] == pytest.approx(1.0)
    assert corr.loc["f1", "f3"] == pytest.approx(-1.0)
    assert corr.loc["f1", "f1"] == pytest.approx(1.0)


def test_select_drops_redundant_keeps_higher_icir():
    """A,B 高相关(0.9)冗余 → 留 ICIR 更高的 A；C 独立 → 保留。"""
    corr = pd.DataFrame(
        [[1.0, 0.9, 0.0], [0.9, 1.0, 0.1], [0.0, 0.1, 1.0]],
        index=["A", "B", "C"], columns=["A", "B", "C"],
    )
    icir = {"A": 0.5, "B": 0.4, "C": 0.3}
    kept = select_low_correlation(corr, icir, threshold=0.7)
    assert kept == ["A", "C"]


def test_select_strong_negative_is_redundant():
    """强负相关(-0.9)同样视为冗余（按绝对值判定）。"""
    corr = pd.DataFrame(
        [[1.0, -0.9], [-0.9, 1.0]], index=["A", "B"], columns=["A", "B"]
    )
    icir = {"A": 0.6, "B": 0.2}
    kept = select_low_correlation(corr, icir, threshold=0.7)
    assert kept == ["A"]


def test_select_max_factors_cap():
    cols = ["A", "B", "C", "D"]
    corr = pd.DataFrame(np.eye(4), index=cols, columns=cols)  # 全不相关
    icir = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    kept = select_low_correlation(corr, icir, threshold=0.7, max_factors=2)
    assert kept == ["A", "B"]  # 按 ICIR 降序取前 2


def test_select_ignores_nan_icir():
    cols = ["A", "B"]
    corr = pd.DataFrame(np.eye(2), index=cols, columns=cols)
    icir = {"A": 0.4, "B": float("nan")}
    kept = select_low_correlation(corr, icir, threshold=0.7)
    assert kept == ["A"]
