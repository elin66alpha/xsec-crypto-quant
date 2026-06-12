"""数据层测试：storage 存取往返 + validate 检出注入问题（无网络）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.storage import (
    available_range,
    load_ohlcv,
    sanitize_symbol,
    save_ohlcv,
)
from data.validate import (
    check_ohlcv_validity,
    check_required_historical_members,
    check_timestamp_continuity,
    check_universe_consistency,
    validate_ohlcv,
)


def _make_ohlcv(start="2023-01-01", periods=40) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="D", tz="UTC")
    close = pd.Series(100.0 + np.arange(periods), index=idx)
    return pd.DataFrame(
        {
            "open": close.values,
            "high": close.values + 2,
            "low": close.values - 2,
            "close": close.values,
            "volume": 1000.0,
        },
        index=idx,
    )


# ---------- storage ----------

def test_sanitize_symbol():
    assert sanitize_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert sanitize_symbol("ETH/USDT") == "ETHUSDT"


def test_storage_roundtrip(tmp_path):
    df = _make_ohlcv(periods=70)  # 跨 3 个月，触发多分片
    save_ohlcv(df, "BTC/USDT:USDT", data_dir=tmp_path)
    loaded = load_ohlcv("BTC/USDT:USDT", data_dir=tmp_path)
    # parquet 不保留 index.freq 元数据；数据本身应完全一致
    pd.testing.assert_frame_equal(df, loaded, check_freq=False)


def test_storage_idempotent_merge(tmp_path):
    df = _make_ohlcv(periods=40)
    save_ohlcv(df, "BTCUSDT", data_dir=tmp_path)
    save_ohlcv(df, "BTCUSDT", data_dir=tmp_path)  # 重复写不应产生重复行
    loaded = load_ohlcv("BTCUSDT", data_dir=tmp_path)
    assert len(loaded) == len(df)
    assert not loaded.index.duplicated().any()


def test_available_range(tmp_path):
    df = _make_ohlcv(periods=40)
    save_ohlcv(df, "BTCUSDT", data_dir=tmp_path)
    lo, hi = available_range("BTCUSDT", data_dir=tmp_path)
    assert lo == df.index[0] and hi == df.index[-1]


# ---------- validate: 必须检出人为注入的问题 ----------

def test_validate_clean_passes():
    assert validate_ohlcv(_make_ohlcv()).ok


def test_validate_detects_gap():
    df = _make_ohlcv(periods=40).drop(index=pd.Timestamp("2023-01-10", tz="UTC"))
    rep = check_timestamp_continuity(df, "1d")
    assert any("gap" in w for w in rep.warnings)


def test_validate_detects_bad_ohlc():
    df = _make_ohlcv(periods=10)
    df.loc[df.index[3], "high"] = 0.0  # high < low/close，非法
    rep = check_ohlcv_validity(df)
    assert not rep.ok


def test_validate_detects_duplicate_timestamp():
    df = _make_ohlcv(periods=10)
    dup = pd.concat([df, df.iloc[[5]]])
    rep = check_timestamp_continuity(dup, "1d")
    assert not rep.ok


# ---------- validate: 池成分一致性（幸存者偏差/前视）----------

def test_universe_consistency_passes_when_listed_early():
    listing = pd.Series({
        "AAA": pd.Timestamp("2022-01-01", tz="UTC"),
        "BBB": pd.Timestamp("2022-06-01", tz="UTC"),
    })
    hist = {pd.Timestamp("2023-03-01", tz="UTC"): ["AAA", "BBB"]}
    assert check_universe_consistency(hist, listing, min_listing_days=90).ok


def test_universe_consistency_detects_lookahead():
    """池含一个上市日晚于该月的标的 -> 前视/幸存者偏差，必须报 error。"""
    listing = pd.Series({
        "AAA": pd.Timestamp("2022-01-01", tz="UTC"),
        "FUTURE": pd.Timestamp("2023-05-01", tz="UTC"),  # 晚于 3 月池
    })
    hist = {pd.Timestamp("2023-03-01", tz="UTC"): ["AAA", "FUTURE"]}
    rep = check_universe_consistency(hist, listing, min_listing_days=90)
    assert not rep.ok
    assert any("FUTURE" in e for e in rep.errors)


def test_universe_consistency_detects_post_delisting_members():
    listing = pd.Series({"FTT-20220501": pd.Timestamp("2022-05-01", tz="UTC")})
    delisting = pd.Series({"FTT-20220501": pd.Timestamp("2022-11-14", tz="UTC")})
    hist = {pd.Timestamp("2022-12-01", tz="UTC"): ["FTT-20220501"]}

    rep = check_universe_consistency(
        hist,
        listing,
        min_listing_days=90,
        delisting_dates=delisting,
    )

    assert not rep.ok
    assert any("退市后仍入池" in e for e in rep.errors)


def test_required_historical_members_detects_missing_delisted_representatives():
    hist = {
        pd.Timestamp("2021-01-01", tz="UTC"): ["BTC", "ETH"],
        pd.Timestamp("2022-01-01", tz="UTC"): ["BTC", "SOL"],
    }

    rep = check_required_historical_members(
        hist,
        {"LUNA-20190726", "FTT-20220501", "SRM-20210101", "ANC-20220401"},
        start=pd.Timestamp("2021-01-01", tz="UTC"),
        end=pd.Timestamp("2022-12-01", tz="UTC"),
    )

    assert not rep.ok
    assert any("疑似幸存者偏差" in e for e in rep.errors)


def test_required_historical_members_passes_when_delisted_representative_present():
    hist = {
        pd.Timestamp("2021-01-01", tz="UTC"): ["BTC", "ETH"],
        pd.Timestamp("2022-01-01", tz="UTC"): ["BTC", "FTT-20220501"],
    }

    rep = check_required_historical_members(
        hist,
        {"LUNA-20190726", "FTT-20220501", "SRM-20210101", "ANC-20220401"},
        start=pd.Timestamp("2021-01-01", tz="UTC"),
        end=pd.Timestamp("2022-12-01", tz="UTC"),
    )

    assert rep.ok


def test_universe_consistency_runs_required_historical_member_check():
    listing = pd.Series({
        "BTC": pd.Timestamp("2020-01-01", tz="UTC"),
        "ETH": pd.Timestamp("2020-01-01", tz="UTC"),
    })
    hist = {
        pd.Timestamp("2021-01-01", tz="UTC"): ["BTC"],
        pd.Timestamp("2022-01-01", tz="UTC"): ["ETH"],
    }

    rep = check_universe_consistency(
        hist,
        listing,
        min_listing_days=0,
        required_presence_symbols={"LUNA-20190726", "FTT-20220501", "SRM-20210101", "ANC-20220401"},
        required_presence_start=pd.Timestamp("2021-01-01", tz="UTC"),
        required_presence_end=pd.Timestamp("2022-12-01", tz="UTC"),
    )

    assert not rep.ok
    assert any("now-delisted" in e for e in rep.errors)
