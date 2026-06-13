"""Bulk historical data downloader for the real-data Phase 5 run.

The driver keeps storage keyed by calendar ``asset_id`` so reused symbols never
collide on disk. It downloads OHLCV first, builds the dollar-volume panel from
local parquet only, constructs monthly universe history, then downloads funding
from Binance Vision monthly archives for contracts that entered the historical
pool.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import requests  # noqa: E402
from loguru import logger  # noqa: E402

from data.fetcher import (  # noqa: E402
    fetch_binance_vision_funding,
    fetch_ohlcv,
    make_okx_perp,
    to_binance_usdt_symbol,
)
from data.storage import (  # noqa: E402
    available_range,
    load_funding,
    load_ohlcv,
    save_funding,
    save_ohlcv,
)
from data.universe import (  # noqa: E402
    UniverseConfig,
    build_universe_history,
    calendar_delisting_dates,
    calendar_listing_dates,
    load_perp_calendar,
)
from data.validate import (  # noqa: E402
    ValidationReport,
    check_delisted_symbol_coverage,
    check_universe_consistency,
    validate_ohlcv,
)

DEFAULT_START = "2020-01-01"
TRIAL_START = "2020-12-01"
TRIAL_END = "2021-01-31"
FUNDING_STEP = pd.Timedelta(hours=8)
OHLCV_STEP = pd.Timedelta(days=1)
COMPARISON_BASES = ("BTC", "ETH", "LTC", "XRP", "DOGE")


@dataclass(frozen=True)
class ContractDownload:
    asset_id: str
    symbol: str
    data_source: str
    binance_symbol: str | None
    listing_date: pd.Timestamp
    delisting_date: pd.Timestamp | None
    fetch_start: pd.Timestamp
    fetch_end: pd.Timestamp


def _utc(ts: Any) -> pd.Timestamp:
    out = pd.Timestamp(ts)
    return out.tz_localize("UTC") if out.tz is None else out.tz_convert("UTC")


def _date(ts: pd.Timestamp) -> str:
    return str(_utc(ts).strftime("%Y-%m-%d"))


def _iso(ts: pd.Timestamp | None) -> str | None:
    return None if ts is None else _utc(ts).isoformat()


def _today_utc() -> str:
    return str(pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"))


def _end_of_day(ts: pd.Timestamp) -> pd.Timestamp:
    return _utc(ts).normalize() + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)


def _month_end(ts: pd.Timestamp) -> pd.Timestamp:
    return (_utc(ts) + pd.offsets.MonthEnd(0)).normalize()


def history_buffer_days(config: UniverseConfig | None = None) -> int:
    config = config or UniverseConfig()
    return config.lookback_days + config.min_listing_days + 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START, help="Backtest start date, UTC")
    parser.add_argument("--end", default=_today_utc(), help="Backtest end date, UTC")
    parser.add_argument("--data-dir", default="data/historical", help="Parquet storage root")
    parser.add_argument("--trial", action="store_true", help="Run the fixed 2020-12/2021-01 trial")
    parser.add_argument("--symbols", help="Comma-separated asset_id subset for debugging")
    parser.add_argument("--skip-funding", action="store_true", help="Only download OHLCV")
    return parser.parse_args(argv)


def _selected_asset_ids(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def select_contracts_for_window(
    calendar: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    symbols: set[str] | None = None,
    config: UniverseConfig | None = None,
) -> list[ContractDownload]:
    """Return contracts whose lifecycle intersects ``[start-buffer, end]``."""
    config = config or UniverseConfig()
    fetch_floor = start - pd.Timedelta(days=history_buffer_days(config))
    contracts: list[ContractDownload] = []

    if symbols is not None:
        missing = sorted(symbols.difference(str(idx) for idx in calendar.index))
        if missing:
            raise ValueError(f"Unknown asset_id in --symbols: {missing}")

    for asset_id, row in calendar.iterrows():
        asset_id = str(asset_id)
        if symbols is not None and asset_id not in symbols:
            continue
        listing = row.get("listing_date")
        if pd.isna(listing):
            continue
        listing_ts = _utc(listing)
        raw_delisting = row.get("delisting_date")
        delisting_ts = None if pd.isna(raw_delisting) else _utc(raw_delisting)
        lifecycle_end = delisting_ts or end
        if listing_ts > end or lifecycle_end < fetch_floor:
            continue

        fetch_start = max(listing_ts, fetch_floor)
        fetch_end = min(lifecycle_end, end)
        if fetch_end < fetch_start:
            continue

        data_source = row.get("data_source", "okx")
        binance_symbol = row.get("binance_symbol")
        contracts.append(
            ContractDownload(
                asset_id=asset_id,
                symbol=str(row.get("symbol", asset_id)),
                data_source="okx" if pd.isna(data_source) else str(data_source),
                binance_symbol=None if pd.isna(binance_symbol) else str(binance_symbol),
                listing_date=listing_ts,
                delisting_date=delisting_ts,
                fetch_start=fetch_start,
                fetch_end=fetch_end,
            )
        )
    return contracts


def missing_ohlcv_range(
    asset_id: str,
    target_start: pd.Timestamp,
    target_end: pd.Timestamp,
    *,
    data_dir: str | Path,
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    existing = available_range(asset_id, timeframe="1d", data_dir=data_dir)
    if existing is None:
        return target_start, target_end
    stored_start, stored_end = existing
    stored_start = _utc(stored_start)
    stored_end = _utc(stored_end)
    if stored_start <= target_start and stored_end >= target_end:
        return None
    if stored_start <= target_start <= stored_end:
        resume_start = stored_end + OHLCV_STEP
        if resume_start > target_end:
            return None
        return resume_start, target_end
    return target_start, target_end


def _available_funding_range(
    asset_id: str,
    *,
    data_dir: str | Path,
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    df = load_funding(asset_id, data_dir=data_dir)
    if df.empty:
        return None
    return _utc(df.index[0]), _utc(df.index[-1])


def missing_funding_range(
    asset_id: str,
    target_start: pd.Timestamp,
    target_end: pd.Timestamp,
    *,
    data_dir: str | Path,
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    existing = _available_funding_range(asset_id, data_dir=data_dir)
    if existing is None:
        return target_start, target_end
    stored_start, stored_end = existing
    if stored_start <= target_start and stored_end >= target_end:
        return None
    if stored_start <= target_start <= stored_end:
        resume_start = stored_end + pd.Timedelta(milliseconds=1)
        if resume_start > target_end:
            return None
        return resume_start, target_end
    return target_start, target_end


def download_ohlcv_stage(
    contracts: list[ContractDownload],
    *,
    data_dir: str | Path,
    fetcher=fetch_ohlcv,
    exchange=None,
    session=None,
) -> tuple[list[str], list[dict[str, Any]]]:
    downloaded: list[str] = []
    failures: list[dict[str, Any]] = []
    client_session = session or requests.Session()
    for i, contract in enumerate(contracts, start=1):
        missing = missing_ohlcv_range(
            contract.asset_id,
            contract.fetch_start,
            contract.fetch_end,
            data_dir=data_dir,
        )
        if missing is None:
            print(f"Stage A [{i}/{len(contracts)}] skip covered {contract.asset_id}")
            downloaded.append(contract.asset_id)
            continue

        since, until = missing
        print(
            f"Stage A [{i}/{len(contracts)}] OHLCV {contract.asset_id} "
            f"{_date(since)}..{_date(until)} source={contract.data_source}"
        )
        try:
            df = fetcher(
                contract.symbol,
                timeframe="1d",
                since=since,
                until=until,
                exchange=exchange if contract.data_source == "okx" else None,
                source=contract.data_source,
                binance_symbol=contract.binance_symbol,
                session=client_session,
            )
            if df.empty:
                raise ValueError("empty OHLCV response")
            save_ohlcv(df, symbol=contract.asset_id, timeframe="1d", data_dir=data_dir)
            downloaded.append(contract.asset_id)
        except Exception as exc:  # noqa: BLE001 - keep the batch moving
            logger.exception("OHLCV download failed for {}", contract.asset_id)
            failures.append(
                {
                    "stage": "ohlcv",
                    "asset_id": contract.asset_id,
                    "symbol": contract.symbol,
                    "source": contract.data_source,
                    "since": _iso(since),
                    "until": _iso(until),
                    "error": str(exc),
                }
            )
    return downloaded, failures


def build_dollar_volume_panel_from_storage(
    asset_ids: list[str],
    *,
    data_dir: str | Path,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build close x volume panel from local OHLCV parquet only."""
    series_map: dict[str, pd.Series] = {}
    for asset_id in asset_ids:
        df = load_ohlcv(asset_id, start=start, end=end, data_dir=data_dir)
        if df.empty:
            continue
        idx = df.index.normalize()
        series = pd.Series((df["close"] * df["volume"]).to_numpy(), index=idx, name=asset_id)
        series_map[asset_id] = series[~series.index.duplicated(keep="last")]
    if not series_map:
        return pd.DataFrame()
    return pd.DataFrame(series_map).sort_index()


