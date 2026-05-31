"""动态池纯逻辑测试（无网络）。验收：决策 1 + 无前视（决策 9）。"""

from __future__ import annotations

import pandas as pd
import pytest

from data.universe import (
    UniverseConfig,
    build_universe_history,
    infer_listing_dates,
    select_universe,
)


@pytest.fixture
def panel() -> pd.DataFrame:
    """构造美元成交额面板：4 个标的，半年日数据。

    AAA 成交额最高、BBB 次之、CCC 较低；DDD 上市晚（只在最后两个月有数据）。
    """
    dates = pd.date_range("2023-01-01", "2023-06-30", freq="D", tz="UTC")
    data = {
        "AAA": 100.0,
        "BBB": 80.0,
        "CCC": 30.0,
    }
    df = pd.DataFrame({sym: [v] * len(dates) for sym, v in data.items()}, index=dates)
    # DDD 从 5 月才上市（之前 NaN），成交额很高
    ddd = pd.Series(float("nan"), index=dates)
    ddd.loc[ddd.index >= "2023-05-01"] = 200.0
    df["DDD"] = ddd
    return df


def test_infer_listing_dates(panel):
    listing = infer_listing_dates(panel)
    assert listing["AAA"] == pd.Timestamp("2023-01-01", tz="UTC")
    assert listing["DDD"] == pd.Timestamp("2023-05-01", tz="UTC")


def test_select_top_n_by_volume(panel):
    cfg = UniverseConfig(top_n=2, min_listing_days=30, lookback_days=30)
    sel = select_universe(panel, pd.Timestamp("2023-04-15", tz="UTC"), config=cfg)
    assert sel == ["AAA", "BBB"]  # 按成交额降序取 2


def test_listing_age_filter_excludes_new_listing(panel):
    """DDD 5/1 上市，5/15 时仅上市 14 天 < 90，尽管成交额最高也不得入选。"""
    cfg = UniverseConfig(top_n=4, min_listing_days=90, lookback_days=30)
    sel = select_universe(panel, pd.Timestamp("2023-05-15", tz="UTC"), config=cfg)
    assert "DDD" not in sel
    assert "AAA" in sel


def test_no_lookahead(panel):
    """as_of 当日之后的成交额不得影响选池：把未来某天 CCC 拉爆，结论不应改变。"""
    cfg = UniverseConfig(top_n=2, min_listing_days=30, lookback_days=30)
    as_of = pd.Timestamp("2023-03-15", tz="UTC")
    base = select_universe(panel, as_of, config=cfg)
    tampered = panel.copy()
    tampered.loc[tampered.index > as_of, "CCC"] = 1e9  # 仅篡改未来
    after = select_universe(tampered, as_of, config=cfg)
    assert base == after


def test_build_universe_history_monthly(panel):
    cfg = UniverseConfig(top_n=3, min_listing_days=30, lookback_days=30)
    hist = build_universe_history(panel, "2023-03-01", "2023-06-01", config=cfg)
    months = sorted(hist.keys())
    assert [m.month for m in months] == [3, 4, 5, 6]
    # 每月都至少选出原始 3 个老标的之一
    for members in hist.values():
        assert "AAA" in members
