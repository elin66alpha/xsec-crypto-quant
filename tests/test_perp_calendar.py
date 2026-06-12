"""Perp calendar builder tests with mocked OKX/Binance Vision metadata."""

from __future__ import annotations

from urllib.parse import urlencode

from scripts.build_okx_perp_calendar import (
    BINANCE_VISION_S3_URL,
    DAILY_KLINES_PREFIX,
    MONTHLY_KLINES_PREFIX,
    build_calendar,
    infer_archive_date_range,
    list_s3_common_prefixes,
)


class _FakeResponse:
    def __init__(self, *, content: bytes = b"", json_data=None, status_code: int = 200) -> None:
        self.content = content
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, routes: dict[str, _FakeResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def get(self, url: str, params=None, timeout=20):
        del timeout
        self.calls.append((url, params))
        key = url if params is None else f"{url}?{urlencode(sorted(params.items()))}"
        try:
            return self.routes[key]
        except KeyError as exc:
            raise AssertionError(f"unexpected GET {key}") from exc


def _s3_xml(
    *,
    common_prefixes: list[str] | None = None,
    keys: list[str] | None = None,
    is_truncated: bool = False,
    next_marker: str | None = None,
) -> bytes:
    prefix_xml = "".join(
        f"<CommonPrefixes><Prefix>{prefix}</Prefix></CommonPrefixes>"
        for prefix in (common_prefixes or [])
    )
    key_xml = "".join(f"<Contents><Key>{key}</Key></Contents>" for key in (keys or []))
    next_xml = f"<NextMarker>{next_marker}</NextMarker>" if next_marker else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<IsTruncated>{str(is_truncated).lower()}</IsTruncated>"
        f"{next_xml}{prefix_xml}{key_xml}</ListBucketResult>"
    ).encode()


def _s3_route(prefix: str, *, marker: str | None = None, delimiter: str | None = None) -> str:
    params = {"max-keys": "1000", "prefix": prefix}
    if delimiter is not None:
        params["delimiter"] = delimiter
    if marker is not None:
        params["marker"] = marker
    return f"{BINANCE_VISION_S3_URL}?{urlencode(sorted(params.items()))}"


def test_s3_common_prefix_listing_uses_next_marker_pagination():
    routes = {
        _s3_route(DAILY_KLINES_PREFIX, delimiter="/"): _FakeResponse(
            content=_s3_xml(
                common_prefixes=[f"{DAILY_KLINES_PREFIX}AAAUSDT/"],
                is_truncated=True,
                next_marker="page-2",
            )
        ),
        _s3_route(DAILY_KLINES_PREFIX, marker="page-2", delimiter="/"): _FakeResponse(
            content=_s3_xml(common_prefixes=[f"{DAILY_KLINES_PREFIX}BBBUSDT/"])
        ),
    }
    session = _FakeSession(routes)

    prefixes = list_s3_common_prefixes(DAILY_KLINES_PREFIX, session=session, sleep_seconds=0)

    assert prefixes == [f"{DAILY_KLINES_PREFIX}AAAUSDT/", f"{DAILY_KLINES_PREFIX}BBBUSDT/"]
    assert session.calls[1][1]["marker"] == "page-2"


def test_infer_archive_date_range_refines_monthly_bounds_with_daily_files():
    symbol = "OLDUSDT"
    routes = {
        _s3_route(f"{MONTHLY_KLINES_PREFIX}{symbol}/1d/"): _FakeResponse(
            content=_s3_xml(
                keys=[
                    f"{MONTHLY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-05.zip",
                    f"{MONTHLY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-06.zip",
                ]
            )
        ),
        _s3_route(f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-05"): _FakeResponse(
            content=_s3_xml(
                keys=[
                    f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-05-03.zip",
                    f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-05-04.zip",
                ]
            )
        ),
        _s3_route(f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-06"): _FakeResponse(
            content=_s3_xml(
                keys=[
                    f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-06-19.zip",
                    f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-2021-06-20.zip",
                ]
            )
        ),
    }
    session = _FakeSession(routes)

    assert infer_archive_date_range(symbol, session=session, sleep_seconds=0) == (
        "2021-05-03",
        "2021-06-20",
        "monthly+daily",
    )


def test_build_calendar_adds_full_archive_delisted_and_symbol_reuse_generations():
    okx_payload = {
        "code": "0",
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "settleCcy": "USDT",
                "listTime": "1577836800000",
                "expTime": "",
            },
            {
                "instId": "LUNA-USDT-SWAP",
                "settleCcy": "USDT",
                "listTime": "1653696000000",
                "expTime": "",
            },
        ],
    }
    routes = {
        "https://www.okx.com/api/v5/public/instruments?instType=SWAP": _FakeResponse(json_data=okx_payload),
        _s3_route(DAILY_KLINES_PREFIX, delimiter="/"): _FakeResponse(
            content=_s3_xml(
                common_prefixes=[
                    f"{DAILY_KLINES_PREFIX}BTCUSDT/",
                    f"{DAILY_KLINES_PREFIX}BTCUSDT_210625/",
                    f"{DAILY_KLINES_PREFIX}FTTUSDT/",
                    f"{DAILY_KLINES_PREFIX}LUNAUSDT/",
                    f"{DAILY_KLINES_PREFIX}XRPBUSD/",
                ]
            )
        ),
    }
    for symbol, first, last in [
        ("BTCUSDT", "2020-01-01", "2023-01-01"),
        ("FTTUSDT", "2021-09-01", "2022-11-14"),
        ("LUNAUSDT", "2022-05-01", "2022-05-13"),
    ]:
        first_month = first[:7]
        last_month = last[:7]
        routes[_s3_route(f"{MONTHLY_KLINES_PREFIX}{symbol}/1d/")] = _FakeResponse(
            content=_s3_xml(
                keys=[
                    f"{MONTHLY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-{first_month}.zip",
                    f"{MONTHLY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-{last_month}.zip",
                ]
            )
        )
        routes[_s3_route(f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-{first_month}")] = _FakeResponse(
            content=_s3_xml(
                keys=[
                    f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-{first}.zip",
                    f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-{last}.zip",
                ]
            )
        )
        if last_month != first_month:
            routes[_s3_route(f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-{last_month}")] = _FakeResponse(
                content=_s3_xml(keys=[f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-{last}.zip"])
            )
    session = _FakeSession(routes)

    calendar = build_calendar(session=session, sleep_seconds=0)
    by_asset = {row["asset_id"]: row for row in calendar["contracts"]}

    assert "BTC-20200101" in by_asset
    assert "FTT-20210901" in by_asset
    assert "LUNA-20220501" in by_asset
    assert "LUNA-20220528" in by_asset
    assert by_asset["FTT-20210901"]["data_source"] == "binance_vision"
    assert by_asset["LUNA-20220501"]["symbol"] == by_asset["LUNA-20220528"]["symbol"]
    assert "BTC-20200101" not in [
        row["asset_id"] for row in calendar["contracts"] if row["data_source"] == "binance_vision"
    ]