def write_universe_history(
    universe_history: dict[pd.Timestamp, list[str]],
    *,
    data_dir: str | Path,
) -> Path:
    path = Path(data_dir) / "universe_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {_date(month): members for month, members in sorted(universe_history.items())}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def count_delisted_members(members: list[str], calendar: pd.DataFrame) -> int:
    if not members:
        return 0
    rows = calendar.loc[[member for member in members if member in calendar.index]]
    if rows.empty:
        return 0
    return int(rows["delisting_date"].notna().sum())


def download_funding_stage(
    pool_asset_ids: list[str],
    contract_by_asset: dict[str, ContractDownload],
    *,
    data_dir: str | Path,
    fetcher=fetch_binance_vision_funding,
    exchange=None,
    session=None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    del exchange  # Backtest-period funding uses Binance Vision archives by policy.
    downloaded: list[str] = []
    skipped: list[str] = []
    failures: list[dict[str, Any]] = []
    client_session = session or requests.Session()
    for i, asset_id in enumerate(pool_asset_ids, start=1):
        contract = contract_by_asset[asset_id]
        binance_symbol = contract.binance_symbol or to_binance_usdt_symbol(contract.symbol)
        funding_end = _end_of_day(contract.fetch_end)
        missing = missing_funding_range(
            asset_id,
            contract.fetch_start,
            funding_end,
            data_dir=data_dir,
        )
        if missing is None:
            print(f"Stage D [{i}/{len(pool_asset_ids)}] skip covered funding {asset_id}")
            downloaded.append(asset_id)
            continue
        since, until = missing
        print(
            f"Stage D [{i}/{len(pool_asset_ids)}] funding {asset_id} "
            f"{_date(since)}..{_date(until)} binance_symbol={binance_symbol}"
        )
        try:
            df = fetcher(binance_symbol, since=since, until=until, session=client_session)
            if df.empty:
                print(f"Stage D [{i}/{len(pool_asset_ids)}] funding archive gap {asset_id}")
                skipped.append(asset_id)
                continue
            save_funding(df, symbol=asset_id, data_dir=data_dir)
            downloaded.append(asset_id)
        except Exception as exc:  # noqa: BLE001 - keep the batch moving
            logger.exception("Funding download failed for {}", asset_id)
            failures.append(
                {
                    "stage": "funding",
                    "asset_id": asset_id,
                    "symbol": binance_symbol,
                    "since": _iso(since),
                    "until": _iso(until),
                    "error": str(exc),
                }
            )
    return downloaded, skipped, failures


def _gap_stats(
    df: pd.DataFrame,
    expected_range: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> dict[str, Any]:
    expected_start, expected_end = (None, None) if expected_range is None else expected_range
    if df.empty:
        return {
            "rows": 0,
            "start": None,
            "end": None,
            "missing_bars": None,
            "expected_start": _date(expected_start) if expected_start is not None else None,
            "expected_end": _date(expected_end) if expected_end is not None else None,
            "start_shortfall_days": None,
            "end_shortfall_days": None,
        }
    expected = pd.date_range(df.index.min(), df.index.max(), freq="D", tz="UTC")
    missing = expected.difference(df.index)
    start_shortfall = None
    end_shortfall = None
    if expected_start is not None:
        start_shortfall = max(0, int((_utc(df.index[0]) - _utc(expected_start)).days))
    if expected_end is not None:
        end_shortfall = max(0, int((_utc(expected_end) - _utc(df.index[-1])).days))
    return {
        "rows": int(len(df)),
        "start": _date(df.index[0]),
        "end": _date(df.index[-1]),
        "missing_bars": int(len(missing)),
        "expected_start": _date(expected_start) if expected_start is not None else None,
        "expected_end": _date(expected_end) if expected_end is not None else None,
        "start_shortfall_days": start_shortfall,
        "end_shortfall_days": end_shortfall,
    }


def _in_pool_days_by_asset(
    universe_history: dict[pd.Timestamp, list[str]],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, set[pd.Timestamp]]:
    start_day = _utc(start).normalize()
    end_day = _utc(end).normalize()
    out: dict[str, set[pd.Timestamp]] = {}
    for month, members in universe_history.items():
        month_start = _utc(month).normalize()
        window_start = max(month_start, start_day)
        window_end = min(_month_end(month_start), end_day)
        if window_end < window_start:
            continue
        days = set(pd.date_range(window_start, window_end, freq="D", tz="UTC"))
        for asset_id in members:
            out.setdefault(asset_id, set()).update(days)
    return out


def funding_coverage_stats(
    pool_asset_ids: list[str],
    universe_history: dict[pd.Timestamp, list[str]],
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    data_dir: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    in_pool_days = _in_pool_days_by_asset(universe_history, start=start, end=end)
    rows: list[dict[str, Any]] = []
    total_in_pool_days = 0
    total_covered_days = 0
    total_rows = 0
    for asset_id in sorted(pool_asset_ids):
        days = in_pool_days.get(asset_id, set())
        funding = load_funding(asset_id, start=start, end=_end_of_day(end), data_dir=data_dir)
        funding_days = set(funding.index.normalize()) if not funding.empty else set()
        covered_days = len(days & funding_days)
        in_pool_count = len(days)
        total_in_pool_days += in_pool_count
        total_covered_days += covered_days
        total_rows += int(len(funding))
        fraction = None if in_pool_count == 0 else covered_days / in_pool_count
        rows.append(
            {
                "asset_id": asset_id,
                "in_pool_days": in_pool_count,
                "covered_days": covered_days,
                "coverage_fraction": fraction,
                "funding_rows": int(len(funding)),
                "first_funding": None if funding.empty else _date(funding.index[0]),
                "last_funding": None if funding.empty else _date(funding.index[-1]),
            }
        )
    aggregate = {
        "in_pool_days": total_in_pool_days,
        "covered_days": total_covered_days,
        "coverage_fraction": None if total_in_pool_days == 0 else total_covered_days / total_in_pool_days,
        "funding_rows": total_rows,
    }
    return rows, aggregate


def validate_downloads(
    asset_ids: list[str],
    universe_history: dict[pd.Timestamp, list[str]],
    calendar: pd.DataFrame,
    *,
    data_dir: str | Path,
    report_path: str | Path,
    config: UniverseConfig | None = None,
    expected_ranges: dict[str, tuple[pd.Timestamp, pd.Timestamp]] | None = None,
    funding_pool_asset_ids: list[str] | None = None,
    funding_start: pd.Timestamp | None = None,
    funding_end: pd.Timestamp | None = None,
) -> ValidationReport:
    config = config or UniverseConfig()
    combined = ValidationReport()
    per_asset: dict[str, dict[str, Any]] = {}
    funding_rows: list[dict[str, Any]] = []
    funding_aggregate: dict[str, Any] | None = None

    for asset_id in asset_ids:
        df = load_ohlcv(asset_id, data_dir=data_dir)
        rep = validate_ohlcv(df, timeframe="1d", symbol=asset_id)
        stats = _gap_stats(df, None if expected_ranges is None else expected_ranges.get(asset_id))
        if expected_ranges is not None and asset_id in expected_ranges:
            if df.empty:
                rep.add_error(f"[{asset_id}] coverage missing all expected data")
            elif (stats["start_shortfall_days"] or 0) > 3 or (stats["end_shortfall_days"] or 0) > 3:
                rep.add_error(
                    f"[{asset_id}] coverage boundary shortfall exceeds 3 days "
                    f"(expected {stats['expected_start']}~{stats['expected_end']}, "
                    f"actual {stats['start']}~{stats['end']}, "
                    f"start_shortfall={stats['start_shortfall_days']}, "
                    f"end_shortfall={stats['end_shortfall_days']})"
                )
        combined.extend(rep)
        per_asset[asset_id] = {
            "stats": stats,
            "errors": rep.errors,
            "warnings": rep.warnings,
        }

    universe_report = check_universe_consistency(
        universe_history,
        calendar_listing_dates(calendar),
        min_listing_days=config.min_listing_days,
        delisting_dates=calendar_delisting_dates(calendar),
    )
    delisted_report = check_delisted_symbol_coverage(calendar)
    combined.extend(universe_report)
    combined.extend(delisted_report)

    if funding_pool_asset_ids is not None and funding_start is not None and funding_end is not None:
        funding_rows, funding_aggregate = funding_coverage_stats(
            funding_pool_asset_ids,
            universe_history,
            start=funding_start,
            end=funding_end,
            data_dir=data_dir,
        )
        for row in funding_rows:
            if row["in_pool_days"] and row["covered_days"] < row["in_pool_days"]:
                fraction = row["coverage_fraction"]
                combined.add_warning(
                    f"[{row['asset_id']}] funding coverage "
                    f"{0.0 if fraction is None else fraction:.4f} "
                    f"({row['covered_days']}/{row['in_pool_days']} in-pool days); "
                    "missing archive days treated as 0"
                )

    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Download Validation",
        "",
        f"Overall: {'PASS' if combined.ok else 'FAIL'}",
        f"OHLCV assets checked: {len(asset_ids)}",
        f"Validation errors: {len(combined.errors)}",
        f"Validation warnings: {len(combined.warnings)}",
        "",
        "## Universe Checks",
        "",
        f"- Consistency errors: {len(universe_report.errors)}",
        f"- Consistency warnings: {len(universe_report.warnings)}",
        f"- Delisted coverage errors: {len(delisted_report.errors)}",
        f"- Delisted coverage warnings: {len(delisted_report.warnings)}",
        "",
        "## Per-Asset Gap Stats",
        "",
        "| asset_id | rows | expected_start | actual_start | expected_end | actual_end | start_shortfall_days | end_shortfall_days | missing_bars | errors | warnings |",
        "|---|---:|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for asset_id, item in sorted(per_asset.items()):
        stats = item["stats"]
        missing = stats["missing_bars"]
        lines.append(
            f"| {asset_id} | {stats['rows']} | {stats['expected_start']} | {stats['start']} | "
            f"{stats['expected_end']} | {stats['end']} | "
            f"{'' if stats['start_shortfall_days'] is None else stats['start_shortfall_days']} | "
            f"{'' if stats['end_shortfall_days'] is None else stats['end_shortfall_days']} | "
            f"{'' if missing is None else missing} | {len(item['errors'])} | "
            f"{len(item['warnings'])} |"
        )

    if funding_aggregate is not None:
        coverage = funding_aggregate["coverage_fraction"]
        lines.extend(
            [
                "",
                "## Funding Coverage",
                "",
                "Funding uses Binance Vision monthly fundingRate archives for all pool assets. "
                "Archive gaps are warning-level; downstream research treats missing funding as 0. "
                "Trailing partial months can lag archive publication.",
                "",
                f"Aggregate in-pool day coverage: "
                f"{'n/a' if coverage is None else f'{coverage:.4f}'} "
                f"({funding_aggregate['covered_days']}/{funding_aggregate['in_pool_days']} days, "
                f"{funding_aggregate['funding_rows']} rows)",
                "",
                "| asset_id | in_pool_days | covered_days | coverage_fraction | funding_rows | first_funding | last_funding |",
                "|---|---:|---:|---:|---:|---|---|",
            ]
        )
        for row in funding_rows:
            fraction = row["coverage_fraction"]
            lines.append(
                f"| {row['asset_id']} | {row['in_pool_days']} | {row['covered_days']} | "
                f"{'' if fraction is None else f'{fraction:.4f}'} | {row['funding_rows']} | "
                f"{row['first_funding']} | {row['last_funding']} |"
            )

    if combined.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {msg}" for msg in combined.errors)
    if combined.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {msg}" for msg in combined.warnings)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return combined


def write_failures(failures: list[dict[str, Any]], *, reports_dir: str | Path = "reports") -> Path:
    path = Path(reports_dir) / "download_failures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(failures, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _find_okx_symbol_for_base(calendar: pd.DataFrame, base: str) -> str:
    rows = calendar[
        (calendar.get("base") == base)
        & (calendar["data_source"] == "okx")
        & calendar["delisting_date"].isna()
    ]
    if rows.empty:
        rows = calendar[
            calendar["symbol"].astype(str).str.startswith(f"{base}/")
            & (calendar["data_source"] == "okx")
            & calendar["delisting_date"].isna()
        ]
    if rows.empty:
        raise KeyError(f"No active OKX calendar row for {base}")
    return str(rows.iloc[0]["symbol"])


def run_trial_source_comparison(
    calendar: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    report_path: str | Path,
    exchange=None,
    session=None,
) -> list[dict[str, Any]]:
    exchange = exchange or make_okx_perp()
    client_session = session or requests.Session()
    results: list[dict[str, Any]] = []
    for base in COMPARISON_BASES:
        okx_symbol = _find_okx_symbol_for_base(calendar, base)
        binance_symbol = f"{base}USDT"
        okx = fetch_ohlcv(
            okx_symbol,
            timeframe="1d",
            since=start,
            until=end,
            exchange=exchange,
            source="okx",
        )
        binance = fetch_ohlcv(
            okx_symbol,
            timeframe="1d",
            since=start,
            until=end,
            source="binance_vision",
            binance_symbol=binance_symbol,
            session=client_session,
        )
        joined = pd.DataFrame({"okx": okx["close"], "binance": binance["close"]}).dropna()
        okx_ret = joined["okx"].pct_change()
        binance_ret = joined["binance"].pct_change()
        aligned = pd.DataFrame({"okx": okx_ret, "binance": binance_ret}).dropna()
        diff = aligned["okx"] - aligned["binance"]
        okx_boundary = bool((okx.index == okx.index.normalize()).all()) if not okx.empty else False
        binance_boundary = (
            bool((binance.index == binance.index.normalize()).all()) if not binance.empty else False
        )
        results.append(
            {
                "base": base,
                "okx_rows": int(len(okx)),
                "binance_rows": int(len(binance)),
                "aligned_return_rows": int(len(aligned)),
                "mean_return_diff_bp": None if diff.empty else float(diff.mean() * 10_000),
                "max_abs_return_diff_bp": None if diff.empty else float(diff.abs().max() * 10_000),
                "return_correlation": None if len(aligned) < 2 else float(aligned["okx"].corr(aligned["binance"])),
                "bar_boundary_aligned": okx_boundary and binance_boundary,
            }
        )

    _write_source_comparison_report(results, report_path=report_path, start=start, end=end)
    return results


def _write_source_comparison_report(
    results: list[dict[str, Any]],
    *,
    report_path: str | Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> None:
    min_corr = min(
        (row["return_correlation"] for row in results if row["return_correlation"] is not None),
        default=None,
    )
    lines = [
        "# Trial Source Comparison",
        "",
        f"Window: {_date(start)} to {_date(end)}",
        "OKX daily candles are requested through ccxt with UTC timezone defaults; "
        "ccxt maps daily OKX bars to the UTC candle key for >=6h timeframes.",
        "",
        f"Minimum return correlation: {min_corr:.6f}" if min_corr is not None else "Minimum return correlation: n/a",
        "",
        "| base | OKX rows | Binance rows | aligned returns | mean diff bp | max abs diff bp | corr | UTC boundary |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in results:
        corr = row["return_correlation"]
        mean_diff = row["mean_return_diff_bp"]
        max_diff = row["max_abs_return_diff_bp"]
        lines.append(
            f"| {row['base']} | {row['okx_rows']} | {row['binance_rows']} | "
            f"{row['aligned_return_rows']} | "
            f"{'' if mean_diff is None else f'{mean_diff:.4f}'} | "
            f"{'' if max_diff is None else f'{max_diff:.4f}'} | "
            f"{'' if corr is None else f'{corr:.6f}'} | "
            f"{'yes' if row['bar_boundary_aligned'] else 'no'} |"
        )
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def funding_depth_diagnostics(
    asset_ids: list[str],
    *,
    data_dir: str | Path,
    expected_start: pd.Timestamp | None = None,
    expected_starts: dict[str, pd.Timestamp] | None = None,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        target_start = None if expected_starts is None else expected_starts.get(asset_id)
        target_start = expected_start if target_start is None else target_start
        df = load_funding(asset_id, data_dir=data_dir)
        if df.empty:
            diagnostics.append(
                {
                    "asset_id": asset_id,
                    "rows": 0,
                    "expected_start": None if target_start is None else _date(target_start),
                    "first": None,
                    "last": None,
                    "reaches_expected_start": False,
                    "max_gap_hours": None,
                }
            )
            continue
        diffs = df.index.to_series().diff().dropna()
        max_gap = None if diffs.empty else float(diffs.max() / pd.Timedelta(hours=1))
        diagnostics.append(
            {
                "asset_id": asset_id,
                "rows": int(len(df)),
                "expected_start": None if target_start is None else _date(target_start),
                "first": _date(df.index[0]),
                "last": _date(df.index[-1]),
                "reaches_expected_start": (
                    True if target_start is None else _utc(df.index[0]) <= _utc(target_start) + FUNDING_STEP
                ),
                "max_gap_hours": max_gap,
            }
        )
    return diagnostics


def append_funding_diagnostics_to_report(report_path: str | Path, diagnostics: list[dict[str, Any]]) -> None:
    lines = ["", "## Funding Depth Diagnostics", ""]
    lines.extend(
        [
            "| asset_id | rows | expected_start | first | last | reaches start | max gap hours |",
            "|---|---:|---|---|---|---|---:|",
        ]
    )
    for item in diagnostics:
        lines.append(
            f"| {item['asset_id']} | {item['rows']} | {item.get('expected_start')} | "
            f"{item.get('first')} | {item.get('last')} | {item.get('reaches_expected_start')} | "
            f"{item.get('max_gap_hours')} |"
        )
    path = Path(report_path)
    with path.open("a", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")


FUNDING_ARCHIVE_PROBES = (
    ("BTC archive start", "BTCUSDT", "2020-08-01", "2020-08-10", "2020-08-01"),
    ("ETH archive start", "ETHUSDT", "2020-08-01", "2020-08-10", "2020-08-01"),
    ("LUNA 2021 coverage", "LUNAUSDT", "2021-01-28", "2021-02-05", "2021-01-28"),
    ("FTT pre-2022-04 gap", "FTTUSDT", "2021-09-01", "2022-04-10", "2021-09-01"),
)


def funding_archive_probe_diagnostics(
    session=None,
) -> list[dict[str, Any]]:
    client_session = session or requests.Session()
    diagnostics: list[dict[str, Any]] = []
    for label, symbol, since, until, expected_start in FUNDING_ARCHIVE_PROBES:
        start = _utc(since)
        expected = _utc(expected_start)
        try:
            df = fetch_binance_vision_funding(
                symbol,
                since=since,
                until=until,
                session=client_session,
            )
        except Exception as exc:  # noqa: BLE001 - probe diagnostics should not mask prior report output
            diagnostics.append(
                {
                    "label": label,
                    "symbol": symbol,
                    "rows": 0,
                    "expected_start": _date(expected),
                    "first": None,
                    "last": None,
                    "max_gap_hours": None,
                    "start_gap_days": None,
                    "error": str(exc),
                }
            )
            continue

        if df.empty:
            diagnostics.append(
                {
                    "label": label,
                    "symbol": symbol,
                    "rows": 0,
                    "expected_start": _date(expected),
                    "first": None,
                    "last": None,
                    "max_gap_hours": None,
                    "start_gap_days": None,
                    "error": None,
                }
            )
            continue

        diffs = df.index.to_series().diff().dropna()
        max_gap = None if diffs.empty else float(diffs.max() / pd.Timedelta(hours=1))
        start_gap = max(0, int((_utc(df.index[0]).normalize() - start.normalize()).days))
        diagnostics.append(
            {
                "label": label,
                "symbol": symbol,
                "rows": int(len(df)),
                "expected_start": _date(expected),
                "first": _date(df.index[0]),
                "last": _date(df.index[-1]),
                "max_gap_hours": max_gap,
                "start_gap_days": start_gap,
                "error": None,
            }
        )
    return diagnostics


def append_funding_archive_probes_to_report(
    report_path: str | Path,
    diagnostics: list[dict[str, Any]],
) -> None:
    lines = [
        "",
        "## Funding Archive Probes",
        "",
        "| probe | symbol | rows | expected_start | first | last | start_gap_days | max_gap_hours | error |",
        "|---|---|---:|---|---|---|---:|---:|---|",
    ]
    for item in diagnostics:
        lines.append(
            f"| {item['label']} | {item['symbol']} | {item['rows']} | "
            f"{item.get('expected_start')} | {item.get('first')} | {item.get('last')} | "
            f"{item.get('start_gap_days')} | {item.get('max_gap_hours')} | "
            f"{item.get('error')} |"
        )
    path = Path(report_path)
    with path.open("a", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")


def _trial_delisted_check(
    universe_history: dict[pd.Timestamp, list[str]],
    calendar: pd.DataFrame,
) -> tuple[list[str], int]:
    jan = pd.Timestamp("2021-01-01")
    members = universe_history.get(jan) or universe_history.get(jan.tz_localize("UTC")) or []
    return members, count_delisted_members(members, calendar)


def _print_universe_summary(universe_history: dict[pd.Timestamp, list[str]], calendar: pd.DataFrame) -> None:
    for month, members in sorted(universe_history.items()):
        print(
            f"Stage C universe {month.strftime('%Y-%m-%d')}: "
            f"pool_size={len(members)} delisted_members={count_delisted_members(members, calendar)}"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = UniverseConfig()
    start = _utc(TRIAL_START if args.trial else args.start)
    end = _utc(TRIAL_END if args.trial else args.end)
    data_dir = Path(args.data_dir)
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"Download window: {_date(start)}..{_date(end)} trial={args.trial}")
    print(f"Data dir: {data_dir}")
    calendar = load_perp_calendar()
    selected = _selected_asset_ids(args.symbols)
    contracts = select_contracts_for_window(calendar, start, end, symbols=selected, config=config)
    print(f"Stage A contracts selected: {len(contracts)}")

    okx_exchange = make_okx_perp() if any(c.data_source == "okx" for c in contracts) else None
    downloaded_asset_ids, failures = download_ohlcv_stage(
        contracts,
        data_dir=data_dir,
        exchange=okx_exchange,
    )
    write_failures(failures, reports_dir=reports_dir)

    print("Stage B building local dollar-volume panel")
    panel_start = start - pd.Timedelta(days=history_buffer_days(config))
    panel = build_dollar_volume_panel_from_storage(
        downloaded_asset_ids,
        data_dir=data_dir,
        start=panel_start,
        end=end,
    )
    if panel.empty:
        failures.append({"stage": "panel", "error": "empty dollar-volume panel"})
        write_failures(failures, reports_dir=reports_dir)
        return 1
    print(f"Stage B panel shape: rows={panel.shape[0]} columns={panel.shape[1]}")

    print("Stage C building universe history")
    universe_history = build_universe_history(panel, start=start, end=end, calendar=calendar, config=config)
    universe_path = write_universe_history(universe_history, data_dir=data_dir)
    print(f"Stage C wrote {universe_path}")
    _print_universe_summary(universe_history, calendar)

    contract_by_asset = {contract.asset_id: contract for contract in contracts}
    expected_ohlcv_ranges = {
        contract.asset_id: (contract.fetch_start, contract.fetch_end)
        for contract in contracts
        if contract.asset_id in downloaded_asset_ids
    }
    pool_asset_ids = sorted({asset_id for members in universe_history.values() for asset_id in members})
    funding_failures: list[dict[str, Any]] = []
    if args.skip_funding:
        print("Stage D funding skipped by --skip-funding")
    else:
        print(f"Stage D funding assets selected: {len(pool_asset_ids)}")
        _, skipped_funding, funding_failures = download_funding_stage(
            pool_asset_ids,
            contract_by_asset,
            data_dir=data_dir,
            exchange=okx_exchange,
        )
        print(f"Stage D funding archive-gap assets: {len(skipped_funding)}")
        failures.extend(funding_failures)
        write_failures(failures, reports_dir=reports_dir)

    print("Stage E validating downloads and universe")
    validation = validate_downloads(
        downloaded_asset_ids,
        universe_history,
        calendar,
        data_dir=data_dir,
        report_path=reports_dir / "download_validation.md",
        config=config,
        expected_ranges=expected_ohlcv_ranges,
        funding_pool_asset_ids=None if args.skip_funding else pool_asset_ids,
        funding_start=start,
        funding_end=end,
    )
    print(validation.summary())

    trial_failed = False
    if args.trial:
        members, delisted_count = _trial_delisted_check(universe_history, calendar)
        print(f"Trial 2021-01 pool: size={len(members)} delisted_count={delisted_count}")
        print("Trial 2021-01 members:", ", ".join(members))
        if delisted_count < 5:
            trial_failed = True
            failures.append(
                {
                    "stage": "trial",
                    "error": f"2021-01 pool delisted_count {delisted_count} < 5",
                }
            )

        comparison = run_trial_source_comparison(
            calendar,
            start=start,
            end=end,
            report_path=reports_dir / "trial_source_comparison.md",
            exchange=okx_exchange,
        )
        min_corr = min(
            (row["return_correlation"] for row in comparison if row["return_correlation"] is not None),
            default=None,
        )
        print(f"Trial source comparison min_corr={min_corr}")
        if min_corr is None or min_corr < 0.99:
            trial_failed = True
            failures.append({"stage": "trial", "error": f"source comparison min_corr {min_corr}"})

        diagnostics = funding_depth_diagnostics(
            ["BTC-20191112", "ETH-20191112"],
            data_dir=data_dir,
            expected_starts={
                "BTC-20191112": pd.Timestamp("2020-08-01", tz="UTC"),
                "ETH-20191112": pd.Timestamp("2020-08-01", tz="UTC"),
            },
        )
        append_funding_diagnostics_to_report(reports_dir / "trial_source_comparison.md", diagnostics)
        archive_probes = funding_archive_probe_diagnostics()
        append_funding_archive_probes_to_report(reports_dir / "trial_source_comparison.md", archive_probes)
        print("Trial funding archive probes:", archive_probes)
        if not args.skip_funding:
            for item in diagnostics:
                if item["rows"] == 0 or not item.get("reaches_expected_start"):
                    trial_failed = True
                    failures.append({"stage": "trial", "error": f"funding depth failed: {item}"})
        write_failures(failures, reports_dir=reports_dir)

    if failures:
        print(f"Completed with {len(failures)} failure(s); see {reports_dir / 'download_failures.json'}")
    if not validation.ok:
        print(f"Validation failed; see {reports_dir / 'download_validation.md'}")
    return 1 if failures or not validation.ok or trial_failed else 0


if __name__ == "__main__":
    sys.exit(main())
