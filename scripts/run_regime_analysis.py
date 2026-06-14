"""阶段 3 真实数据 regime 分析驱动（in-sample 2020-01 ~ 2024-06）。

把阶段 3 的市场状态模块接到真实、动态池掩码后的 OKX 日频数据上，产出：

- reports/regime_timeline_is.png
- reports/regime_summary_is.md

诚实护栏：
- HMM 状态数写死为 3，不为历史崩盘反向调参。
- SpikeConfig 使用默认值。
- expanding regime 是唯一可用于下游的无前视标签。
- full-sample regime 含前视，只作为研究对照，报告里显式警示。
- 不写 trials_ledger；regime 拟合不是因子试验。
- 默认只跑 in-sample，用户覆盖 --end 时也不得超过 validation_end=2025-09-30。

跑法（仓库根目录）：
    ~/miniconda3/envs/xsec-crypto-quant/bin/python scripts/run_regime_analysis.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.walkforward import SplitConfig  # noqa: E402
from data.storage import load_ohlcv  # noqa: E402
from regime.correlation_spike import detect_correlation_spike  # noqa: E402
from regime.market_regime import (  # noqa: E402
    DEFAULT_CORR_WINDOW,
    DEFAULT_N_STATES,
    DEFAULT_VOL_WINDOW,
    REGIME_CRISIS,
    compute_market_features,
    equal_weight_pool_return,
    label_regimes_expanding,
    label_regimes_full,
)
from regime.risk_params import effective_gross_multiplier  # noqa: E402

logging.getLogger("hmmlearn").setLevel(logging.ERROR)

DATA_DIR = Path("data/historical")
DEFAULT_TIMELINE_PATH = Path("reports/regime_timeline_is.png")
DEFAULT_SUMMARY_PATH = Path("reports/regime_summary_is.md")
HMM_N_STATES = 3
EXPANDING_MIN_TRAIN = 252
EXPANDING_REFIT_EVERY = 21
WARMUP_DAYS = EXPANDING_MIN_TRAIN + max(DEFAULT_VOL_WINDOW, DEFAULT_CORR_WINDOW) + 10

STATE_LABELS = {
    0: "Low-vol",
    1: "Trend",
    2: "Crisis",
}
STATE_COLORS = {
    0: "#9ecae1",
    1: "#a1d99b",
    2: "#fb6a4a",
}
FEATURE_COLUMNS = ["market_vol", "xs_corr_median", "mean_abs_return"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--start", default=SplitConfig.in_sample_start)
    parser.add_argument("--end", default=SplitConfig.in_sample_end)
    parser.add_argument("--timeline", default=str(DEFAULT_TIMELINE_PATH))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY_PATH))
    return parser.parse_args(argv)


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def validate_requested_window(start: pd.Timestamp, end: pd.Timestamp) -> None:
    validation_end = _utc(SplitConfig.validation_end)
    if start > end:
        raise ValueError(f"start must be <= end, got {start.date()} > {end.date()}")
    if end > validation_end:
        raise ValueError(
            f"--end {end.date()} would touch locked holdout boundary; "
            f"latest allowed date is validation_end {validation_end.date()}"
        )


def load_universe_history(data_dir: Path) -> dict[pd.Timestamp, list[str]]:
    payload = json.loads((data_dir / "universe_history.json").read_text(encoding="utf-8"))
    return {_utc(month): list(members) for month, members in payload.items()}


def select_universe_window(
    universe_history: dict[pd.Timestamp, list[str]],
    *,
    load_start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[pd.Timestamp, list[str]]:
    """Keep the latest universe before load_start plus every month through end."""
    eligible = sorted(month for month in universe_history if month <= end)
    prior = [month for month in eligible if month <= load_start]
    first = prior[-1] if prior else load_start
    return {month: universe_history[month] for month in eligible if month >= first}


def pool_asset_ids(universe_history: dict[pd.Timestamp, list[str]]) -> list[str]:
    return sorted({asset for members in universe_history.values() for asset in members})


def load_close_panel(
    asset_ids: list[str],
    data_dir: Path,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Read local parquet OHLCV shards into a close wide panel with columns=asset_id."""
    cols: dict[str, pd.Series] = {}
    for asset_id in asset_ids:
        df = load_ohlcv(asset_id, start=start, end=end, data_dir=data_dir)
        if df.empty:
            continue
        series = pd.Series(df["close"].to_numpy(), index=df.index.normalize(), name=asset_id)
        cols[asset_id] = series[~series.index.duplicated(keep="last")].sort_index()
    return pd.DataFrame(cols).sort_index()


