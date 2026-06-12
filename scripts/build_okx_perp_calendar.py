"""Build a reproducible USDT perpetual contract calendar.

The OKX public instruments endpoint only lists currently known instruments. Per
the delisted-contract probe, removed contracts are not queryable by old OKX
instId. This script therefore combines:

1. current OKX USDT swap instruments from public REST; and
2. historical USD-M USDT perpetual symbols enumerated from Binance Vision S3
   archives as an approximation for OKX-unavailable delisted generations.

The output is intentionally small JSON metadata committed to git, not market
data. It is used to make universe construction timestamp-aware. Calendar rows
use asset_id, not symbol, as the unique key because symbols such as LUNA can be
reused for different asset generations.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments"
BINANCE_VISION_S3_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DAILY_KLINES_PREFIX = "data/futures/um/daily/klines/"
MONTHLY_KLINES_PREFIX = "data/futures/um/monthly/klines/"
DEFAULT_OUTPUT = Path("metadata/okx_perp_calendar.json")
DEFAULT_SLEEP_SECONDS = 0.05
DEFAULT_RETRIES = 3
DEFAULT_WORKERS = 16
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
DAILY_DATE_RE = re.compile(r"-1d-(\d{4}-\d{2}-\d{2})\.zip$")
MONTHLY_DATE_RE = re.compile(r"-1d-(\d{4}-\d{2})\.zip$")


def _client(session=None):
    return session if session is not None else requests


def _sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def get_with_retry(
    url: str,
    *,
    params: dict[str, str] | None = None,
    session=None,
    timeout: float = 20.0,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
):
    """GET with bounded retry and rate limiting for public metadata endpoints."""
    client = _client(session)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.get(url, params=params, timeout=timeout)
            if response.status_code not in {429, 500, 502, 503, 504}:
                _sleep(sleep_seconds)
                response.raise_for_status()
                return response
            last_exc = RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:  # noqa: BLE001 - metadata fetch retry boundary
            last_exc = exc
        if attempt < retries:
            _sleep(sleep_seconds * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"GET failed: {url}")


def _find_text(node: ET.Element, name: str) -> str | None:
    found = node.find(f"s3:{name}", S3_NS)
    if found is None:
        found = node.find(name)
    return found.text if found is not None else None


def _findall(node: ET.Element, name: str) -> list[ET.Element]:
    found = node.findall(f"s3:{name}", S3_NS)
    if found:
        return found
    return node.findall(name)


def _parse_s3_xml(content: bytes) -> tuple[list[str], list[str], bool, str | None]:
    root = ET.fromstring(content)
    common_prefixes = [
        text
        for elem in _findall(root, "CommonPrefixes")
        if (text := _find_text(elem, "Prefix")) is not None
    ]
    keys = [
        text
        for elem in _findall(root, "Contents")
        if (text := _find_text(elem, "Key")) is not None
    ]
    is_truncated = (_find_text(root, "IsTruncated") or "false").lower() == "true"
    next_marker = _find_text(root, "NextMarker")
    if is_truncated and not next_marker:
        next_marker = (common_prefixes or keys or [None])[-1]
    return common_prefixes, keys, is_truncated, next_marker


def list_s3(
    prefix: str,
    *,
    delimiter: str | None = None,
    session=None,
    timeout: float = 20.0,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> tuple[list[str], list[str]]:
    """List Binance Vision S3 prefixes/keys using ListBucket XML + NextMarker pagination."""
    all_prefixes: list[str] = []
    all_keys: list[str] = []
    marker: str | None = None
    while True:
        params = {"prefix": prefix, "max-keys": "1000"}
        if delimiter is not None:
            params["delimiter"] = delimiter
        if marker is not None:
            params["marker"] = marker
        response = get_with_retry(
            BINANCE_VISION_S3_URL,
            params=params,
            session=session,
            timeout=timeout,
            retries=retries,
            sleep_seconds=sleep_seconds,
        )
        prefixes, keys, is_truncated, next_marker = _parse_s3_xml(response.content)
        all_prefixes.extend(prefixes)
        all_keys.extend(keys)
        if not is_truncated:
            break
        if not next_marker:
            raise RuntimeError(f"S3 listing truncated but missing NextMarker for {prefix}")
        marker = next_marker
    return all_prefixes, all_keys


def list_s3_common_prefixes(prefix: str, **kwargs) -> list[str]:
    prefixes, _ = list_s3(prefix, delimiter="/", **kwargs)
    return prefixes


def list_s3_keys(prefix: str, **kwargs) -> list[str]:
    _, keys = list_s3(prefix, **kwargs)
    return keys


def ms_to_date(value: str | int | None) -> str | None:
    if value in (None, "", "0"):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date().isoformat()


def _iso_date(value: str | None) -> datetime.date | None:
    if value in (None, ""):
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def inst_id_to_symbol(inst_id: str) -> str:
    if not inst_id.endswith("-USDT-SWAP"):
        raise ValueError(f"unsupported instId format: {inst_id}")
    base = inst_id.removesuffix("-USDT-SWAP")
    return f"{base}/USDT:USDT"


def asset_id(base: str, listing_date: str | None) -> str:
    """Unique asset-generation key; symbols alone can be reused after redenomination."""
    suffix = (listing_date or "unknown").replace("-", "")
    return f"{base}-{suffix}"


def with_asset_id(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    out["asset_id"] = asset_id(str(out["base"]), out.get("listing_date"))
    return out


def normalize_okx_instrument(row: dict[str, Any]) -> dict[str, Any] | None:
    inst_id = row.get("instId", "")
    settle = row.get("settleCcy")
    if not inst_id.endswith("-USDT-SWAP") or settle != "USDT":
        return None
    base = inst_id.removesuffix("-USDT-SWAP")
    return with_asset_id({
        "inst_id": inst_id,
        "symbol": inst_id_to_symbol(inst_id),
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "listing_date": ms_to_date(row.get("listTime")),
        "delisting_date": ms_to_date(row.get("expTime")),
        "data_source": "okx",
        "binance_symbol": None,
        "notes": "current OKX public instruments snapshot",
    })


def binance_symbol_to_base(symbol: str) -> str:
    if not symbol.endswith("USDT"):
        raise ValueError(f"not a USDT symbol: {symbol}")
    return symbol.removesuffix("USDT")


def binance_symbol_to_okx_symbol(symbol: str) -> str:
    return f"{binance_symbol_to_base(symbol)}/USDT:USDT"


def binance_symbol_to_inst_id(symbol: str) -> str:
    return f"{binance_symbol_to_base(symbol)}-USDT-SWAP"


def is_usdt_perp_archive_symbol(symbol: str) -> bool:
    """Keep USD-M perpetual-like USDT symbols; drop USDC/BUSD and dated futures."""
    return symbol.endswith("USDT") and "_" not in symbol and not symbol.endswith("USDTSETTLED")


def symbol_from_s3_prefix(prefix: str, root_prefix: str) -> str:
    rest = prefix.removeprefix(root_prefix).strip("/")
    return rest.split("/", maxsplit=1)[0]


def fetch_current_okx_usdt_swaps(
    timeout: float = 20.0,
    *,
    session=None,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> list[dict[str, Any]]:
    response = get_with_retry(
        OKX_INSTRUMENTS_URL,
        params={"instType": "SWAP"},
        session=session,
        timeout=timeout,
        retries=retries,
        sleep_seconds=sleep_seconds,
    )
    payload = response.json()
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX instruments failed: {payload.get('code')} {payload.get('msg')}")
    entries = [normalize_okx_instrument(row) for row in payload.get("data", [])]
    return sorted((entry for entry in entries if entry is not None), key=lambda x: x["symbol"])


def enumerate_binance_vision_usdt_symbols(
    *,
    session=None,
    timeout: float = 20.0,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> list[str]:
    prefixes = list_s3_common_prefixes(
        DAILY_KLINES_PREFIX,
        session=session,
        timeout=timeout,
        retries=retries,
        sleep_seconds=sleep_seconds,
    )
    symbols = {
        symbol_from_s3_prefix(prefix, DAILY_KLINES_PREFIX)
        for prefix in prefixes
    }
    return sorted(symbol for symbol in symbols if is_usdt_perp_archive_symbol(symbol))


def _month_start(month: str) -> datetime.date:
    return datetime.strptime(month, "%Y-%m").date()


def _month_end(month: str) -> datetime.date:
    year, month_num = map(int, month.split("-"))
    return datetime(year, month_num, calendar.monthrange(year, month_num)[1]).date()


def _dates_from_keys(keys: list[str], pattern: re.Pattern[str]) -> list[str]:
    out: list[str] = []
    for key in keys:
        match = pattern.search(key)
        if match:
            out.append(match.group(1))
    return sorted(set(out))


def list_archive_months(symbol: str, **kwargs) -> list[str]:
    keys = list_s3_keys(f"{MONTHLY_KLINES_PREFIX}{symbol}/1d/", **kwargs)
    return _dates_from_keys(keys, MONTHLY_DATE_RE)


def list_archive_daily_dates(symbol: str, month: str | None = None, **kwargs) -> list[str]:
    prefix = f"{DAILY_KLINES_PREFIX}{symbol}/1d/{symbol}-1d-"
    if month is not None:
        prefix += month
    keys = list_s3_keys(prefix, **kwargs)
    return _dates_from_keys(keys, DAILY_DATE_RE)


def infer_archive_date_range(
    symbol: str,
    *,
    session=None,
    timeout: float = 20.0,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> tuple[str, str, str]:
    """Infer first/last archive date from monthly files, refined by daily files."""
    kwargs = {
        "session": session,
        "timeout": timeout,
        "retries": retries,
        "sleep_seconds": sleep_seconds,
    }
    months = list_archive_months(symbol, **kwargs)
    if months:
        first_month, last_month = months[0], months[-1]
        first_daily = list_archive_daily_dates(symbol, first_month, **kwargs)
        last_daily = list_archive_daily_dates(symbol, last_month, **kwargs)
        start = first_daily[0] if first_daily else _month_start(first_month).isoformat()
        end = last_daily[-1] if last_daily else _month_end(last_month).isoformat()
        return start, end, "monthly+daily"

    dates = list_archive_daily_dates(symbol, **kwargs)
    if not dates:
        raise RuntimeError(f"Binance Vision archive has no 1d files for {symbol}")
    return dates[0], dates[-1], "daily"


def refine_archive_date_range_from_months(
    symbol: str,
    months: list[str],
    *,
    session=None,
    timeout: float = 20.0,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> tuple[str, str, str]:
    """Refine a known monthly range with daily files for the boundary months."""
    if not months:
        raise ValueError("months must not be empty")
    kwargs = {
        "session": session,
        "timeout": timeout,
        "retries": retries,
        "sleep_seconds": sleep_seconds,
    }
    first_month, last_month = months[0], months[-1]
    first_daily = list_archive_daily_dates(symbol, first_month, **kwargs)
    last_daily = first_daily if last_month == first_month else list_archive_daily_dates(symbol, last_month, **kwargs)
    start = first_daily[0] if first_daily else _month_start(first_month).isoformat()
    end = last_daily[-1] if last_daily else _month_end(last_month).isoformat()
    return start, end, "monthly+daily"


def binance_archive_entry(symbol: str, listing_date: str, delisting_date: str, source: str) -> dict[str, Any]:
    base = binance_symbol_to_base(symbol)
    return with_asset_id({
        "inst_id": binance_symbol_to_inst_id(symbol),
        "symbol": binance_symbol_to_okx_symbol(symbol),
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "listing_date": listing_date,
        "delisting_date": delisting_date,
        "data_source": "binance_vision",
        "binance_symbol": symbol,
        "notes": (
            "Binance Vision archive-inferred lifecycle "
            f"({source}); used as bounded approximation for OKX-unavailable historical contracts."
        ),
    })


def should_add_binance_generation(
    symbol: str,
    archive_start: str,
    archive_end: str,
    current_by_symbol: dict[str, dict[str, Any]],
) -> bool:
    okx_symbol = binance_symbol_to_okx_symbol(symbol)
    current = current_by_symbol.get(okx_symbol)
    if current is None:
        return True
    current_listing = _iso_date(current.get("listing_date"))
    archive_end_date = _iso_date(archive_end)
    if current_listing is None or archive_end_date is None:
        return False
    return archive_end_date < current_listing


def current_listing_within_month(
    symbol: str,
    month: str,
    current_by_symbol: dict[str, dict[str, Any]],
) -> bool:
    current = current_by_symbol.get(binance_symbol_to_okx_symbol(symbol))
    if current is None:
        return False
    current_listing = _iso_date(current.get("listing_date"))
    if current_listing is None:
        return False
    return _month_start(month) <= current_listing <= _month_end(month)


def build_calendar(
    timeout: float = 20.0,
    *,
    session=None,
    retries: int = DEFAULT_RETRIES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    max_workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    current = fetch_current_okx_usdt_swaps(
        timeout=timeout,
        session=session,
        retries=retries,
        sleep_seconds=sleep_seconds,
    )
    current_by_symbol = {entry["symbol"]: entry for entry in current}
    by_asset = {entry["asset_id"]: entry for entry in current}
    archive_symbols = enumerate_binance_vision_usdt_symbols(
        session=session,
        timeout=timeout,
        retries=retries,
        sleep_seconds=sleep_seconds,
    )

    def build_archive_entry(symbol: str) -> dict[str, Any] | None:
        kwargs = {
            "session": session,
            "timeout": timeout,
            "retries": retries,
            "sleep_seconds": sleep_seconds,
        }
        months = list_archive_months(symbol, **kwargs)
        if months:
            rough_start = _month_start(months[0]).isoformat()
            rough_end = _month_end(months[-1]).isoformat()
            if (
                not should_add_binance_generation(symbol, rough_start, rough_end, current_by_symbol)
                and not current_listing_within_month(symbol, months[-1], current_by_symbol)
            ):
                return None
            archive_start, archive_end, source = refine_archive_date_range_from_months(symbol, months, **kwargs)
        else:
            archive_start, archive_end, source = infer_archive_date_range(symbol, **kwargs)
        if not should_add_binance_generation(symbol, archive_start, archive_end, current_by_symbol):
            return None
        return binance_archive_entry(symbol, archive_start, archive_end, source)

    worker_count = 1 if session is not None else max(1, max_workers)
    if worker_count == 1:
        archive_entries = [build_archive_entry(symbol) for symbol in archive_symbols]
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(build_archive_entry, symbol): symbol
                for symbol in archive_symbols
            }
            archive_entries = [future.result() for future in as_completed(futures)]

    for entry in archive_entries:
        if entry is None:
            continue
        by_asset.setdefault(entry["asset_id"], entry)
    entries = sorted(by_asset.values(), key=lambda x: (x["symbol"], x["asset_id"]))
    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "schema_version": 3,
        "notes": (
            "Current OKX instruments plus Binance Vision archive-inferred USDT perpetual "
            "asset generations for OKX-unavailable historical contracts. Binance archive "
            "membership is a bounded proxy for unavailable OKX delisting history. asset_id "
            "is the stable universe key; symbol is only the exchange fetch symbol."
        ),
        "binance_vision_symbols_seen": len(archive_symbols),
        "contracts": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()

    calendar = build_calendar(
        timeout=args.timeout,
        sleep_seconds=args.sleep_seconds,
        retries=args.retries,
        max_workers=args.workers,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(calendar, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(calendar['contracts'])} contracts to {args.output}")


if __name__ == "__main__":
    main()
