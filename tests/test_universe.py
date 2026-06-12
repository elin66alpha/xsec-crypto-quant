"""动态池纯逻辑测试（无网络）。验收：决策 1 + 无前视（决策 9）。"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from data.universe import (
    UniverseConfig,
    build_universe_history,
    calendar_delisting_dates,
    calendar_listing_dates,
    infer_listing_dates,
    load_dollar_volume_panel,
    load_perp_calendar,
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


def test_load_perp_calendar_parses_listing_and_delisting_dates(tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "contracts": [
                    {
                        "symbol": "BTC/USDT:USDT",
                        "inst_id": "BTC-USDT-SWAP",
                        "listing_date": "2020-01-01",
                        "delisting_date": None,
                        "data_source": "okx",
                    },
                    {
                        "symbol": "FTT/USDT:USDT",
                        "inst_id": "FTT-USDT-SWAP",
                        "listing_date": "2021-09-01",
                        "delisting_date": "2022-11-14",
                        "data_source": "binance_vision",
                        "binance_symbol": "FTTUSDT",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    calendar = load_perp_calendar(path)
    listings = calendar_listing_dates(calendar)
    delistings = calendar_delisting_dates(calendar)

    assert listings["FTT-20210901"] == pd.Timestamp("2021-09-01", tz="UTC")
    assert delistings["FTT-20210901"] == pd.Timestamp("2022-11-14", tz="UTC")
    assert pd.isna(calendar.loc["BTC-20200101", "delisting_date"])
    assert calendar.loc["FTT-20210901", "symbol"] == "FTT/USDT:USDT"
    assert calendar.loc["FTT-20210901", "binance_symbol"] == "FTTUSDT"


def test_select_universe_truncates_after_delisting_without_pre_delist_signal():
    dates = pd.date_range("2023-01-01", "2023-06-30", freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "LIVE": [100.0] * len(dates),
            "DEAD": [200.0] * len(dates),
        },
        index=dates,
    )
    listings = pd.Series(
        {
            "LIVE": pd.Timestamp("2022-01-01", tz="UTC"),
            "DEAD": pd.Timestamp("2022-01-01", tz="UTC"),
        }
    )
    delistings = pd.Series({"DEAD": pd.Timestamp("2023-04-15", tz="UTC")})
    cfg = UniverseConfig(top_n=2, min_listing_days=0, lookback_days=30)

    before = select_universe(df, pd.Timestamp("2023-04-01", tz="UTC"), listings, delistings, cfg)
    after = select_universe(df, pd.Timestamp("2023-05-01", tz="UTC"), listings, delistings, cfg)

    assert before == ["DEAD", "LIVE"]
    assert after == ["LIVE"]


def test_build_universe_history_uses_calendar_delisting_dates():
    dates = pd.date_range("2023-01-01", "2023-06-30", freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "LIVE": [100.0] * len(dates),
            "DEAD": [200.0] * len(dates),
        },
        index=dates,
    )
    calendar = pd.DataFrame(
        {
            "symbol": ["LIVE/USDT:USDT", "DEAD/USDT:USDT"],
            "listing_date": [
                pd.Timestamp("2022-01-01", tz="UTC"),
                pd.Timestamp("2022-01-01", tz="UTC"),
            ],
            "delisting_date": [pd.NaT, pd.Timestamp("2023-04-15", tz="UTC")],
            "data_source": ["okx", "binance_vision"],
        },
        index=["LIVE", "DEAD"],
    )
    cfg = UniverseConfig(top_n=2, min_listing_days=0, lookback_days=30)

    history = build_universe_history(
        df,
        "2023-04-01",
        "2023-05-01",
        calendar=calendar,
        config=cfg,
    )

    assert history[pd.Timestamp("2023-04-01")] == ["DEAD", "LIVE"]
    assert history[pd.Timestamp("2023-05-01")] == ["LIVE"]


def test_build_universe_history_returns_asset_ids_for_reused_symbol():
    dates = pd.date_range("2022-04-20", "2022-06-05", freq="D", tz="UTC")
    old_luna = pd.Series(float("nan"), index=dates)
    old_luna.loc[old_luna.index <= "2022-05-13"] = 100.0
    new_luna = pd.Series(float("nan"), index=dates)
    new_luna.loc[new_luna.index >= "2022-05-28"] = 200.0
    df = pd.DataFrame(
        {
            "LUNA-20190726": old_luna,
            "LUNA-20220528": new_luna,
        },
        index=dates,
    )
    calendar = pd.DataFrame(
        {
            "symbol": ["LUNA/USDT:USDT", "LUNA/USDT:USDT"],
            "listing_date": [
                pd.Timestamp("2019-07-26", tz="UTC"),
                pd.Timestamp("2022-05-28", tz="UTC"),
            ],
            "delisting_date": [pd.Timestamp("2022-05-13", tz="UTC"), pd.NaT],
            "data_source": ["binance_vision", "okx"],
        },
        index=["LUNA-20190726", "LUNA-20220528"],
    )
    cfg = UniverseConfig(top_n=1, min_listing_days=0, lookback_days=5, min_valid_days_ratio=0.1)

    history = build_universe_history(
        df,
        "2022-05-01",
        "2022-06-01",
        calendar=calendar,
        config=cfg,
    )

    assert history[pd.Timestamp("2022-05-01")] == ["LUNA-20190726"]
    assert history[pd.Timestamp("2022-06-01")] == ["LUNA-20220528"]
    assert "LUNA/USDT:USDT" not in {member for members in history.values() for member in members}


def test_load_dollar_volume_panel_routes_calendar_sources(monkeypatch):
    calls = []

    def fake_fetch_ohlcv(
        symbol,
        timeframe,
        since,
        until,
        exchange=None,
        source="okx",
        binance_symbol=None,
        session=None,
    ):
        calls.append(
            {
                "symbol": symbol,
                "source": source,
                "binance_symbol": binance_symbol,
                "exchange": exchange,
                "session": session,
                "since": since,
                "until": until,
                "timeframe": timeframe,
            }
        )
        idx = pd.DatetimeIndex([pd.Timestamp("2022-11-01", tz="UTC")], name="datetime")
        return pd.DataFrame(
            {
                "open": [1.0],
                "high": [2.0],
                "low": [0.5],
                "close": [2.0],
                "volume": [50.0],
            },
            index=idx,
        )

    monkeypatch.setattr("data.fetcher.fetch_ohlcv", fake_fetch_ohlcv)
    calendar = pd.DataFrame(
        {
            "symbol": ["FTT/USDT:USDT"],
            "data_source": ["binance_vision"],
            "binance_symbol": ["FTTUSDT"],
            "listing_date": [pd.Timestamp("2021-09-01", tz="UTC")],
            "delisting_date": [pd.Timestamp("2022-11-14", tz="UTC")],
        },
        index=["FTT-20210901"],
    )
    session = object()

    panel = load_dollar_volume_panel(
        symbols=["FTT-20210901"],
        since="2022-11-01",
        until="2022-11-01",
        calendar=calendar,
        fetch_session=session,
    )

    assert calls == [
        {
            "symbol": "FTT/USDT:USDT",
            "source": "binance_vision",
            "binance_symbol": "FTTUSDT",
            "exchange": None,
            "session": session,
            "since": "2022-11-01",
            "until": "2022-11-01",
            "timeframe": "1d",
        }
    ]
    assert panel.loc[pd.Timestamp("2022-11-01", tz="UTC"), "FTT-20210901"] == 100.0


def test_load_dollar_volume_panel_keeps_reused_symbol_generations_separate(monkeypatch):
    calls = []
    exchange = object()

    def fake_fetch_ohlcv(
        symbol,
        timeframe,
        since,
        until,
        exchange=None,
        source="okx",
        binance_symbol=None,
        session=None,
    ):
        calls.append(
            {
                "symbol": symbol,
                "source": source,
                "binance_symbol": binance_symbol,
                "exchange": exchange,
            }
        )
        idx = pd.DatetimeIndex([pd.Timestamp("2022-06-01", tz="UTC")], name="datetime")
        close = 1.0 if binance_symbol == "LUNAUSDT" else 10.0
        return pd.DataFrame(
            {
                "open": [close],
                "high": [close],
                "low": [close],
                "close": [close],
                "volume": [100.0],
            },
            index=idx,
        )

    monkeypatch.setattr("data.fetcher.fetch_ohlcv", fake_fetch_ohlcv)
    calendar = pd.DataFrame(
        {
            "symbol": ["LUNA/USDT:USDT", "LUNA/USDT:USDT"],
            "data_source": ["binance_vision", "okx"],
            "binance_symbol": ["LUNAUSDT", None],
            "listing_date": [
                pd.Timestamp("2019-07-26", tz="UTC"),
                pd.Timestamp("2022-05-28", tz="UTC"),
            ],
            "delisting_date": [pd.Timestamp("2022-05-13", tz="UTC"), pd.NaT],
        },
        index=["LUNA-20190726", "LUNA-20220528"],
    )

    panel = load_dollar_volume_panel(
        exchange=exchange,
        since="2022-06-01",
        until="2022-06-01",
        calendar=calendar,
    )

    assert panel.columns.tolist() == ["LUNA-20190726", "LUNA-20220528"]
    assert calls == [
        {
            "symbol": "LUNA/USDT:USDT",
            "source": "binance_vision",
            "binance_symbol": "LUNAUSDT",
            "exchange": None,
        },
        {
            "symbol": "LUNA/USDT:USDT",
            "source": "okx",
            "binance_symbol": None,
            "exchange": exchange,
        },
    ]
    assert panel.loc[pd.Timestamp("2022-06-01", tz="UTC"), "LUNA-20190726"] == 100.0
    assert panel.loc[pd.Timestamp("2022-06-01", tz="UTC"), "LUNA-20220528"] == 1000.0

    with pytest.raises(ValueError, match="多个 asset_id"):
        load_dollar_volume_panel(
            exchange=exchange,
            symbols=["LUNA/USDT:USDT"],
            since="2022-06-01",
            until="2022-06-01",
            calendar=calendar,
        )
