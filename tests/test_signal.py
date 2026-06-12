"""横截面打分测试（阶段 4）。验收：等权合成、IC 符号定向、NaN 友好。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy.signal import cross_sectional_score, equal_weights, signs_from_mean_ic


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")


def test_signs_from_mean_ic():
    signs = signs_from_mean_ic({"a": 0.05, "b": -0.03, "c": 0.0, "d": float("nan")})
    assert signs == {"a": 1.0, "b": -1.0, "c": 1.0, "d": 1.0}


def test_equal_weights():
    assert equal_weights(["a", "b", "c", "d"]) == {n: 0.25 for n in "abcd"}
    assert equal_weights([]) == {}


def test_score_is_zero_mean_each_row():
    """居中 rank 合成 → 每日截面近似零均值（适配美元中性）。"""
    cols = list("ABCDE")
    f1 = pd.DataFrame([[1, 2, 3, 4, 5], [5, 4, 3, 2, 1]], index=_dates(2), columns=cols).astype(float)
    score = cross_sectional_score({"f1": f1}, signs={"f1": 1.0})
    for i in range(len(score)):
        assert score.iloc[i].sum() == pytest.approx(0.0, abs=1e-9)


def test_sign_flips_factor():
    """IC 为负的因子方向应被翻转：高原始值 → 低分。"""
    cols = list("ABCDE")
    f = pd.DataFrame([[1, 2, 3, 4, 5]], index=_dates(1), columns=cols).astype(float)
    pos = cross_sectional_score({"f": f}, signs={"f": 1.0}).iloc[0]
    neg = cross_sectional_score({"f": f}, signs={"f": -1.0}).iloc[0]
    assert pos["E"] > pos["A"]           # 正向：E（最大）得高分
    assert neg["E"] < neg["A"]           # 反向：E 得低分
    assert neg["A"] == pytest.approx(-pos["A"])  # 整体取反


def test_equal_weight_combination():
    """两因子等权合成 = 各自居中 rank 的平均。"""
    cols = list("ABCDE")
    f1 = pd.DataFrame([[1, 2, 3, 4, 5]], index=_dates(1), columns=cols).astype(float)
    f2 = pd.DataFrame([[5, 4, 3, 2, 1]], index=_dates(1), columns=cols).astype(float)
    score = cross_sectional_score({"f1": f1, "f2": f2}, signs={"f1": 1.0, "f2": 1.0}).iloc[0]
    # f1 与 f2 完全反向 → 等权合成后应彼此抵消,全为 0
    assert np.allclose(score.values, 0.0, atol=1e-9)


def test_nan_friendly_uses_available_factors():
    """某标的当日缺一个因子 → 用其余因子求加权平均,不当 0 处理。"""
    cols = list("ABCDE")
    f1 = pd.DataFrame([[1, 2, 3, 4, 5]], index=_dates(1), columns=cols).astype(float)
    f2 = pd.DataFrame([[1, 2, 3, 4, np.nan]], index=_dates(1), columns=cols).astype(float)
    score = cross_sectional_score({"f1": f1, "f2": f2}, signs={"f1": 1.0, "f2": 1.0}).iloc[0]
    # E 只有 f1 有效 → 分数 = f1 的居中 rank（不被缺失拉低）
    only_f1 = cross_sectional_score({"f1": f1}, signs={"f1": 1.0}).iloc[0]
    assert score["E"] == pytest.approx(only_f1["E"])


def test_empty_returns_empty():
    assert cross_sectional_score({}, signs={}).empty
