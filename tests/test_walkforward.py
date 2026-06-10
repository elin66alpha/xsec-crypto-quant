"""阶段 5 样本切分与 walk-forward 测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.walkforward import (
    HoldoutLockedError,
    SplitConfig,
    require_holdout_unlocked,
    split_three_way,
    walk_forward_windows,
)


def test_three_way_split_locks_holdout_by_default():
    idx = pd.date_range("2020-01-01", "2024-03-31", freq="D", tz="UTC")
    split = split_three_way(idx)
    assert split.in_sample.min() == pd.Timestamp("2020-01-01", tz="UTC")
    assert split.validation.min() == pd.Timestamp("2023-01-01", tz="UTC")
    assert split.holdout is None


def test_three_way_split_requires_explicit_holdout_unlock():
    idx = pd.date_range("2020-01-01", "2024-03-31", freq="D", tz="UTC")
    cfg = SplitConfig(holdout_start="2024-01-01", holdout_end="2024-03-31")
    split = split_three_way(idx, cfg, unlock_holdout=True)
    assert split.holdout is not None
    assert split.holdout.min() == pd.Timestamp("2024-01-01", tz="UTC")
    with pytest.raises(HoldoutLockedError):
        require_holdout_unlocked(False)


def test_walk_forward_windows_18_6_3():
    idx = pd.date_range("2020-01-01", "2022-12-31", freq="D", tz="UTC")
    windows = walk_forward_windows(idx, train_months=18, validation_months=6, step_months=3)
    assert len(windows) == 5
    first = windows[0]
    assert first.train_start == pd.Timestamp("2020-01-01", tz="UTC")
    assert first.validation_start == pd.Timestamp("2021-07-01", tz="UTC")
