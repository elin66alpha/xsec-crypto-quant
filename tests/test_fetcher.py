"""Fetcher fallback tests; all network access is mocked."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from data.fetcher import (
    fetch_binance_vision_funding,
    fetch_binance_vision_ohlcv,
    fetch_ohlcv,
    to_binance_usdt_symbol,
)


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, zip_by_day: dict[str, bytes]) -> None:
        self.zip_by_day = zip_by_day
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> _FakeResponse:
        self.urls.append(url)
        assert timeout == 30
        for day, content in self.zip_by_day.items():
            suffix = day if day.endswith(".zip") else f"{day}.zip"
            if url.endswith(suffix):
                return _FakeResponse(200, content)
        return _FakeResponse(404)


def _daily_kline_zip(symbol: str, day: str, row: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(
            f"{symbol}-1d-{day}.csv",
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore\n"
            + ",".join(row)
            + "\n",
        )
    return buf.getvalue()


def _funding_zip(symbol: str, month: str, rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(
            f"{symbol}-fundingRate-{month}.csv",
            "calc_time,funding_interval_hours,last_funding_rate\n"
            + "\n".join(",".join(row) for row in rows)
            + "\n",
        )
    return buf.getvalue()


def test_to_binance_usdt_symbol_normalizes_okx_symbols():
    assert to_binance_usdt_symbol("FTT/USDT:USDT") == "FTTUSDT"
    assert to_binance_usdt_symbol("FTT-USDT-SWAP") == "FTTUSDT"
    assert to_binance_usdt_symbol("FTTUSDT") == "FTTUSDT"


def test_binance_vision_ohlcv_parses_daily_zip_and_skips_404():
    day = "2022-11-01"
    ts = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    session = _FakeSession(
        {
            day: _daily_kline_zip(
                "FTTUSDT",
                day,
                [
                    str(ts),
                    "10",
                    "12",
                    "9",
                    "11",
                    "1234",
                    str(ts + 86_399_999),
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                ],
            )
        }
    )

    df = fetch_binance_vision_ohlcv(
        "FTT/USDT:USDT",
        since="2022-11-01",
        until="2022-11-02",
        session=session,
    )

    assert session.urls[0].endswith("/FTTUSDT/1d/FTTUSDT-1d-2022-11-01.zip")
    assert session.urls[1].endswith("/FTTUSDT/1d/FTTUSDT-1d-2022-11-02.zip")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.tolist() == [pd.Timestamp("2022-11-01", tz="UTC")]
    assert df.loc[pd.Timestamp("2022-11-01", tz="UTC"), "close"] == 11.0


def test_fetch_ohlcv_dispatches_to_binance_vision_source():
    day = "2022-11-01"
    ts = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    session = _FakeSession(
        {
            day: _daily_kline_zip(
                "FTTUSDT",
                day,
                [str(ts), "1", "2", "0.5", "1.5", "100", str(ts), "0", "0", "0", "0", "0"],
            )
        }
    )

    df = fetch_ohlcv(
        "FTT/USDT:USDT",
        since="2022-11-01",
        until="2022-11-01",
        source="binance_vision",
        binance_symbol="FTTUSDT",
        session=session,
    )

    assert len(df) == 1
    assert df["volume"].iloc[0] == 100.0


def test_binance_vision_funding_parses_monthly_zip_and_skips_404():
    session = _FakeSession(
        {
            "BTCUSDT-fundingRate-2020-08.zip": _funding_zip(
                "BTCUSDT",
                "2020-08",
                [
                    ["1596240000005", "8", "0.00027910"],
                    ["1596268800000", "8", "-0.00010000"],
                ],
            )
        }
    )

    df = fetch_binance_vision_funding(
        "BTC/USDT:USDT",
        since="2020-08-01",
        until="2020-09-01",
        session=session,
        backoff_seconds=0,
    )

    assert session.urls == [
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        "BTCUSDT/BTCUSDT-fundingRate-2020-08.zip",
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        "BTCUSDT/BTCUSDT-fundingRate-2020-09.zip",
    ]
    assert list(df.columns) == ["fundingRate"]
    assert df.index.tolist() == [
        pd.Timestamp("2020-08-01 00:00:00.005", tz="UTC"),
        pd.Timestamp("2020-08-01 08:00:00", tz="UTC"),
    ]
    assert df["fundingRate"].tolist() == [0.0002791, -0.0001]


class _ShortPageOkx:
    rateLimit = 0

    def __init__(self) -> None:
        self.calls: list[int | None] = []

    def parse_timeframe(self, timeframe: str) -> int:
        assert timeframe == "1d"
        return 86_400

    def milliseconds(self) -> int:
        return int(pd.Timestamp("2022-04-11", tz="UTC").timestamp() * 1000)

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        assert symbol == "AVAX/USDT:USDT"
        assert timeframe == "1d"
        assert limit == 100
        self.calls.append(since)
        first_day = pd.Timestamp("2022-01-01", tz="UTC")
        if len(self.calls) == 1:
            days = pd.date_range(first_day, periods=99, freq="D", tz="UTC")
        elif len(self.calls) == 2:
            days = pd.date_range(first_day + pd.Timedelta(days=99), periods=2, freq="D", tz="UTC")
        else:
            days = pd.DatetimeIndex([], tz="UTC")
        return [
            [int(day.timestamp() * 1000), 1.0, 2.0, 0.5, 1.5, 100.0]
            for day in days
        ]


def test_okx_ohlcv_continues_after_short_non_final_page():
    exchange = _ShortPageOkx()

    df = fetch_ohlcv(
        "AVAX/USDT:USDT",
        since="2022-01-01",
        until="2022-04-11",
        exchange=exchange,
        source="okx",
    )

    assert len(df) == 101
    assert df.index[-1] == pd.Timestamp("2022-04-11", tz="UTC")
    assert len(exchange.calls) == 2


def test_binance_vision_requires_daily_bounded_requests():
    with pytest.raises(ValueError, match="1d"):
        fetch_binance_vision_ohlcv("FTTUSDT", timeframe="1h", since="2022-11-01")
    with pytest.raises(ValueError, match="since"):
        fetch_binance_vision_ohlcv("FTTUSDT")
