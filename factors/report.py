"""因子分析 HTML 报告（阶段 2 验收产物）。

输入 ``selection.FactorAnalysis``,输出一个**自包含、无外部依赖**的 HTML 报告,含：
    - 元信息：q 值、horizon、相关阈值、FDR 校正前/后通过数对比
    - 最终低相关精选池
    - IC / ICIR / t / 原始 p / FDR q 值 / 是否显著 的完整表（按 p 升序,显著行高亮）
    - 各入选因子的分层回测条形图（Q1→Q5 单调性,纯 CSS 画,无需 plotly/JS）
    - 诚实声明：FDR 控制比例非零保证；最终 edge 真伪仍待阶段 5

报告写入 ``reports/``（被 .gitignore 忽略,不入库）。渲染为纯字符串,可离线单测。
"""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from .selection import FactorAnalysis


def _fmt(x: float, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:.{nd}f}"


def _fdr_table_html(analysis: FactorAnalysis) -> str:
    df = analysis.fdr_table
    cols = ["mean_ic", "icir", "t_stat", "n_obs", "pvalue", "qvalue", "reject"]
    have = [c for c in cols if c in df.columns]
    head = "".join(f"<th>{c}</th>" for c in ["factor", *have])
    rows = []
    for name, row in df.iterrows():
        cls = ' class="hit"' if bool(row.get("reject", False)) else ""
        cells = [f"<td>{html.escape(str(name))}</td>"]
        for c in have:
            v = row[c]
            if c == "reject":
                cells.append(f"<td>{'✔' if bool(v) else ''}</td>")
            elif c == "n_obs":
                cells.append(f"<td>{int(v) if np.isfinite(v) else '—'}</td>")
            else:
                cells.append(f"<td>{_fmt(float(v), 4)}</td>")
        rows.append(f"<tr{cls}>{''.join(cells)}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _quantile_chart_html(name: str, means: pd.Series, stats: dict[str, float]) -> str:
    vals = means.dropna()
    if vals.empty:
        return f"<h3>{html.escape(name)}</h3><p>无分层数据</p>"
    scale = float(max(abs(vals.min()), abs(vals.max()))) or 1.0
    bars = []
    for q, v in vals.items():
        width = abs(v) / scale * 100.0
        color = "#2e7d32" if v >= 0 else "#c62828"
        bars.append(
            f'<div class="qrow"><span class="qlbl">Q{int(q)}</span>'
            f'<span class="qbar" style="width:{width:.1f}%;background:{color}"></span>'
            f'<span class="qval">{_fmt(v, 5)}</span></div>'
        )
    mono = _fmt(stats.get("monotonicity", float("nan")), 3)
    spread = _fmt(stats.get("spread", float("nan")), 5)
    h = int(stats.get("horizon", 0))
    return (
        f"<h3>{html.escape(name)} <small>(horizon {h}d)</small></h3>"
        f'<div class="qchart">{"".join(bars)}</div>'
        f"<p class=\"meta\">单调性 monotonicity = <b>{mono}</b>（+1=完美单调递增）　"
        f"顶-底价差 spread = <b>{spread}</b></p>"
    )


def render_html(analysis: FactorAnalysis, title: str = "横截面因子分析报告（阶段 2）") -> str:
    """把 ``FactorAnalysis`` 渲染成自包含 HTML 字符串。"""
    c = analysis.fdr_counts
    meta = (
        f"<ul class=\"meta\">"
        f"<li>FDR 目标 q = <b>{analysis.q}</b>　相关阈值 = <b>{analysis.corr_threshold}</b>　"
        f"horizon = {list(analysis.horizons)}</li>"
        f"<li>共测 <b>{c.get('n_total', 0)}</b> 个 因子×horizon 组合；"
        f"裸 p&lt;0.05 通过 <b>{c.get('n_naive', 0)}</b> 个；"
        f"FDR 校正后通过 <b>{c.get('n_fdr', 0)}</b> 个</li>"
        f"</ul>"
    )
    selected = analysis.selected
    sel_html = (
        "<p class=\"sel\">最终低相关精选池：<b>"
        + (", ".join(html.escape(s) for s in selected) if selected else "（无因子通过筛选）")
        + "</b></p>"
    )
    charts = "".join(
        _quantile_chart_html(b, analysis.quantile_means.get(b, pd.Series(dtype=float)),
                             analysis.quantile_stats.get(b, {}))
        for b in selected
    ) or "<p>无入选因子,跳过分层回测图。</p>"

    note = (
        "<div class=\"note\"><b>诚实声明：</b>FDR 把假阳性<b>比例</b>控制在 q 以内,"
        "<b>不保证为零</b>——可能有噪声靠运气通过。因子经 FDR + 分层单调 + 低相关筛出后,"
        "其 edge 是否真实仍须阶段 5 的 Deflated Sharpe（扣试错运气）与 locked holdout"
        "（只看一次）进一步把关。本报告只完成阶段 2 的筛选,不构成"
        "“已找到 edge”的结论。"
    )

    style = (
        "body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:2rem;"
        "color:#222;max-width:1000px}h1{font-size:1.5rem}h2{margin-top:2rem;border-bottom:"
        "2px solid #eee;padding-bottom:.3rem}table{border-collapse:collapse;width:100%;"
        "font-size:.85rem}th,td{border:1px solid #ddd;padding:.3rem .5rem;text-align:right}"
        "th:first-child,td:first-child{text-align:left}tr.hit{background:#e8f5e9;font-weight:600}"
        ".meta{color:#555;font-size:.9rem}.sel{font-size:1.05rem;background:#fffde7;padding:.6rem;"
        "border-radius:4px}.qchart{margin:.4rem 0}.qrow{display:flex;align-items:center;margin:2px 0}"
        ".qlbl{width:2.2rem}.qbar{height:14px;border-radius:2px;margin:0 .5rem}.qval{font-variant-"
        "numeric:tabular-nums}.note{margin-top:2rem;padding:.8rem;background:#fff3e0;border-left:"
        "4px solid #fb8c00;font-size:.9rem;line-height:1.6}"
    )
    return (
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title><style>{style}</style></head><body>"
        f"<h1>{html.escape(title)}</h1>{meta}{sel_html}"
        f"<h2>1. IC / ICIR / FDR 校正后 p 值</h2>{_fdr_table_html(analysis)}"
        f"<h2>2. 入选因子分层回测（Q1→Q5）</h2>{charts}"
        f"{note}</body></html>"
    )


def save_html(
    analysis: FactorAnalysis,
    path: str | Path = "reports/factor_analysis.html",
    title: str = "横截面因子分析报告（阶段 2）",
) -> Path:
    """渲染并写入 HTML 文件,返回路径。父目录不存在则创建。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(analysis, title=title), encoding="utf-8")
    logger.info("因子分析报告已写入 {}", out)
    return out
