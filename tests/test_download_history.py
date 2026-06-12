"""Download-history driver tests; network access is mocked."""

from __future__ import annotations

import io
import zipfile

import pandas as pd

from data.fetcher import fetch_binance_vision_ohlcv
from data.storage import save_ohlcv
from scripts.download_history import (
    ContractDownload,
    build_dollar_volume_panel_from_storage,
    download_ohlcv_stage,
)


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, routes: dict[str, _FakeResponse]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> _FakeResponse:
        self.urls.append(url)
        assert timeout == 30
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response
        return _FakeResponse(404)


def _zip_with_rows(filename: str, rows: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(
            filename,
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore\n"
            + "\n".join(",".join(row) for row in rows)
            + "\n",
        )
    return buf.getvalue()


def _row(day: str, close: str = "11", volume: str = "1234") -> list[str]:
    ts = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    return [str(ts), "10", "12", "9", close, volume, str(ts + 86_399_999), "0", "0", "0", "0", "0"]


def _ohlcv(start: str, periods: int, close: float = 10.0, volume: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def test_binance_vision_monthly_zip_is_used_for_complete_month():
    monthly_zip = _zip_with_rows(
        "FTTUSDT-1d-2022-01.csv",
        [_row("2022-01-01", close="11"), _row("2022-01-02", close="12")],
    )
    session = _FakeSession({"FTTUSDT-1d-2022-01.zip": _FakeResponse(200, monthly_zip)})

    df = fetch_binance_vision_ohlcv(
        "FTTUSDT",
        since="2022-01-01",
        until="2022-01-31",
        session=session,
        backoff_seconds=0,
    )

    assert session.urls == [
        "https://data.binance.vision/data/futures/um/monthly/klines/FTTUSDT/1d/FTTUSDT-1d-2022-01.zip"
    ]
    assert df.index.tolist() == [
        pd.Timestamp("2022-01-01", tz="UTC"),
        pd.Timestamp("2022-01-02", tz="UTC"),
    ]
    assert df["close"].tolist() == [11.0, 12.0]


def test_binance_vision_monthly_404_falls_back_to_daily_zip():
    daily_zip = _zip_with_rows("FTTUSDT-1d-2022-01-03.csv", [_row("2022-01-03")])
    session = _FakeSession(
        {
            "FTTUSDT-1d-2022-01.zip": _FakeResponse(404),
            "FTTUSDT-1d-2022-01-03.zip": _FakeResponse(200, daily_zip),
        }
    )

    df = fetch_binance_vision_ohlcv(
        "FTTUSDT",
        since="2022-01-01",
        until="2022-01-31",
        session=session,
        backoff_seconds=0,
    )

    assert session.urls[0].endswith("/monthly/klines/FTTUSDT/1d/FTTUSDT-1d-2022-01.zip")
    assert any(url.endswith("/daily/klines/FTTUSDT/1d/FTTUSDT-1d-2022-01-01.zip") for url in session.urls)
    assert any(url.endswith("/daily/klines/FTTUSDT/1d/FTTUSDT-1d-2022-01-31.zip") for url in session.urls)
    assert df.index.tolist() == [pd.Timestamp("2022-01-03", tz="UTC")]
    assert len(session.urls) == 32


def test_download_ohlcv_stage_skips_fetch_when_storage_covers_range(tmp_path):
    asset_id = "FTT-20210901"
    save_ohlcv(_ohlcv("2022-01-01", periods=31), asset_id, data_dir=tmp_path)
    calls = []

    def fake_fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("covered range should not be fetched")

    contract = ContractDownload(
        asset_id=asset_id,
        symbol="FTT/USDT:USDT",
        data_source="binance_vision",
        binance_symbol="FTTUSDT",
        listing_date=pd.Timestamp("2021-09-01", tz="UTC"),
        delisting_date=pd.Timestamp("2022-11-14", tz="UTC"),
        fetch_start=pd.Timestamp("2022-01-01", tz="UTC"),
        fetch_end=pd.Timestamp("2022-01-31", tz="UTC"),
    )

    downloaded, failures = download_ohlcv_stage(
        [contract],
        data_dir=tmp_path,
        fetcher=fake_fetcher,
        session=_FakeSession({}),
    )

    assert downloaded == [asset_id]
    assert failures == []
    assert calls == []


def test_build_dollar_volume_panel_from_storage(tmp_path):
    save_ohlcv(_ohlcv("2022-01-01", periods=2, close=10.0, volume=100.0), "AAA-20200101", data_dir=tmp_path)
    save_ohlcv(_ohlcv("2022-01-01", periods=2, close=20.0, volume=50.0), "BBB-20200101", data_dir=tmp_path)

    panel = build_dollar_volume_panel_from_storage(
        ["AAA-20200101", "BBB-20200101"],
        data_dir=tmp_path,
    )

    assert panel.shape == (2, 2)
    assert panel.loc[pd.Timestamp("2022-01-01", tz="UTC"), "AAA-20200101"] == 1000.0
    assert panel.loc[pd.Timestamp("2022-01-02", tz="UTC"), "BBB-20200101"] == 1000.0
