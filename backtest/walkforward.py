"""三段切分与 walk-forward 窗口（阶段 5）。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


class HoldoutLockedError(RuntimeError):
    """locked holdout 未解锁时被访问。"""


@dataclass(frozen=True)
class SplitConfig:
    """默认三段切分：2020-2022 / 2023 / 最近 6-12 个月 holdout 可由调用方传入。"""

    in_sample_start: str = "2020-01-01"
    in_sample_end: str = "2022-12-31"
    validation_start: str = "2023-01-01"
    validation_end: str = "2023-12-31"
    holdout_start: str = "2024-01-01"
    holdout_end: str | None = None


@dataclass(frozen=True)
class SplitResult:
    in_sample: pd.Index
    validation: pd.Index
    holdout: pd.Index | None


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp


def _utc_ts(value: str | None, fallback: pd.Timestamp | None = None) -> pd.Timestamp:
    if value is None:
        if fallback is None:
            raise ValueError("fallback required when value is None")
        return fallback
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def split_three_way(
    index: pd.Index,
    config: SplitConfig | None = None,
    unlock_holdout: bool = False,
) -> SplitResult:
    """按日期切分样本；默认不返回 holdout，防止误看。

    真正解锁 holdout 前，调用方应先创建 ``pre-holdout-freeze`` tag 并显式传
    ``unlock_holdout=True``。本函数只提供代码层面的护栏，流程纪律仍由 git tag 留证。
    """
    config = config or SplitConfig()
    dt_index = pd.DatetimeIndex(index)
    if dt_index.tz is None:
        dt_index = dt_index.tz_localize("UTC")
    else:
        dt_index = dt_index.tz_convert("UTC")

    last = dt_index.max()
    is_start = _utc_ts(config.in_sample_start)
    is_end = _utc_ts(config.in_sample_end)
    val_start = _utc_ts(config.validation_start)
    val_end = _utc_ts(config.validation_end)
    hold_start = _utc_ts(config.holdout_start)
    hold_end = _utc_ts(config.holdout_end, fallback=last)

    in_sample = dt_index[(dt_index >= is_start) & (dt_index <= is_end)]
    validation = dt_index[(dt_index >= val_start) & (dt_index <= val_end)]
    holdout_idx = dt_index[(dt_index >= hold_start) & (dt_index <= hold_end)]
    if not unlock_holdout:
        return SplitResult(in_sample=in_sample, validation=validation, holdout=None)
    return SplitResult(in_sample=in_sample, validation=validation, holdout=holdout_idx)


def require_holdout_unlocked(unlock_holdout: bool) -> None:
    """访问 locked holdout 前显式调用；默认抛错。"""
    if not unlock_holdout:
        raise HoldoutLockedError(
            "locked holdout cannot be inspected before pre-holdout-freeze and explicit unlock"
        )


def walk_forward_windows(
    index: pd.Index,
    train_months: int = 18,
    validation_months: int = 6,
    step_months: int = 3,
) -> list[WalkForwardWindow]:
    """生成 train 18M / validation 6M / step 3M 的滚动窗口。"""
    if min(train_months, validation_months, step_months) <= 0:
        raise ValueError("窗口月份参数必须为正")
    dt_index = pd.DatetimeIndex(index).sort_values()
    if dt_index.empty:
        return []
    if dt_index.tz is None:
        dt_index = dt_index.tz_localize("UTC")
    else:
        dt_index = dt_index.tz_convert("UTC")

    windows: list[WalkForwardWindow] = []
    start = dt_index.min()
    last = dt_index.max()
    while True:
        train_start = start
        train_end = train_start + pd.DateOffset(months=train_months) - pd.Timedelta(days=1)
        val_start = train_end + pd.Timedelta(days=1)
        val_end = val_start + pd.DateOffset(months=validation_months) - pd.Timedelta(days=1)
        if val_end > last:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                validation_start=val_start,
                validation_end=val_end,
            )
        )
        start = start + pd.DateOffset(months=step_months)
    return windows
