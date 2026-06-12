"""Bulk historical data downloader for the real-data Phase 5 run.

The driver keeps storage keyed by calendar ``asset_id`` so reused symbols never
collide on disk. It downloads OHLCV first, builds the dollar-volume panel from
local parquet only, constructs monthly universe history, then downloads funding
for OKX contracts that actually entered the historical pool.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from loguru import logger

from data.fetcher import fetch_funding_rate_history, fetch_ohlcv, make_okx_perp
from data.storage import (
    available_range,
    load_funding,
    load_ohlcv,
    save_funding,
    save_ohlcv,
)
from data.universe import (
    UniverseConfig,
    build_universe_history,
    calendar_delisting_dates,
    calendar_listing_dates,
    load_perp_calendar,
)
from data.validate import (
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
    fetcher=fetch_funding_rate_history,
    exchange=None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    downloaded: list[str] = []
    skipped: list[str] = []
    failures: list[dict[str, Any]] = []
    for i, asset_id in enumerate(pool_asset_ids, start=1):
        contract = contract_by_asset[asset_id]
        if contract.data_source != "okx":
            print(f"Stage D [{i}/{len(pool_asset_ids)}] skip funding {asset_id} source=binance_vision")
            skipped.append(asset_id)
            continue
        missing = missing_funding_range(
            asset_id,
            contract.fetch_start,
            contract.fetch_end,
            data_dir=data_dir,
        )
        if missing is None:
            print(f"Stage D [{i}/{len(pool_asset_ids)}] skip covered funding {asset_id}")
            downloaded.append(asset_id)
            continue
        since, until = missing
        print(f"Stage D [{i}/{len(pool_asset_ids)}] funding {asset_id} {_date(since)}..{_date(until)}")
        try:
            df = fetcher(contract.symbol, since=since, until=until, exchange=exchange)
            if df.empty:
                raise ValueError("empty funding response")
            save_funding(df, symbol=asset_id, data_dir=data_dir)
            downloaded.append(asset_id)
        except Exception as exc:  # noqa: BLE001 - keep the batch moving
            logger.exception("Funding download failed for {}", asset_id)
            failures.append(
                {
                    "stage": "funding",
                    "asset_id": asset_id,
                    "symbol": contract.symbol,
                    "since": _iso(since),
                    "until": _iso(until),
                    "error": str(exc),
                }
            )
    return downloaded, skipped, failures


def _gap_stats(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "start": None, "end": None, "missing_bars": None}
    expected = pd.date_range(df.index.min(), df.index.max(), freq="D", tz="UTC")
    missing = expected.difference(df.index)
    return {
        "rows": int(len(df)),
        "start": _date(df.index[0]),
        "end": _date(df.index[-1]),
        "missing_bars": int(len(missing)),
    }


def validate_downloads(
    asset_ids: list[str],
    universe_history: dict[pd.Timestamp, list[str]],
    calendar: pd.DataFrame,
    *,
    data_dir: str | Path,
    report_path: str | Path,
    config: UniverseConfig | None = None,
) -> ValidationReport:
    config = config or UniverseConfig()
    combined = ValidationReport()
    per_asset: dict[str, dict[str, Any]] = {}

    for asset_id in asset_ids:
        df = load_ohlcv(asset_id, data_dir=data_dir)
        rep = validate_ohlcv(df, timeframe="1d", symbol=asset_id)
        combined.extend(rep)
        per_asset[asset_id] = {
            "stats": _gap_stats(df),
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
        "| asset_id | rows | start | end | missing_bars | errors | warnings |",
        "|---|---:|---|---|---:|---:|---:|",
    ]
    for asset_id, item in sorted(per_asset.items()):
        stats = item["stats"]
        missing = stats["missing_bars"]
        lines.append(
            f"| {asset_id} | {stats['rows']} | {stats['start']} | {stats['end']} | "
            f"{'' if missing is None else missing} | {len(item['errors'])} | "
            f"{len(item['warnings'])} |"
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
    expected_start: pd.Timestamp,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        df = load_funding(asset_id, data_dir=data_dir)
        if df.empty:
            diagnostics.append({"asset_id": asset_id, "rows": 0, "first": None, "max_gap_hours": None})
            continue
        diffs = df.index.to_series().diff().dropna()
        max_gap = None if diffs.empty else float(diffs.max() / pd.Timedelta(hours=1))
        diagnostics.append(
            {
                "asset_id": asset_id,
                "rows": int(len(df)),
                "first": _date(df.index[0]),
                "last": _date(df.index[-1]),
                "reaches_expected_start": _utc(df.index[0]) <= expected_start + FUNDING_STEP,
                "max_gap_hours": max_gap,
            }
        )
    return diagnostics


def append_funding_diagnostics_to_report(report_path: str | Path, diagnostics: list[dict[str, Any]]) -> None:
    lines = ["", "## Funding Depth Diagnostics", ""]
    lines.extend(["| asset_id | rows | first | last | reaches start | max gap hours |", "|---|---:|---|---|---|---:|"])
    for item in diagnostics:
        lines.append(
            f"| {item['asset_id']} | {item['rows']} | {item.get('first')} | {item.get('last')} | "
            f"{item.get('reaches_expected_start')} | {item.get('max_gap_hours')} |"
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
        print(f"Stage D funding skipped binance_vision assets: {len(skipped_funding)}")
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
            expected_start=panel_start,
        )
        append_funding_diagnostics_to_report(reports_dir / "trial_source_comparison.md", diagnostics)
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
