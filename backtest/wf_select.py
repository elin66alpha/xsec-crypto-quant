"""Walk-forward parameter selection pipeline.

Each window selects parameters on the training slice only, then evaluates the selected
parameter set on that window's validation slice. The stitched OOS return series contains
validation periods only; overlapping validation dates are kept once in chronological order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pandas as pd

from .metrics import sharpe_ratio
from .overfitting_check import expand_parameter_grid
from .walkforward import walk_forward_windows


@dataclass(frozen=True)
class WalkForwardReport:
    """Result of walk-forward parameter selection."""

    per_window: pd.DataFrame
    oos_returns: pd.Series
    n_trials_total: int


RunFn = Callable[[dict[str, object], pd.Index], pd.Series]


def _normalize_index(index: pd.Index) -> pd.DatetimeIndex:
    dt_index = pd.DatetimeIndex(index).sort_values().drop_duplicates()
    if dt_index.tz is None:
        return dt_index.tz_localize("UTC")
    return dt_index.tz_convert("UTC")


def _slice_index(
    index: pd.DatetimeIndex,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DatetimeIndex:
    return index[(index >= start) & (index <= end)]


def _run_segment(run_fn: RunFn, params: dict[str, object], segment_idx: pd.Index) -> pd.Series:
    returns = pd.Series(run_fn(params, segment_idx), dtype=float)
    return returns.reindex(segment_idx)


def run_walk_forward(
    grid: dict[str, Iterable[object]],
    run_fn: RunFn,
    index: pd.Index,
    train_months: int = 18,
    validation_months: int = 6,
    step_months: int = 3,
) -> WalkForwardReport:
    """Run train-select, validation-evaluate walk-forward parameter selection.

    Parameters
    ----------
    grid
        Parameter grid. Empty grids are treated as a single ``{}`` trial.
    run_fn
        Callable receiving ``(params, date_index)`` and returning net returns for that
        exact segment.
    index
        Full research date index used to derive walk-forward windows.
    train_months / validation_months / step_months
        Passed through to :func:`backtest.walkforward.walk_forward_windows`.
    """
    dt_index = _normalize_index(index)
    params_grid = expand_parameter_grid(grid)
    windows = walk_forward_windows(
        dt_index,
        train_months=train_months,
        validation_months=validation_months,
        step_months=step_months,
    )

    rows: list[dict[str, object]] = []
    oos_segments: list[pd.Series] = []
    emitted_dates: set[pd.Timestamp] = set()

    for window_id, window in enumerate(windows):
        train_idx = _slice_index(dt_index, window.train_start, window.train_end)
        val_idx = _slice_index(dt_index, window.validation_start, window.validation_end)
        if train_idx.empty or val_idx.empty:
            continue

        train_results: list[tuple[float, dict[str, object]]] = []
        for params in params_grid:
            train_returns = _run_segment(run_fn, params, train_idx)
            train_results.append((sharpe_ratio(train_returns), params))

        best_train_sharpe, best_params = max(
            train_results,
            key=lambda item: float("-inf") if pd.isna(item[0]) else item[0],
        )
        val_returns = _run_segment(run_fn, best_params, val_idx)
        val_sharpe = sharpe_ratio(val_returns)

        new_val_idx = [ts for ts in val_returns.index if ts not in emitted_dates]
        if new_val_idx:
            stitched = val_returns.loc[new_val_idx]
            oos_segments.append(stitched)
            emitted_dates.update(stitched.index)

        rows.append(
            {
                "window": window_id,
                "train_start": window.train_start,
                "train_end": window.train_end,
                "validation_start": window.validation_start,
                "validation_end": window.validation_end,
                "selected_params": dict(best_params),
                "train_sharpe": best_train_sharpe,
                "validation_sharpe": val_sharpe,
            }
        )

    if oos_segments:
        oos_returns = pd.concat(oos_segments).sort_index().rename("oos_returns")
    else:
        oos_returns = pd.Series(dtype=float, name="oos_returns")

    return WalkForwardReport(
        per_window=pd.DataFrame(rows),
        oos_returns=oos_returns,
        n_trials_total=len(windows) * len(params_grid),
    )
