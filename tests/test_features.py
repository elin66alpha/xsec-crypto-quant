"""截面 rank 预处理测试（阶段 1）。验收：决策 5（截面 rank）+ 决策 9（无前视）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.preprocess import RankConfig, cross_sectional_rank, rank_normalize


@pytest.fixture
def panel() -> pd.DataFrame:
    """4 标的、3 日的原始因子面板。

    Day1 严格递增 A<B<C<D；Day2 严格递减；Day3 C 缺数据（NaN，当日不在池/无数据）。
    """
    dates = pd.date_range("2023-01-01", periods=3, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "A": [1.0, 4.0, 1.0],
            "B": [2.0, 3.0, 2.0],
            "C": [3.0, 2.0, np.nan],
            "D": [4.0, 1.0, 4.0],
        },
        index=dates,
    )


def test_pct_rank_ascending(panel):
    """默认升序百分位：当日最小值→0.25，最大值→1.0（4 个标的）。"""
    out = cross_sectional_rank(panel)
    row1 = out.iloc[0]
    assert row1["A"] == pytest.approx(0.25)
    assert row1["B"] == pytest.approx(0.50)
    assert row1["C"] == pytest.approx(0.75)
    assert row1["D"] == pytest.approx(1.00)
    # Day2 递减：最大值 A 仍得最高 rank
    assert out.iloc[1]["A"] == pytest.approx(1.00)
    assert out.iloc[1]["D"] == pytest.approx(0.25)


def test_nan_excluded_from_cross_section(panel):
    """NaN 标的输出仍为 NaN，且不占名次：当日有效 3 个，rank 归一到 1/3,2/3,1。"""
    out = cross_sectional_rank(panel)
    row3 = out.iloc[2]
    assert np.isnan(row3["C"])
    assert row3["A"] == pytest.approx(1 / 3)
    assert row3["B"] == pytest.approx(2 / 3)
    assert row3["D"] == pytest.approx(1.0)


def test_ascending_false_reverses(panel):
    """ascending=False：原始值越大 rank 越小。"""
    out = cross_sectional_rank(panel, RankConfig(ascending=False))
    row1 = out.iloc[0]
    assert row1["A"] == pytest.approx(1.00)  # 最小值 → 最高 rank
    assert row1["D"] == pytest.approx(0.25)  # 最大值 → 最低 rank


def test_center_row_sums_to_zero(panel):
    """center=True：每行（非 NaN 部分）和≈0，可直接作美元中性多空权重。"""
    out = cross_sectional_rank(panel, RankConfig(center=True))
    for i in range(len(out)):
        assert out.iloc[i].sum(skipna=True) == pytest.approx(0.0, abs=1e-9)
    # 居中后符号合理：当日最大值为正、最小值为负
    assert out.iloc[0]["D"] > 0 > out.iloc[0]["A"]


def test_ties_get_average_rank():
    """并列值用 average 名次：两两并列 → 0.375 / 0.875。"""
    dates = pd.date_range("2023-01-01", periods=1, freq="D", tz="UTC")
    df = pd.DataFrame({"A": [1.0], "B": [1.0], "C": [2.0], "D": [2.0]}, index=dates)
    out = cross_sectional_rank(df)
    row = out.iloc[0]
    assert row["A"] == pytest.approx(0.375)
    assert row["B"] == pytest.approx(0.375)
    assert row["C"] == pytest.approx(0.875)
    assert row["D"] == pytest.approx(0.875)


def test_min_valid_masks_thin_cross_section(panel):
    """min_valid=4 时，Day3 仅 3 个有效标的 → 整行置 NaN。"""
    out = cross_sectional_rank(panel, RankConfig(min_valid=4))
    assert out.iloc[2].isna().all()
    # Day1/Day2 满 4 个，不受影响
    assert out.iloc[0].notna().all()


def test_rows_are_independent_no_lookahead(panel):
    """无前视（决策 9）：篡改其他日期的数据不改变某一日的截面 rank。"""
    base = cross_sectional_rank(panel)
    tampered = panel.copy()
    tampered.iloc[1] = [999.0, -999.0, 0.0, 50.0]  # 只改 Day2
    after = cross_sectional_rank(tampered)
    pd.testing.assert_series_equal(base.iloc[0], after.iloc[0])
    pd.testing.assert_series_equal(base.iloc[2], after.iloc[2])


def test_empty_panel_returns_empty():
    out = cross_sectional_rank(pd.DataFrame())
    assert out.empty


def test_rank_normalize_kwargs(panel):
    """便捷入口与显式 config 结果一致。"""
    a = rank_normalize(panel, center=True)
    b = cross_sectional_rank(panel, RankConfig(center=True))
    pd.testing.assert_frame_equal(a, b)
