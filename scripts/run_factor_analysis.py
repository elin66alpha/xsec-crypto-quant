"""阶段 2 真实数据因子分析驱动（in-sample 2020-01 ~ 2024-06）。

把阶段 1（features）与阶段 2（factors）模块接到全量真实数据上，**只在 in-sample 段**
跑一次诚实的横截面因子筛选：IC（Newey-West HAC）→ BH-FDR 校正 → 相关剔除 → 分层回测，
并把本批试验组合数记入 docs/trials_ledger.md 喂阶段 5 的 Deflated Sharpe。

铁律落地：
- **动态池掩码**：每个时点只在"当月真实在池"的 asset_id 间评估（universe_history.json
  月度成分前向填充到日频），杜绝幸存者偏差与用未来池成分回测过去（决策 1 / 风控铁律 4）。
- **只碰 in-sample**：默认 2020-01-01 ~ 2024-06-30（backtest.walkforward.SplitConfig），
  validation/holdout 不触碰（决策 / 阶段 5 三段切分）。
- **无前视**：因子只用 t 及以前信息，forward_return 对齐在 t（ic_analysis 已保证）。
- **不选最优 N**：动量等窗口作为并列候选因子一起测（禁止全量选参），最终 N 由阶段 5
  Walk-Forward 选；窗口越多 FDR 多重检验压力越大，是诚实代价。

跑法（仓库根目录）：
    ~/miniconda3/envs/xsec-crypto-quant/bin/python scripts/run_factor_analysis.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.metrics import bootstrap_ci  # noqa: E402
from backtest.walkforward import SplitConfig  # noqa: E402
from data.storage import load_funding, load_ohlcv  # noqa: E402
from factors.ic_analysis import cross_sectional_ic, forward_return, ic_summary  # noqa: E402
from factors.quantile_backtest import (  # noqa: E402
    daily_long_short_spread,
    quantile_returns,
)
from factors.report import save_html  # noqa: E402
from factors.selection import analyze_factors  # noqa: E402
from features.carry import funding_carry, to_daily_funding  # noqa: E402
from features.cross_sectional import (  # noqa: E402
    momentum,
    realized_volatility,
    volume_change,
)

# 因子窗口网格（2026-06-13 用户确认；并列候选，最终 N 留待阶段 5 Walk-Forward）
MOMENTUM_WINDOWS = (7, 14, 30, 60, 90)
VOL_WINDOWS = (14, 30)
VOLUME_WINDOWS = (14, 30)
CARRY_WINDOWS = (7, 14, 30)
HORIZONS = (1, 3, 5, 10)  # CLAUDE.md 阶段 2 钉死
FDR_Q = 0.10  # 2026-06-13 用户确认
DATA_DIR = Path("data/historical")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default=str(DATA_DIR))
    p.add_argument("--start", default=SplitConfig.in_sample_start, help="in-sample 起")
    p.add_argument("--end", default=SplitConfig.in_sample_end, help="in-sample 止")
    p.add_argument("--q", type=float, default=FDR_Q, help="BH-FDR 阈值")
    p.add_argument("--report", default="reports/factor_analysis_is.html")
    return p.parse_args(argv)


def _utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def load_universe_history(data_dir: Path) -> dict[pd.Timestamp, list[str]]:
    import json

    payload = json.loads((data_dir / "universe_history.json").read_text(encoding="utf-8"))
    return {_utc(m): list(v) for m, v in payload.items()}


def pool_asset_ids(universe_history: dict[pd.Timestamp, list[str]]) -> list[str]:
    return sorted({a for members in universe_history.values() for a in members})


def load_price_volume_panels(
    asset_ids: list[str], data_dir: Path, *, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """从本地 parquet 读出 close / volume 宽面板（列 = asset_id，日频 UTC）。

    起点比 ``start`` 早留出最长动量窗口的余量，保证 in-sample 首日就有动量值。
    """
    close_cols: dict[str, pd.Series] = {}
    volume_cols: dict[str, pd.Series] = {}
    for aid in asset_ids:
        df = load_ohlcv(aid, start=start, end=end, data_dir=data_dir)
        if df.empty:
            continue
        idx = df.index.normalize()
        close_cols[aid] = pd.Series(df["close"].values, index=idx)
        volume_cols[aid] = pd.Series(df["volume"].values, index=idx)
    close = pd.DataFrame(close_cols).sort_index()
    volume = pd.DataFrame(volume_cols).sort_index()
    return close, volume


def load_daily_funding_panel(
    asset_ids: list[str], data_dir: Path, *, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """读出 8h funding 宽面板并聚合成日度（缺失不补 0，沿用 min_count=1 语义）。"""
    cols: dict[str, pd.Series] = {}
    for aid in asset_ids:
        f = load_funding(aid, start=start, end=end, data_dir=data_dir)
        if f.empty:
            continue
        cols[aid] = f["fundingRate"]
    if not cols:
        return pd.DataFrame()
    wide = pd.DataFrame(cols).sort_index()
    return to_daily_funding(wide, how="sum")


def daily_pool_mask(
    universe_history: dict[pd.Timestamp, list[str]],
    index: pd.DatetimeIndex,
    columns: pd.Index,
) -> pd.DataFrame:
    """把月度池成分前向填充到日频，得到 (date × asset) 的布尔掩码。

    某日的在池集合 = 不晚于该日的最近一个月初的成分。无前视：当月池在月初（收盘后选定）
    起生效，月内每日沿用。
    """
    months = sorted(universe_history)
    mask = pd.DataFrame(False, index=index, columns=columns)
    for i, m in enumerate(months):
        m_start = m
        m_end = months[i + 1] if i + 1 < len(months) else index.max() + pd.Timedelta(days=1)
        rows = (index >= m_start) & (index < m_end)
        members = [a for a in universe_history[m] if a in columns]
        if rows.any() and members:
            mask.loc[rows, members] = True
    return mask


def apply_mask(panel: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """把因子面板对齐到掩码并将非在池格子置 NaN（截面评估只含当时在池资产）。"""
    aligned = panel.reindex(index=mask.index, columns=mask.columns)
    return aligned.where(mask)


def build_masked_panels(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    funding_daily: pd.DataFrame,
    mask: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """构造全部候选因子面板并逐一做动态池掩码。"""
    panels: dict[str, pd.DataFrame] = {}
    for w in MOMENTUM_WINDOWS:
        panels[f"momentum_{w}"] = momentum(close, w)
    for w in VOL_WINDOWS:
        panels[f"realized_vol_{w}"] = realized_volatility(close, w)
    for w in VOLUME_WINDOWS:
        panels[f"volume_chg_{w}"] = volume_change(volume, w)
    if not funding_daily.empty:
        for w in CARRY_WINDOWS:
            panels[f"carry_{w}"] = funding_carry(funding_daily, w)
    else:
        logger.warning("funding 面板为空，跳过 carry 因子")
    return {name: apply_mask(p, mask) for name, p in panels.items()}


def append_trials_ledger(n_combos: int, *, note: str) -> None:
    """把本批试验组合数追加进 docs/trials_ledger.md（喂阶段 5 DSR 的 n_trials）。"""
    path = Path("docs/trials_ledger.md")
    line = f"| {date.today().isoformat()} | phase2-real-is | {n_combos} | {note} |\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else (
        "| date | batch | n_combos | note |\n|---|---|---:|---|\n"
    )
    if f"phase2-real-is | {n_combos} | {note}" in existing:
        logger.info("trials_ledger 已含本批（{}），跳过追加（幂等）", note)
        return
    path.write_text(existing + line, encoding="utf-8")
    logger.info("trials_ledger += {} 组合（{}）", n_combos, note)


def co_primary_screen(
    panels: dict[str, pd.DataFrame],
    close_is: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    q: float,
    fdr_reject: set[str],
    n_quantiles: int = 5,
    n_bootstrap: int = 1000,
    block_length: int = 20,
) -> pd.DataFrame:
    """对每个基础因子（取 |ICIR| 最大 horizon）并列检验 IC 与等权多空价差。

    决策 A（2026-06-13 用户确认）：策略是分层等权多空，其损益 = 桶均值之差，故以
    **价差 bootstrap 显著性**为 P&L 主检验；逐日 Spearman **IC** 为稳健佐证。两者方向
    一致且都显著 = robust；价差显著但 IC 反号 = 尾部驱动(divergent)；据此分类。
    """
    fwd = {h: forward_return(close_is, h) for h in horizons}
    rows: list[dict] = []
    for base, panel in panels.items():
        # 该因子 |ICIR| 最大的 horizon 作代表（与 selection.py 一致）
        best_h, best_absicir, best_ic = horizons[0], -1.0, {}
        for h in horizons:
            s = ic_summary(cross_sectional_ic(panel, fwd[h]), hac_lags=h - 1)
            if abs(s.get("icir", 0.0)) > best_absicir:
                best_h, best_absicir, best_ic = h, abs(s["icir"]), s
        spread = daily_long_short_spread(panel, fwd[best_h], n_quantiles).dropna()
        lo, hi = bootstrap_ci(spread, lambda s: float(s.mean()), n_bootstrap=n_bootstrap,
                              ci=0.95, block_length=block_length)
        sp_mean = float(spread.mean())
        sp_sig = (lo > 0) or (hi < 0)            # CI 不含 0
        ic_mean = best_ic["mean_ic"]
        ic_sig = f"{base}@{best_h}d" in fdr_reject
        same_sign = (ic_mean > 0) == (sp_mean > 0)
        if sp_sig and ic_sig and same_sign:
            cls = "robust"
        elif sp_sig and ic_sig and not same_sign:
            cls = "divergent(tail)"
        elif sp_sig and not ic_sig:
            cls = "spread-only"
        elif ic_sig and not sp_sig:
            cls = "ic-only"
        else:
            cls = "weak"
        rows.append({
            "factor": base, "best_h": best_h,
            "mean_ic": ic_mean, "icir": best_ic["icir"], "ic_t": best_ic["t_stat"],
            "ic_sig": ic_sig,
            "ls_spread": sp_mean, "ls_ci_lo": lo, "ls_ci_hi": hi, "ls_sig": sp_sig,
            "direction": "long-high" if sp_mean > 0 else "long-low",
            "class": cls,
        })
    df = pd.DataFrame(rows)
    order = {"robust": 0, "divergent(tail)": 1, "spread-only": 2, "ic-only": 3, "weak": 4}
    return df.sort_values(["class", "ls_spread"], key=lambda c: c.map(order) if c.name == "class" else c.abs(),
                          ascending=[True, False]).reset_index(drop=True)


def save_screen_table(screen: pd.DataFrame, path: str) -> None:
    cols = ["factor", "best_h", "class", "direction", "mean_ic", "ic_t", "ic_sig",
            "ls_spread", "ls_ci_lo", "ls_ci_hi", "ls_sig"]
    md = screen[cols].copy()
    for c in ["mean_ic", "ls_spread", "ls_ci_lo", "ls_ci_hi"]:
        md[c] = md[c].map(lambda x: f"{x:+.5f}")
    md["ic_t"] = md["ic_t"].map(lambda x: f"{x:+.2f}")
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in md.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 阶段2 真实 in-sample 因子筛选（决策A：IC × 等权多空价差 并列主筛）\n\n"
        "价差(ls_spread)=每日顶层均值−底层均值的时间均值；ls_ci 为 circular block bootstrap "
        "(block=20) 95% 区间，不含 0 即显著(ls_sig)。direction=按价差符号定多空腿方向。\n"
        "class：robust=IC与价差同号且都显著；divergent(tail)=价差显著但IC反号(尾部驱动、脆弱)；"
        "weak=价差不显著。\n\n"
    )
    Path(path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def save_figure(
    screen: pd.DataFrame,
    panels: dict[str, pd.DataFrame],
    close_is: pd.DataFrame,
    *,
    path: str,
    n_quantiles: int = 5,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    color = {"robust": "#2e7d32", "divergent(tail)": "#ef6c00",
             "spread-only": "#1565c0", "ic-only": "#6a1b9a", "weak": "#9e9e9e"}
    # 用最强的几个 robust/divergent，否则退而展示价差绝对值最大的几个
    show = screen[screen["class"].isin(["robust", "divergent(tail)"])]
    if show.empty:
        show = screen.reindex(screen["ls_spread"].abs().sort_values(ascending=False).index).head(6)
    fig, axes = plt.subplots(1, 3, figsize=(18, 7))

    # Panel 1: mean IC by factor
    s = screen.iloc[::-1]
    axes[0].barh(s["factor"], s["mean_ic"], color=[color[c] for c in s["class"]])
    axes[0].axvline(0, color="k", lw=0.8)
    axes[0].set_title("(1) Cross-sectional IC (Spearman, best horizon)\nnegative = reversal / low-value outperforms")
    axes[0].set_xlabel("mean IC")

    # Panel 2: long-short spread with bootstrap CI
    err = [(s["ls_spread"] - s["ls_ci_lo"]).values, (s["ls_ci_hi"] - s["ls_spread"]).values]
    axes[1].barh(s["factor"], s["ls_spread"], color=[color[c] for c in s["class"]])
    axes[1].errorbar(s["ls_spread"], s["factor"], xerr=err, fmt="none", ecolor="k", capsize=3, lw=1)
    axes[1].axvline(0, color="k", lw=0.8)
    axes[1].set_title("(2) Equal-weight L/S daily spread + 95% block-bootstrap CI\nCI excludes 0 = tradable edge (DECISION metric)")
    axes[1].set_xlabel("daily top-bottom spread")

    # Panel 3: Q1..Q5 quantile mean returns of the strongest factors
    fwd_cache: dict[int, pd.DataFrame] = {}
    width = 0.8 / max(1, len(show))
    for i, (_, row) in enumerate(show.iterrows()):
        h = int(row["best_h"])
        fwd_cache.setdefault(h, forward_return(close_is, h))
        means = quantile_returns(panels[row["factor"]], fwd_cache[h], n_quantiles)
        x = np.arange(1, n_quantiles + 1) + (i - len(show) / 2) * width
        axes[2].bar(x, means.values, width=width,
                    label=f"{row['factor']}@{h}d [{row['class'][:4]}]",
                    color=color[row["class"]], alpha=0.55 + 0.45 * (row["class"] == "robust"))
    axes[2].axhline(0, color="k", lw=0.8)
    axes[2].set_title("(3) Mean forward return by quantile Q1->Q5\nmonotone = clean; only-Q5-up = tail-driven")
    axes[2].set_xlabel("quantile (1=low score ... 5=high score)")
    axes[2].legend(fontsize=7)

    fig.suptitle("Phase 2 real in-sample factor screen (2020-01~2024-06, dynamic-pool masked)", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data_dir)
    is_start, is_end = _utc(args.start), _utc(args.end)
    # 多留最长动量窗口 + 余量，保证 in-sample 首日有因子值
    load_start = is_start - pd.Timedelta(days=max(MOMENTUM_WINDOWS) + 10)

    print(f"阶段2 真实数据因子分析｜in-sample {args.start} ~ {args.end}｜q={args.q}")
    uh = load_universe_history(data_dir)
    # 只用 in-sample 段的池历史做掩码（validation/holdout 月份不参与）
    uh_is = {m: v for m, v in uh.items() if is_start <= m <= is_end}
    aids = pool_asset_ids(uh_is)
    print(f"in-sample 池历史：{len(uh_is)} 个月，去重 {len(aids)} 个 asset_id")

    close, volume = load_price_volume_panels(aids, data_dir, start=load_start, end=is_end)
    funding_daily = load_daily_funding_panel(aids, data_dir, start=load_start, end=is_end)
    print(f"close 面板 {close.shape}｜volume {volume.shape}｜funding 日度 {funding_daily.shape}")

    # in-sample 评估区间的日频网格 + 池掩码
    is_index = close.loc[(close.index >= is_start) & (close.index <= is_end)].index
    mask = daily_pool_mask(uh_is, is_index, close.columns)
    pool_per_day = mask.sum(axis=1)
    print(f"在池标的数/日：中位 {int(pool_per_day.median())}，min {int(pool_per_day.min())}，"
          f"max {int(pool_per_day.max())}")

    panels = build_masked_panels(close, volume, funding_daily, mask)
    close_is = close.reindex(index=is_index)
    n_combos = len(panels) * len(HORIZONS)
    print(f"候选因子 {len(panels)} 个 × horizon {HORIZONS} = {n_combos} 个 IC 组合")

    analysis = analyze_factors(panels, close_is, horizons=HORIZONS, q=args.q)

    print("\n=== FDR 校正前后通过数 ===")
    print(f"裸 p<0.05 通过：{analysis.fdr_counts['n_naive']}｜"
          f"FDR(q={args.q}) 通过：{analysis.fdr_counts['n_fdr']}｜"
          f"共测 {analysis.fdr_counts['n_total']}")
    print(f"经 FDR 后存活基础因子：{analysis.surviving_bases or '无'}")

    # 决策 A：IC × 等权多空价差 并列主筛
    fdr_reject = set(analysis.fdr_table.index[analysis.fdr_table["reject"]])
    print("\n=== 并列主筛：IC × 等权多空价差(block bootstrap 95%CI) ===")
    screen = co_primary_screen(panels, close_is, horizons=HORIZONS, q=args.q, fdr_reject=fdr_reject)
    show_cols = ["factor", "best_h", "class", "direction", "mean_ic", "ic_t", "ls_spread",
                 "ls_ci_lo", "ls_ci_hi"]
    with pd.option_context("display.float_format", lambda x: f"{x:+.5f}"):
        print(screen[show_cols].to_string(index=False))
    robust = screen.loc[screen["class"] == "robust", "factor"].tolist()
    divergent = screen.loc[screen["class"] == "divergent(tail)", "factor"].tolist()
    print(f"\n稳健候选(robust)：{robust or '无'}")
    print(f"尾部驱动/脆弱(divergent)：{divergent or '无'}")

    append_trials_ledger(n_combos, note=f"real in-sample {args.start}..{args.end}, q={args.q}")
    save_screen_table(screen, "reports/factor_screen_is.md")
    save_figure(screen, panels, close_is, path="reports/factor_screen_is.png")
    out = save_html(analysis, title="横截面因子分析报告（阶段 2 · 真实 in-sample）", path=args.report)
    print(f"\n表：reports/factor_screen_is.md｜图：reports/factor_screen_is.png｜HTML：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
