"""Probe OKX public data availability for delisted USDT swap contracts.

This script is intentionally read-only: it calls public OKX and Binance archive
endpoints, prints a JSON result, and does not write project data files.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import ccxt
import requests

OKX_BASE = "https://www.okx.com"
BINANCE_DATA_BASE = "https://data.binance.vision"
TARGET_INST_IDS = (
    "LUNA-USDT-SWAP",
    "FTT-USDT-SWAP",
    "SRM-USDT-SWAP",
    "ANC-USDT-SWAP",
)
BINANCE_SYMBOLS = {
    "LUNA-USDT-SWAP": ("LUNAUSDT", ("2022-05-01", "2022-04-01", "2022-03-01")),
    "FTT-USDT-SWAP": ("FTTUSDT", ("2022-11-01", "2022-10-01", "2022-09-01")),
    "SRM-USDT-SWAP": ("SRMUSDT", ("2022-11-01", "2022-10-01", "2022-09-01")),
    "ANC-USDT-SWAP": ("ANCUSDT", ("2022-05-01", "2022-04-01", "2022-03-01")),
}


def ms_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def parse_candle_ts(row: Any) -> int | None:
    if isinstance(row, list) and row:
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None
    return None


def parse_funding_ts(row: Any) -> int | None:
    if isinstance(row, dict):
        try:
            return int(row.get("fundingTime"))
        except (TypeError, ValueError):
            return None
    return None


def summarize_timestamps(data: list[Any], parser: Callable[[Any], int | None]) -> dict[str, Any]:
    timestamps = [ts for ts in (parser(row) for row in data) if ts is not None]
    if not timestamps:
        return {"rows": len(data), "earliest_ms": None, "latest_ms": None}
    return {
        "rows": len(data),
        "earliest_ms": min(timestamps),
        "latest_ms": max(timestamps),
        "earliest": ms_to_iso(min(timestamps)),
        "latest": ms_to_iso(max(timestamps)),
    }


def request_okx(
    path: str,
    params: dict[str, Any],
    timeout: float,
    sleep_seconds: float,
) -> dict[str, Any]:
    url = f"{OKX_BASE}{path}"
    try:
        response = requests.get(url, params=params, timeout=timeout)
        status = response.status_code
        payload = response.json()
    except Exception as exc:  # pragma: no cover - network diagnostic path
        time.sleep(sleep_seconds)
        return {
            "endpoint": path,
            "params": params,
            "http_status": None,
            "okx_code": None,
            "okx_msg": str(exc),
            "data": [],
        }
    time.sleep(sleep_seconds)
    return {
        "endpoint": path,
        "params": params,
        "http_status": status,
        "okx_code": payload.get("code") if isinstance(payload, dict) else None,
        "okx_msg": payload.get("msg") if isinstance(payload, dict) else None,
        "data": payload.get("data", []) if isinstance(payload, dict) else [],
    }


def find_older_cursor(
    path: str,
    base_params: dict[str, Any],
    cursor_ms: int,
    parser: Callable[[Any], int | None],
    timeout: float,
    sleep_seconds: float,
) -> tuple[str | None, dict[str, Any] | None]:
    for cursor_name in ("before", "after"):
        params = dict(base_params)
        params[cursor_name] = str(cursor_ms)
        probe = request_okx(path, params, timeout, sleep_seconds)
        rows = probe["data"]
        timestamps = [ts for ts in (parser(row) for row in rows) if ts is not None]
        if timestamps and min(timestamps) < cursor_ms:
            return cursor_name, probe
    return None, None


def probe_paginated_endpoint(
    path: str,
    base_params: dict[str, Any],
    parser: Callable[[Any], int | None],
    timeout: float,
    sleep_seconds: float,
    max_pages: int,
) -> dict[str, Any]:
    first = request_okx(path, base_params, timeout, sleep_seconds)
    all_rows = list(first["data"])
    timestamps = [ts for ts in (parser(row) for row in all_rows) if ts is not None]
    cursor_name = None
    next_probe = None

    if timestamps and max_pages > 1:
        cursor_name, next_probe = find_older_cursor(
            path,
            base_params,
            min(timestamps),
            parser,
            timeout,
            sleep_seconds,
        )

    pages = 1
    while cursor_name and next_probe and pages < max_pages:
        rows = next_probe["data"]
        if not rows:
            break
        previous_earliest = min(timestamps) if timestamps else None
        new_timestamps = [ts for ts in (parser(row) for row in rows) if ts is not None]
        if not new_timestamps or (previous_earliest is not None and min(new_timestamps) >= previous_earliest):
            break
        all_rows.extend(rows)
        timestamps.extend(new_timestamps)
        pages += 1
        params = dict(base_params)
        params[cursor_name] = str(min(timestamps))
        next_probe = request_okx(path, params, timeout, sleep_seconds)

    summary = summarize_timestamps(all_rows, parser)
    return {
        "endpoint": path,
        "base_params": base_params,
        "first_http_status": first["http_status"],
        "first_okx_code": first["okx_code"],
        "first_okx_msg": first["okx_msg"],
        "pagination_cursor": cursor_name,
        "pages": pages,
        **summary,
    }


def probe_current_instruments_via_ccxt() -> dict[str, Any]:
    exchange = ccxt.okx({"enableRateLimit": True})
    result: dict[str, Any] = {
        "source": "ccxt.okx.public_get_public_instruments",
        "inst_type": "SWAP",
    }
    try:
        payload = exchange.public_get_public_instruments({"instType": "SWAP"})
        data = payload.get("data", [])
        inst_ids = {row.get("instId") for row in data if isinstance(row, dict)}
        result.update(
            {
                "okx_code": payload.get("code"),
                "okx_msg": payload.get("msg"),
                "rows": len(data),
                "target_present": {inst_id: inst_id in inst_ids for inst_id in TARGET_INST_IDS},
            }
        )
    except Exception as exc:  # pragma: no cover - network diagnostic path
        result["error"] = str(exc)
    return result


def probe_instrument_listing_interfaces(timeout: float, sleep_seconds: float) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    param_sets = [{"instType": "SWAP"}]
    for state in ("live", "suspend", "preopen", "test", "expired", "all"):
        param_sets.append({"instType": "SWAP", "state": state})
    for inst_id in TARGET_INST_IDS:
        param_sets.append({"instType": "SWAP", "instId": inst_id})

    for params in param_sets:
        raw = request_okx("/api/v5/public/instruments", params, timeout, sleep_seconds)
        data = raw["data"]
        probes.append(
            {
                "endpoint": raw["endpoint"],
                "params": params,
                "http_status": raw["http_status"],
                "okx_code": raw["okx_code"],
                "okx_msg": raw["okx_msg"],
                "rows": len(data),
                "target_present": {
                    inst_id: any(row.get("instId") == inst_id for row in data if isinstance(row, dict))
                    for inst_id in TARGET_INST_IDS
                },
            }
        )

    for path in (
        "/api/v5/public/instruments-history",
        "/api/v5/public/instrument-history",
        "/api/v5/public/delivery-exercise-history",
    ):
        raw = request_okx(path, {"instType": "SWAP"}, timeout, sleep_seconds)
        probes.append(
            {
                "endpoint": raw["endpoint"],
                "params": raw["params"],
                "http_status": raw["http_status"],
                "okx_code": raw["okx_code"],
                "okx_msg": raw["okx_msg"],
                "rows": len(raw["data"]),
            }
        )
    return probes


def probe_delisted_contracts(
    timeout: float,
    sleep_seconds: float,
    max_pages: int,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for inst_id in TARGET_INST_IDS:
        out[inst_id] = {
            "history_candles": probe_paginated_endpoint(
                "/api/v5/market/history-candles",
                {"instId": inst_id, "bar": "1Dutc", "limit": "100"},
                parse_candle_ts,
                timeout,
                sleep_seconds,
                max_pages,
            ),
            "funding_rate_history": probe_paginated_endpoint(
                "/api/v5/public/funding-rate-history",
                {"instId": inst_id, "limit": "100"},
                parse_funding_ts,
                timeout,
                sleep_seconds,
                max_pages,
            ),
        }
    return out


def probe_binance_archives(timeout: float, sleep_seconds: float) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for inst_id, (symbol, dates) in BINANCE_SYMBOLS.items():
        rows = []
        for date in dates:
            url = (
                f"{BINANCE_DATA_BASE}/data/futures/um/daily/klines/"
                f"{symbol}/1d/{symbol}-1d-{date}.zip"
            )
            try:
                response = requests.head(url, timeout=timeout, allow_redirects=True)
                status = response.status_code
                size = response.headers.get("content-length")
            except Exception as exc:  # pragma: no cover - network diagnostic path
                status = None
                size = None
                rows.append({"url": url, "http_status": status, "error": str(exc)})
                time.sleep(sleep_seconds)
                continue
            rows.append({"url": url, "http_status": status, "content_length": size})
            time.sleep(sleep_seconds)
        out[inst_id] = rows
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args()

    result = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "targets": TARGET_INST_IDS,
        "current_instruments_ccxt": probe_current_instruments_via_ccxt(),
        "instrument_listing_probes": probe_instrument_listing_interfaces(args.timeout, args.sleep),
        "delisted_contract_probes": probe_delisted_contracts(
            args.timeout,
            args.sleep,
            args.max_pages,
        ),
        "binance_archive_head_probes": probe_binance_archives(args.timeout, args.sleep),
    }
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
