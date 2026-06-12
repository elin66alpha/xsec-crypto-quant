"""Walk-forward parameter selection pipeline tests."""

from __future__ import annotations

import pandas as pd

from backtest.walkforward import walk_forward_windows
from backtest.wf_select import run_walk_forward


def test_run_walk_forward_selects_known_good_parameter_most_windows():
    index = pd.date_range("2020-01-01", "2023-12-31", freq="D", tz="UTC")
    calls: list[tuple[dict[str, object], pd.Index]] = []

    def run_fn(params: dict[str, object], segment_idx: pd.Index) -> pd.Series:
        calls.append((params, segment_idx))
        base = 0.002 if params["window"] == 14 else -0.002
        values = [base + (0.0005 if i % 2 else -0.0005) for i in range(len(segment_idx))]
        return pd.Series(values, index=segment_idx)

    report = run_walk_forward(
        {"window": [7, 14, 30]},
        run_fn,
        index,
        train_months=12,
        validation_months=3,
        step_months=3,
    )

    selected = report.per_window["selected_params"].map(lambda params: params["window"])
    assert (selected == 14).mean() > 0.5
    windows = walk_forward_windows(
        index,
        train_months=12,
        validation_months=3,
        step_months=3,
    )
    assert report.n_trials_total == len(windows) * 3
    assert report.oos_returns.mean() > 0
    assert calls


def test_run_walk_forward_oos_contains_validation_only_without_overlap():
    index = pd.date_range("2020-01-01", "2022-12-31", freq="D", tz="UTC")

    def leaky_run_fn(params: dict[str, object], segment_idx: pd.Index) -> pd.Series:
        del params
        expanded = pd.date_range(
            segment_idx.min() - pd.Timedelta(days=5),
            segment_idx.max() + pd.Timedelta(days=5),
            freq="D",
            tz="UTC",
        )
        return pd.Series(1.0, index=expanded)

    report = run_walk_forward(
        {"p": [1]},
        leaky_run_fn,
        index,
        train_months=12,
        validation_months=6,
        step_months=3,
    )

    windows = walk_forward_windows(
        index,
        train_months=12,
        validation_months=6,
        step_months=3,
    )
    validation_dates: set[pd.Timestamp] = set()
    training_dates: set[pd.Timestamp] = set()
    for window in windows:
        validation_dates.update(
            index[(index >= window.validation_start) & (index <= window.validation_end)]
        )
        training_dates.update(index[(index >= window.train_start) & (index <= window.train_end)])

    assert report.oos_returns.index.is_unique
    assert set(report.oos_returns.index) == validation_dates
    assert set(report.oos_returns.index).isdisjoint(training_dates - validation_dates)
