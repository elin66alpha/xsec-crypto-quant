"""参数敏感性、bootstrap 与 walk-forward 汇总（阶段 5）。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from itertools import product

import numpy as np
import pandas as pd

from .deflated_sharpe import DeflatedSharpeResult, deflated_sharpe_ratio
from .metrics import TRADING_DAYS_CRYPTO, bootstrap_ci, sharpe_ratio


def expand_parameter_grid(grid: dict[str, Iterable[object]]) -> list[dict[str, object]]:
    """把参数网格展开为组合列表；用于记录实际试验次数。"""
    if not grid:
        return [{}]
    keys = list(grid)
    values = [list(grid[k]) for k in keys]
    return [dict(zip(keys, combo, strict=True)) for combo in product(*values)]


def parameter_sensitivity(
    results: pd.DataFrame,
    parameter_columns: list[str],
    metric_column: str,
) -> pd.DataFrame:
    """逐参数汇总 metric 的 min/max/std/range，检查是否只在尖点上有效。"""
    rows: list[dict[str, object]] = []
    for param in parameter_columns:
        grouped = results.groupby(param)[metric_column]
        summary = grouped.agg(["count", "mean", "std", "min", "max"]).reset_index()
        summary["range"] = summary["max"] - summary["min"]
        summary.insert(0, "parameter", param)
        summary = summary.rename(columns={param: "value"})
        rows.extend(summary.to_dict("records"))
    return pd.DataFrame(rows)


def monte_carlo_bootstrap(
    returns: pd.Series,
    statistic: Callable[[pd.Series], float] | None = None,
    n_bootstrap: int = 1000,
    seed: int = 0,
    block_length: int = 20,
) -> dict[str, float]:
    """Monte Carlo circular block bootstrap；默认统计量为年化 Sharpe。"""
    statistic = statistic or (lambda x: sharpe_ratio(x, TRADING_DAYS_CRYPTO))
    lo, hi = bootstrap_ci(
        returns,
        statistic,
        n_bootstrap=n_bootstrap,
        seed=seed,
        block_length=block_length,
    )
    return {
        "ci_low": lo,
        "ci_high": hi,
        "n_bootstrap": float(n_bootstrap),
        "block_length": float(block_length),
    }


def select_validation_candidate(
    results: pd.DataFrame,
    metric_column: str,
    min_metric: float | None = None,
) -> pd.Series:
    """仅基于 validation 结果选择候选；不读取 holdout。"""
    candidates = results.copy()
    if min_metric is not None:
        candidates = candidates[candidates[metric_column] >= min_metric]
    if candidates.empty:
        raise ValueError("没有满足约束的 validation 候选")
    return candidates.sort_values(metric_column, ascending=False).iloc[0]


def deflated_sharpe_report(
    returns: pd.Series,
    parameter_grid: dict[str, Iterable[object]] | None = None,
    n_trials: int | None = None,
    alpha: float = 0.05,
) -> DeflatedSharpeResult:
    """按参数试验次数生成 DSR 报告。"""
    if n_trials is None:
        n_trials = len(expand_parameter_grid(parameter_grid or {}))
    return deflated_sharpe_ratio(returns, n_trials=max(1, n_trials), alpha=alpha)


def walk_forward_summary(results: pd.DataFrame, metric_column: str) -> dict[str, float]:
    """汇总每个 walk-forward validation 窗口的表现，强调跨窗口稳定性。"""
    clean = results[metric_column].replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return {
            "n_windows": 0.0,
            "mean": float("nan"),
            "median": float("nan"),
            "hit_rate_positive": float("nan"),
            "worst": float("nan"),
        }
    return {
        "n_windows": float(len(clean)),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "hit_rate_positive": float((clean > 0.0).mean()),
        "worst": float(clean.min()),
    }
