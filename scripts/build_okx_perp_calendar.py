"""Build a reproducible USDT perpetual contract calendar.

The OKX public instruments endpoint only lists currently known instruments. Per
the delisted-contract probe, removed contracts such as old LUNA/FTT/SRM/ANC are not
queryable by old OKX instId. This script therefore combines:

1. current OKX USDT swap instruments from public REST; and
2. a small curated set of delisted asset generations whose OHLCV fallback is Binance
   data.binance.vision.

The output is intentionally small JSON metadata committed to git, not market
data. It is used to make universe construction timestamp-aware. Calendar rows
use asset_id, not symbol, as the unique key because symbols such as LUNA can be
reused for different asset generations.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments"
DEFAULT_OUTPUT = Path("metadata/okx_perp_calendar.json")

# Dates are UTC dates. They are deliberately lifecycle metadata, not returns.
# OHLCV for these symbols is fetched from Binance Vision because OKX no longer
# recognizes these old instIds according to docs/probe_okx_delisted_results.md.
KNOWN_DELISTED_FALLBACKS: tuple[dict[str, Any], ...] = (
    {
        "inst_id": "LUNA-USDT-SWAP",
        "symbol": "LUNA/USDT:USDT",
        "base": "LUNA",
        "quote": "USDT",
        "settle": "USDT",
        "listing_date": "2019-07-26",
        "delisting_date": "2022-05-13",
        "data_source": "binance_vision",
        "binance_symbol": "LUNAUSDT",
        "notes": (
            "Old LUNA generation from Binance Vision; do not concatenate with OKX "
            "current LUNA listed 2022-05-28."
        ),
    },
    {
        "inst_id": "FTT-USDT-SWAP",
        "symbol": "FTT/USDT:USDT",
        "base": "FTT",
        "quote": "USDT",
        "settle": "USDT",
        "listing_date": "2022-05-01",
        "delisting_date": "2022-11-14",
        "data_source": "binance_vision",
        "binance_symbol": "FTTUSDT",
        "notes": "OKX REST returns 51001 for old instId; Binance Vision daily klines exist.",
    },
    {
        "inst_id": "SRM-USDT-SWAP",
        "symbol": "SRM/USDT:USDT",
        "base": "SRM",
        "quote": "USDT",
        "settle": "USDT",
        "listing_date": "2021-01-01",
        "delisting_date": "2022-11-14",
        "data_source": "binance_vision",
        "binance_symbol": "SRMUSDT",
        "notes": "OKX REST returns 51001 for old instId; Binance Vision daily klines exist.",
    },
    {
        "inst_id": "ANC-USDT-SWAP",
        "symbol": "ANC/USDT:USDT",
        "base": "ANC",
        "quote": "USDT",
        "settle": "USDT",
        "listing_date": "2022-04-01",
        "delisting_date": "2022-05-13",
        "data_source": "binance_vision",
        "binance_symbol": "ANCUSDT",
        "notes": "OKX REST returns 51001 for old instId; Binance Vision daily klines exist.",
    },
)


def ms_to_date(value: str | int | None) -> str | None:
    if value in (None, "", "0"):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date().isoformat()


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


def fetch_current_okx_usdt_swaps(timeout: float = 20.0) -> list[dict[str, Any]]:
    response = requests.get(
        OKX_INSTRUMENTS_URL,
        params={"instType": "SWAP"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX instruments failed: {payload.get('code')} {payload.get('msg')}")
    entries = [normalize_okx_instrument(row) for row in payload.get("data", [])]
    return sorted((entry for entry in entries if entry is not None), key=lambda x: x["symbol"])


def build_calendar(timeout: float = 20.0) -> dict[str, Any]:
    current = fetch_current_okx_usdt_swaps(timeout=timeout)
    by_asset = {entry["asset_id"]: entry for entry in current}
    for fallback in KNOWN_DELISTED_FALLBACKS:
        entry = with_asset_id(fallback)
        by_asset.setdefault(entry["asset_id"], entry)
    entries = sorted(by_asset.values(), key=lambda x: (x["symbol"], x["asset_id"]))
    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "schema_version": 2,
        "notes": (
            "Current OKX instruments plus curated Binance Vision fallback asset generations "
            "for OKX-unavailable delisted contracts. asset_id is the stable universe key; "
            "symbol is only the exchange fetch symbol. See docs/probe_okx_delisted_results.md."
        ),
        "contracts": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    calendar = build_calendar(timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(calendar, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(calendar['contracts'])} contracts to {args.output}")


if __name__ == "__main__":
    main()