def daily_pool_mask(
    universe_history: dict[pd.Timestamp, list[str]],
    index: pd.DatetimeIndex,
    columns: pd.Index,
) -> pd.DataFrame:
    """Forward-fill monthly dynamic-universe membership to a daily boolean mask."""
    months = sorted(universe_history)
    mask = pd.DataFrame(False, index=index, columns=columns)
    for i, month in enumerate(months):
        next_month = months[i + 1] if i + 1 < len(months) else index.max() + pd.Timedelta(days=1)
        rows = (index >= month) & (index < next_month)
        members = [asset for asset in universe_history[month] if asset in columns]
        if rows.any() and members:
            mask.loc[rows, members] = True
    return mask


def apply_mask(panel: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    aligned = panel.reindex(index=mask.index, columns=mask.columns)
    return aligned.where(mask)


def gross_multiplier_timeline(regime: pd.Series, corr_spike: pd.Series) -> pd.Series:
    """Combine expanding HMM labels and corr-spike booleans into daily gross multipliers."""
    index = pd.DatetimeIndex(corr_spike.index)
    regimes = regime.reindex(index)
    spikes = corr_spike.reindex(index).fillna(False).astype(bool)
    values = [
        effective_gross_multiplier(regimes.loc[ts], bool(spikes.loc[ts]))
        for ts in index
    ]
    return pd.Series(values, index=index, name="gross_multiplier")


def state_proportions(regime: pd.Series) -> dict[int, float]:
    valid = regime.dropna().astype(int)
    if valid.empty:
        return {state: float("nan") for state in STATE_LABELS}
    total = len(valid)
    return {state: float((valid == state).sum() / total) for state in STATE_LABELS}


def state_switch_stats(regime: pd.Series) -> tuple[int, float]:
    valid = regime.dropna().astype(int)
    if len(valid) <= 1:
        return 0, float("nan")
    switches = int((valid != valid.shift()).sum() - 1)
    days = max((valid.index.max() - valid.index.min()).days + 1, 1)
    return switches, switches / (days / 365.25)


def state_intervals(regime: pd.Series, target_state: int) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    valid = regime.dropna().astype(int).sort_index()
    intervals: list[tuple[pd.Timestamp, pd.Timestamp, int]] = []
    start: pd.Timestamp | None = None
    prev_ts: pd.Timestamp | None = None

    for ts, value in valid.items():
        gap = prev_ts is not None and ts - prev_ts > pd.Timedelta(days=1)
        is_target = int(value) == target_state
        if start is not None and (not is_target or gap):
            if prev_ts is not None:
                intervals.append((start, prev_ts, (prev_ts - start).days + 1))
            start = None
        if is_target and start is None:
            start = ts
        prev_ts = ts

    if start is not None and prev_ts is not None:
        intervals.append((start, prev_ts, (prev_ts - start).days + 1))
    return intervals


def feature_means_by_state(features: pd.DataFrame, regime: pd.Series) -> pd.DataFrame:
    aligned = features.reindex(regime.index)
    labels = regime.reindex(aligned.index).dropna().astype(int)
    grouped = aligned.loc[labels.index, FEATURE_COLUMNS].groupby(labels).mean()
    return grouped.reindex(list(STATE_LABELS))


def _pct(value: float) -> str:
    return "NA" if pd.isna(value) else f"{value:.2%}"


def _number(value: float) -> str:
    return "NA" if pd.isna(value) else f"{value:.6f}"


def render_summary(
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    close: pd.DataFrame,
    features: pd.DataFrame,
    regime_expanding: pd.Series,
    regime_full: pd.Series,
    spike: pd.DataFrame,
    multiplier: pd.Series,
) -> str:
    exp_props = state_proportions(regime_expanding)
    full_props = state_proportions(regime_full)
    exp_switches, exp_switch_freq = state_switch_stats(regime_expanding)
    full_switches, full_switch_freq = state_switch_stats(regime_full)
    feature_means = feature_means_by_state(features, regime_expanding)
    crisis_intervals = state_intervals(regime_expanding, REGIME_CRISIS)
    spike_days = int(spike["corr_spike"].fillna(False).astype(bool).sum())
    deleveraged_days = int((multiplier < 1.0).sum())
    total_days = len(multiplier.dropna())
    pool_per_day = close.notna().sum(axis=1)

    lines = [
        "# Phase 3 Regime Analysis Summary (real data, in-sample)",
        "",
        "> WARNING: `label_regimes_full` is a full-sample HMM fit and contains look-ahead. "
        "It is included only for research visualization. Downstream/backtest use must rely "
        "on `label_regimes_expanding`.",
        "",
        "## Configuration",
        "",
        f"- Window: {start.date()} to {end.date()} UTC",
        f"- HMM n_states: {HMM_N_STATES} (hard-coded)",
        "- Correlation spike: default SpikeConfig(abs_threshold=0.7, "
        "baseline_window=60, z_threshold=2.0, min_baseline=20)",
        "- Trials ledger: not written; regime fitting is not a factor trial.",
        f"- Daily in-pool asset count: min {int(pool_per_day.min())}, "
        f"median {int(pool_per_day.median())}, max {int(pool_per_day.max())}",
        "",
        "## State Proportions",
        "",
        "| State | Expanding honest | Full research/look-ahead |",
        "|---|---:|---:|",
    ]
    for state, label in STATE_LABELS.items():
        lines.append(f"| {state} {label} | {_pct(exp_props[state])} | {_pct(full_props[state])} |")

    lines.extend(
        [
            "",
            "## State Switching",
            "",
            "| Label source | Switches | Annualized switches |",
            "|---|---:|---:|",
            f"| Expanding honest | {exp_switches} | {_number(exp_switch_freq)} |",
            f"| Full research/look-ahead | {full_switches} | {_number(full_switch_freq)} |",
            "",
            "## Feature Means By Expanding State",
            "",
            "| State | market_vol | xs_corr_median | mean_abs_return |",
            "|---|---:|---:|---:|",
        ]
    )
    for state, label in STATE_LABELS.items():
        row = feature_means.loc[state]
        lines.append(
            f"| {state} {label} | {_number(row['market_vol'])} | "
            f"{_number(row['xs_corr_median'])} | {_number(row['mean_abs_return'])} |"
        )

    lines.extend(
        [
            "",
            "## Correlation Spike And Deleveraging",
            "",
            f"- Corr-spike days: {spike_days} / {len(spike)} ({_pct(spike_days / len(spike))})",
            f"- Deleveraged days (gross multiplier < 1): {deleveraged_days} / {total_days} "
            f"({_pct(deleveraged_days / total_days if total_days else float('nan'))})",
            "",
            "## Expanding Crisis Intervals",
            "",
        ]
    )
    if crisis_intervals:
        lines.extend(["| Start | End | Days |", "|---|---|---:|"])
        for interval_start, interval_end, days in crisis_intervals:
            lines.append(f"| {interval_start.date()} | {interval_end.date()} | {days} |")
    else:
        lines.append("No expanding crisis intervals in this window.")

    return "\n".join(lines) + "\n"


def save_summary(path: Path, summary: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary, encoding="utf-8")
    return path


def _shade_regime_background(ax, regime: pd.Series) -> None:
    used_labels: set[int] = set()
    for state, label in STATE_LABELS.items():
        for start, end, _days in state_intervals(regime, state):
            ax.axvspan(
                start,
                end + pd.Timedelta(days=1),
                color=STATE_COLORS[state],
                alpha=0.16 if state != REGIME_CRISIS else 0.22,
                lw=0,
                label=label if state not in used_labels else None,
            )
            used_labels.add(state)


def save_timeline_figure(
    path: Path,
    *,
    close: pd.DataFrame,
    features: pd.DataFrame,
    regime_expanding: pd.Series,
    spike: pd.DataFrame,
    multiplier: pd.Series,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pool_return = equal_weight_pool_return(close).fillna(0.0)
    nav = (1.0 + pool_return).cumprod()
    spike_signal = spike["corr_spike"].fillna(False).astype(bool)
    spike_dates = spike_signal.index[spike_signal]

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    _shade_regime_background(axes[0], regime_expanding)
    axes[0].plot(nav.index, nav, color="#111827", lw=1.4, label="Equal-weight pool NAV")
    axes[0].set_ylabel("NAV")
    axes[0].set_title("Equal-weight pool NAV with expanding HMM regime background")
    axes[0].legend(loc="upper left", ncols=4, fontsize=8)

    axes[1].plot(
        features.index,
        features["market_vol"],
        color="#1f78b4",
        lw=1.1,
        label="Market vol",
    )
    axes[1].plot(
        features.index,
        features["xs_corr_median"],
        color="#e31a1c",
        lw=1.1,
        label="XS corr median",
    )
    if len(spike_dates):
        axes[1].scatter(
            spike_dates,
            features.loc[spike_dates, "xs_corr_median"],
            marker="|",
            s=80,
            color="#7f1d1d",
            label="Corr spike",
        )
    axes[1].set_ylabel("Feature value")
    axes[1].set_title("Market volatility, cross-sectional correlation, and corr-spike days")
    axes[1].legend(loc="upper left", fontsize=8)

    axes[2].step(
        multiplier.index,
        multiplier,
        where="post",
        color="#111827",
        lw=1.3,
        label="Gross multiplier",
    )
    axes[2].set_ylim(-0.05, 1.05)
    axes[2].set_yticks([0.2, 1.0])
    axes[2].set_ylabel("Multiplier")
    axes[2].set_title("Effective gross leverage multiplier (expanding regime + corr spike)")
    axes[2].legend(loc="upper left", fontsize=8)

    fig.suptitle(
        "Phase 3 real-data regime analysis: expanding labels only for downstream use; "
        "full-sample HMM is look-ahead research only",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    if HMM_N_STATES != DEFAULT_N_STATES:
        raise RuntimeError("HMM_N_STATES must stay fixed at DEFAULT_N_STATES=3")

    args = parse_args(argv)
    data_dir = Path(args.data_dir)
    start, end = _utc(args.start), _utc(args.end)
    try:
        validate_requested_window(start, end)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    load_start = start - pd.Timedelta(days=WARMUP_DAYS)
    print(f"阶段3 regime 分析｜window {start.date()} ~ {end.date()}｜load_start {load_start.date()}")
    print("护栏：n_states=3；SpikeConfig 默认；full HMM 仅研究对照；不触碰 holdout。")

    universe_history = load_universe_history(data_dir)
    universe_window = select_universe_window(universe_history, load_start=load_start, end=end)
    if not universe_window:
        print("ERROR: no universe history available for requested window", file=sys.stderr)
        return 1

    asset_ids = pool_asset_ids(universe_window)
    print(f"动态池历史：{len(universe_window)} 个月，去重 {len(asset_ids)} 个 asset_id")

    close = load_close_panel(asset_ids, data_dir, start=load_start, end=end)
    if close.empty:
        print("ERROR: close panel is empty", file=sys.stderr)
        return 1

    mask = daily_pool_mask(universe_window, close.index, close.columns)
    close_masked = apply_mask(close, mask)
    analysis_index = close_masked.loc[(close_masked.index >= start) & (close_masked.index <= end)].index
    if analysis_index.empty:
        print("ERROR: requested analysis window has no close data", file=sys.stderr)
        return 1

    pool_per_day = mask.reindex(analysis_index).sum(axis=1)
    print(
        "在池标的数/日："
        f"min {int(pool_per_day.min())}｜median {int(pool_per_day.median())}｜"
        f"max {int(pool_per_day.max())}"
    )

    features = compute_market_features(close_masked)
    print(f"市场特征：{features.dropna().shape[0]} 个有效日，列 {list(features.columns)}")

    regime_expanding = label_regimes_expanding(
        features,
        n_states=HMM_N_STATES,
        min_train=EXPANDING_MIN_TRAIN,
        refit_every=EXPANDING_REFIT_EVERY,
    )
    regime_full = label_regimes_full(features, n_states=HMM_N_STATES)
    spike = detect_correlation_spike(close_masked)
    multiplier = gross_multiplier_timeline(regime_expanding, spike["corr_spike"])

    close_is = close_masked.reindex(analysis_index)
    features_is = features.reindex(analysis_index)
    regime_expanding_is = regime_expanding.reindex(analysis_index)
    regime_full_is = regime_full.reindex(analysis_index)
    spike_is = spike.reindex(analysis_index)
    multiplier_is = multiplier.reindex(analysis_index)

    timeline_path = save_timeline_figure(
        Path(args.timeline),
        close=close_is,
        features=features_is,
        regime_expanding=regime_expanding_is,
        spike=spike_is,
        multiplier=multiplier_is,
    )
    summary = render_summary(
        start=start,
        end=end,
        close=close_is,
        features=features_is,
        regime_expanding=regime_expanding_is,
        regime_full=regime_full_is,
        spike=spike_is,
        multiplier=multiplier_is,
    )
    summary_path = save_summary(Path(args.summary), summary)

    exp_props = state_proportions(regime_expanding_is)
    full_props = state_proportions(regime_full_is)
    exp_switches, exp_switch_freq = state_switch_stats(regime_expanding_is)
    crisis_intervals = state_intervals(regime_expanding_is, REGIME_CRISIS)
    deleveraged_ratio = float((multiplier_is < 1.0).mean())
    spike_days = int(spike_is["corr_spike"].fillna(False).astype(bool).sum())

    print("\n=== 关键数字（expanding=诚实默认，full=含前视研究对照） ===")
    for state, label in STATE_LABELS.items():
        print(f"{state} {label}: expanding {_pct(exp_props[state])}｜full {_pct(full_props[state])}")
    print(f"expanding 状态切换：{exp_switches} 次｜年化 {exp_switch_freq:.2f} 次/年")
    print(f"corr_spike 触发：{spike_days} 天")
    print(f"降杠杆天数占比（multiplier<1）：{deleveraged_ratio:.2%}")
    if crisis_intervals:
        formatted = ", ".join(f"{s.date()}..{e.date()}({days}d)" for s, e, days in crisis_intervals)
        print(f"expanding 危机区间：{formatted}")
    else:
        print("expanding 危机区间：无")
    print(f"\n图：{timeline_path}｜摘要：{summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
