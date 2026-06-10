"""阶段 5 Deflated Sharpe 测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.deflated_sharpe import deflated_sharpe_ratio, expected_max_noise_sharpe


def test_expected_noise_sharpe_grows_with_trials():
    one = expected_max_noise_sharpe(n_trials=1, n_observations=252)
    many = expected_max_noise_sharpe(n_trials=100, n_observations=252)
    assert one == pytest.approx(0.0)
    assert many > one


def test_deflated_sharpe_penalizes_trial_count():
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.normal(0.002, 0.01, 500))
    few = deflated_sharpe_ratio(returns, n_trials=1)
    many = deflated_sharpe_ratio(returns, n_trials=200)
    assert few.sharpe == pytest.approx(many.sharpe)
    assert many.benchmark_sharpe > few.benchmark_sharpe
    assert many.pvalue >= few.pvalue
