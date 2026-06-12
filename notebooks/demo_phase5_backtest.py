"""Phase 5 demo: cost-aware backtest + statistical-honesty checks.

This is a synthetic-data teaching demo. It exercises the Phase 5 pipeline without
inspecting any real locked holdout period.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.metrics import performance_summary
from backtest.overfitting_check import (
    deflated_sharpe_report,
    expand_parameter_grid,
    monte_carlo_bootstrap,
    walk_forward_summary,
)
from backtest.walkforward import split_three_way, walk_forward_windows
from backtest.xsec_runner import BacktestConfig, run_backtest
from regime.market_regime import REGIME_CRISIS, REGIME_LOW_VOL


def make_synthetic_panel() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2020-01-01", "2024-06-30", freq="D", tz="UTC")
    symbols = [f"S{i:02d}" for i in range(20)]

    latent_quality = np.zeros((len(dates), len(symbols)))
    latent_quality[0] = rng.normal(0.0, 1.0, size=len(symbols))
    for i in range(1, len(dates)):
        latent_quality[i] = 0.95 * latent_quality[i - 1] + rng.normal(0.0, 0.25, len(symbols))
    noise = rng.normal(0.0, 1.0, size=(len(dates), len(symbols)))
    score = pd.DataFrame(latent_quality + 0.2 * noise, index=dates, columns=symbols)

    next_return = rng.normal(0.0, 0.015, size=score.shape)
    next_return[1:] += 0.002 * latent_quality[:-1]
    open_prices = pd.DataFrame(index=dates, columns=symbols, dtype=float)
    open_prices.iloc[0] = 100.0
    for i in range(len(dates) - 1):
        open_prices.iloc[i + 1] = open_prices.iloc[i] * (1.0 + next_return[i])

    funding = pd.DataFrame(rng.normal(0.0, 0.0002, size=score.shape), index=dates, columns=symbols)
    regime = pd.Series(REGIME_LOW_VOL, index=dates)
    regime.loc["2022-05-01":"2022-06-30"] = REGIME_CRISIS
    return score, open_prices, funding, regime


def main() -> None:
    score, open_prices, funding, regime = make_synthetic_panel()
    split = split_three_way(score.index)  # holdout stays locked by default
    print(f"in-sample days={len(split.in_sample)}, validation days={len(split.validation)}")

    result = run_backtest(
        score.loc[split.in_sample.union(split.validation)],
        open_prices.loc[split.in_sample.union(split.validation)],
        funding_rates=funding.loc[split.in_sample.union(split.validation)],
        regime=regime.loc[split.in_sample.union(split.validation)],
        config=BacktestConfig(quantile=0.2, no_trade_band=0.05, taker_fee=0.0005, slippage=0.0005),
    )
    summary = performance_summary(
        result.net_return,
        turnover=result.turnover,
        n_bootstrap=300,
        seed=1,
    )
    print("summary:")
    for key in [
        "annualized_return",
        "sharpe",
        "sharpe_ci_low",
        "sharpe_ci_high",
        "max_drawdown",
        "avg_turnover",
    ]:
        print(f"  {key}: {summary[key]:.4f}")

    grid = {"window": [7, 14, 30], "quantile": [0.1, 0.2], "band": [0.0, 0.05]}
    print(f"parameter trials recorded for DSR: {len(expand_parameter_grid(grid))}")
    dsr = deflated_sharpe_report(result.net_return, parameter_grid=grid)
    print(
        "deflated sharpe: "
        f"sharpe={dsr.sharpe:.3f}, benchmark={dsr.benchmark_sharpe:.3f}, "
        f"p={dsr.pvalue:.4f}, passed={dsr.passed}"
    )
    ci = monte_carlo_bootstrap(result.net_return, n_bootstrap=300, seed=2)
    print(f"bootstrap sharpe CI: [{ci['ci_low']:.3f}, {ci['ci_high']:.3f}]")

    windows = walk_forward_windows(score.index, train_months=18, validation_months=6, step_months=3)
    wf_rows = []
    for i, window in enumerate(windows[:4], start=1):
        val_idx = score.loc[window.validation_start: window.validation_end].index
        val_result = run_backtest(
            score.loc[val_idx],
            open_prices.loc[val_idx],
            funding_rates=funding.loc[val_idx],
            regime=regime.loc[val_idx],
            config=BacktestConfig(quantile=0.2, no_trade_band=0.05),
        )
        wf_rows.append({"window": i, "sharpe": performance_summary(val_result.net_return)["sharpe"]})
    print("walk-forward summary:", walk_forward_summary(pd.DataFrame(wf_rows), "sharpe"))


if __name__ == "__main__":
    main()
