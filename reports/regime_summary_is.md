# Phase 3 Regime Analysis Summary (real data, in-sample)

> WARNING: `label_regimes_full` is a full-sample HMM fit and contains look-ahead. It is included only for research visualization. Downstream/backtest use must rely on `label_regimes_expanding`.

## Configuration

- Window: 2020-01-01 to 2024-06-30 UTC
- HMM n_states: 3 (hard-coded)
- Correlation spike: default SpikeConfig(abs_threshold=0.7, baseline_window=60, z_threshold=2.0, min_baseline=20)
- Trials ledger: not written; regime fitting is not a factor trial.
- Daily in-pool asset count: min 0, median 30, max 30

## State Proportions

| State | Expanding honest | Full research/look-ahead |
|---|---:|---:|
| 0 Low-vol | 61.35% | 46.65% |
| 1 Trend | 30.75% | 31.74% |
| 2 Crisis | 7.90% | 21.61% |

## State Switching

| Label source | Switches | Annualized switches |
|---|---:|---:|
| Expanding honest | 48 | 13.312073 |
| Full research/look-ahead | 44 | 10.242830 |

## Feature Means By Expanding State

| State | market_vol | xs_corr_median | mean_abs_return |
|---|---:|---:|---:|
| 0 Low-vol | 0.034643 | 0.604267 | 0.037209 |
| 1 Trend | 0.051614 | 0.618715 | 0.052544 |
| 2 Crisis | 0.081832 | 0.814190 | 0.071530 |

## Correlation Spike And Deleveraging

- Corr-spike days: 628 / 1643 (38.22%)
- Deleveraged days (gross multiplier < 1): 632 / 1643 (38.47%)

## Expanding Crisis Intervals

| Start | End | Days |
|---|---|---:|
| 2021-01-11 | 2021-01-11 | 1 |
| 2021-05-30 | 2021-07-04 | 36 |
| 2021-09-07 | 2021-09-07 | 1 |
| 2021-09-20 | 2021-09-22 | 3 |
| 2022-01-21 | 2022-01-22 | 2 |
| 2022-05-04 | 2022-05-30 | 27 |
| 2022-06-15 | 2022-06-16 | 2 |
| 2022-11-08 | 2022-11-22 | 15 |
| 2023-03-12 | 2023-03-12 | 1 |
| 2023-06-10 | 2023-06-10 | 1 |
| 2024-04-12 | 2024-04-26 | 15 |
