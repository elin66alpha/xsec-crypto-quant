"""因子筛选编排（阶段 2 收口）。

把阶段 2 的四步串成一次完整筛选,产出可被审计的结构化结果：

    1. 对每个 因子 × horizon 算 IC 汇总（ic_analysis）
    2. 多重检验 FDR 校正（multiple_testing）
    3. 取"经 FDR 后仍显著"的基础因子,两两相关剔除（correlation,>阈值留 ICIR 更高者）
    4. 给最终入选因子各做一次分层回测（quantile_backtest），供报告画图

设计为纯逻辑（不画图、不写文件,那是 report.py 的事）,可离线单测。

诚实声明：FDR 控制假阳性比例≤q,**不保证为零**；相关剔除用 |ICIR| 最高的 horizon 代表
该基础因子。最终入选 ≤ max_factors（CLAUDE.md：3–8 个、低相关）。是否"真有 edge"仍需
阶段 5 的 Deflated Sharpe + locked holdout 进一步把关——本模块只完成阶段 2 的筛选。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .correlation import factor_correlation_matrix, select_low_correlation
from .ic_analysis import cross_sectional_ic, forward_return, ic_summary
from .multiple_testing import DEFAULT_FDR_Q, fdr_report, fdr_summary
from .quantile_backtest import quantile_backtest

DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 5, 10)


@dataclass
class FactorAnalysis:
    """阶段 2 筛选的完整结果（供 report.py 渲染,或直接审阅）。"""

    horizons: tuple[int, ...]
    q: float
    corr_threshold: float
    ic_summaries: dict[str, dict[str, float]]          # "name@{h}d" -> ic_summary
    fdr_table: pd.DataFrame                            # fdr_report 输出（按 p 升序）
    fdr_counts: dict[str, int]                         # 校正前/后通过数对比
    surviving_bases: list[str]                         # 经 FDR 后至少一个 horizon 显著的基础因子
    correlation: pd.DataFrame                          # surviving_bases 之间的相关矩阵
    selected: list[str]                                # 相关剔除后的最终低相关精选池
    best_horizon: dict[str, int]                       # 每个 surviving base 的代表 horizon
    quantile_means: dict[str, pd.Series] = field(default_factory=dict)  # selected 的分层均值
    quantile_stats: dict[str, dict[str, float]] = field(default_factory=dict)  # 单调性/价差


def compute_ic_summaries(
    panels: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> dict[str, dict[str, float]]:
    """对每个 因子 × horizon 算 IC 汇总,键为 ``"name@{h}d"``。

    horizon=h 的重叠前向收益 IC 序列按约定传 ``hac_lags=h-1`` 给 ``ic_summary``；
    horizon=1 时退化为旧版裸 t 值。
    """
    fwd_cache = {h: forward_return(close, h) for h in horizons}
    out: dict[str, dict[str, float]] = {}
    for name, fac in panels.items():
        for h in horizons:
            ic = cross_sectional_ic(fac, fwd_cache[h])
            out[f"{name}@{h}d"] = ic_summary(ic, hac_lags=h - 1)
    return out


def _base_of(key: str) -> str:
    """"momentum_20@5d" -> "momentum_20"。"""
    return key.split("@", 1)[0]


def _best_horizon_for(
    base: str, summaries: dict[str, dict[str, float]], horizons: tuple[int, ...]
) -> int:
    """该基础因子里 |ICIR| 最大的 horizon 作为代表。"""
    def abs_icir(h: int) -> float:
        v = summaries.get(f"{base}@{h}d", {}).get("icir", float("nan"))
        return abs(v) if v is not None and np.isfinite(v) else 0.0

    return max(horizons, key=abs_icir)


def analyze_factors(
    panels: dict[str, pd.DataFrame],
    close: pd.DataFrame,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    q: float = DEFAULT_FDR_Q,
    corr_threshold: float = 0.7,
    max_factors: int = 10,
    n_quantiles: int = 5,
) -> FactorAnalysis:
    """运行阶段 2 完整筛选,返回 ``FactorAnalysis``。"""
    summaries = compute_ic_summaries(panels, close, horizons)
    fdr_table = fdr_report(summaries, q=q)
    fdr_counts = fdr_summary(fdr_table)

    # 1) 经 FDR 后至少一个 horizon 显著的基础因子
    surviving_keys = fdr_table.index[fdr_table["reject"]].tolist()
    surviving_bases = list(dict.fromkeys(_base_of(k) for k in surviving_keys))  # 去重保序

    best_horizon = {b: _best_horizon_for(b, summaries, horizons) for b in surviving_bases}

    # 2) 在存活基础因子之间做相关剔除
    if surviving_bases:
        corr = factor_correlation_matrix({b: panels[b] for b in surviving_bases})
        icir_scores = {
            b: summaries[f"{b}@{best_horizon[b]}d"].get("icir", float("nan"))
            for b in surviving_bases
        }
        selected = select_low_correlation(
            corr, icir_scores, threshold=corr_threshold, max_factors=max_factors
        )
    else:
        corr = pd.DataFrame()
        selected = []

    # 3) 给最终入选因子各做一次分层回测（供报告画图）
    quantile_means: dict[str, pd.Series] = {}
    quantile_stats: dict[str, dict[str, float]] = {}
    for b in selected:
        h = best_horizon[b]
        res = quantile_backtest(panels[b], forward_return(close, h), n_quantiles=n_quantiles)
        quantile_means[b] = res["quantile_means"]  # type: ignore[assignment]
        quantile_stats[b] = {
            "horizon": float(h),
            "monotonicity": float(res["monotonicity"]),  # type: ignore[arg-type]
            "spread": float(res["spread"]),  # type: ignore[arg-type]
        }

    return FactorAnalysis(
        horizons=horizons,
        q=q,
        corr_threshold=corr_threshold,
        ic_summaries=summaries,
        fdr_table=fdr_table,
        fdr_counts=fdr_counts,
        surviving_bases=surviving_bases,
        correlation=corr,
        selected=selected,
        best_horizon=best_horizon,
        quantile_means=quantile_means,
        quantile_stats=quantile_stats,
    )
