# AGENTS.md

This file is the handoff entry point for any AI agent working in this repository.
Read it before changing code, then read `README.md`, `CLAUDE.md`, and the files
linked below that match the task.

## Project Snapshot

- Project: cross-sectional crypto long/short quant research system.
- Goal: statistically honest search for a real edge, not curve-fit profitability.
- Current exchange/data source in code: OKX USDT-margined perpetual swaps via `ccxt`.
- Historical docs explain the exchange switch: the implemented data layer uses OKX
  because Binance global endpoints return 451 from the current US network location.
- Target cadence: daily signals and rebalance, monthly dynamic universe selection.
- Portfolio mandate: long/short, dollar neutral, single side leverage <= 1x, gross <= 2x.
- Data rule: use only information available at the historical timestamp. No look-ahead,
  no survivorship-biased universe for claims about early history.

## Current Repo State

Phase 0 closeout status:

- Branches before closeout: `main` and `phase-0-data` both pointed to `ba760f4`.
- Remote: `main` tracks `origin/main`.
- Phase 0 closeout is committed on `main`; tag `v0.0-data` points at the closeout commit.
- Local `phase-0-data` was fast-forwarded to the same closeout commit.
- Active local environment: Miniconda env `/home/pc/miniconda3/envs/xsec-crypto-quant`
  with Python 3.12.13.
- Project runtime docs now use Python 3.12 because current `pandas-ta` requires
  Python >= 3.12 and Python 3.11 cannot resolve the declared dependency set.

Phase 0 is now closed out in the working tree:

- Done: repository skeleton and package config in `pyproject.toml`.
- Done: `.env.example` updated for OKX, CoinGecko/CMC, universe, split, risk, and cost settings.
- Done: data availability audit in `docs/data_availability_audit.md`.
- Done: dynamic universe logic in `data/universe.py`.
- Done: paginated OHLCV/funding fetchers in `data/fetcher.py`.
- Done: month-sharded parquet storage in `data/storage.py`.
- Done: data validation checks in `data/validate.py`.
- Done: offline tests in `tests/test_universe.py` and `tests/test_data.py`.
- Done: Phase 0 notebooks:
  - `notebooks/01_data_fetch_storage_validate.ipynb`
  - `notebooks/02_dynamic_universe_evolution.ipynb`
- Done: README/CLAUDE environment and exchange references reconciled to Python 3.12 and OKX.

Phase 1 (cross-sectional feature engineering) is now complete in `features/`:

- Done: `features/preprocess.py` — cross-sectional rank normalization (decision 5/11),
  raw factor in → per-day rank out; NaN excluded; optional centering for dollar-neutral.
- Done: `features/cross_sectional.py` — `momentum`, `realized_volatility`, `volume_change`
  raw factors, parameterized by `window`; `CANDIDATE_WINDOWS = (7,14,30,60,90)`.
- Done: `features/carry.py` — funding carry (decision 3): `to_daily_funding` (8h→daily)
  then `funding_carry` rolling mean.
- Done: `features/orderflow.py` — CVD / Trade Delta, **confirmation-only, never historical
  backtest** (audit downgraded trades to record-from-now-only; L2 deleted entirely).
- Done: tests `tests/test_features.py`, `tests/test_cross_sectional.py`,
  `tests/test_carry.py`, `tests/test_orderflow.py` (50 tests total with Phase 0).
- Done: teaching demos `notebooks/demo_phase1_cross_sectional.py` (rank concept) and
  `notebooks/demo_phase1_all_factors.py` (all factors + perf check).

Phase 2 (cross-sectional factor analysis) is now complete in `factors/`:

- Done: `factors/ic_analysis.py` — `forward_return`, `cross_sectional_ic` (Spearman),
  `ic_summary` (mean_ic / icir / hit_rate / t_stat); horizons 1D/3D/5D/10D.
- Done: `factors/multiple_testing.py` — Benjamini-Hochberg FDR (`pvalue_from_tstat`,
  `benjamini_hochberg`, `fdr_report`, `fdr_summary`); q is a parameter, default 0.10,
  final value deferred until real p-value distribution is seen (record the reason).
- Done: `factors/quantile_backtest.py` — `assign_quantiles`, `quantile_returns`,
  `monotonicity`, `long_short_spread`, `quantile_backtest` (Q1→Q5 monotonicity).
- Done: `factors/correlation.py` — `factor_correlation_matrix`, `select_low_correlation`
  (drop |corr|>0.7 keeping higher ICIR, pool ≤ max_factors).
- Done: `factors/selection.py` — `analyze_factors` orchestrates IC→FDR→correlation→
  quantile into a `FactorAnalysis` result.
- Done: `factors/report.py` — `render_html`/`save_html` self-contained HTML report
  (IC/ICIR/FDR q-values table + CSS quantile bar charts), writes to `reports/` (gitignored).
- Done: tests `tests/test_ic_analysis.py`, `test_multiple_testing.py`,
  `test_quantile_backtest.py`, `test_correlation.py`, `test_selection.py`,
  `test_report.py` (89 tests total with Phases 0–1).
- Done: teaching demo `notebooks/demo_phase2_factor_analysis.py` (full IC→FDR→quantile→
  correlation pipeline on planted ground-truth data + HTML report export).
- Honest finding baked into the demo: FDR controls the false-discovery *rate* ≤ q but
  does NOT guarantee zero — a pure-noise factor can slip through. This is exactly why
  Phase 5 adds Deflated Sharpe + locked holdout as further defenses.

Phase 3 (market-level regime analysis) is now complete in `regime/`:

- Done: `regime/market_regime.py` — pure feature layer (`compute_market_features`:
  market_vol / xs_corr_median / mean_abs_return) + HMM layer (lazy hmmlearn):
  `fit_hmm` (3-state GaussianHMM), `_vol_order_mapping` (raw states → low0/trend1/crisis2
  by volatility), `label_regimes_full` (full-sample, look-ahead, RESEARCH ONLY), and
  `label_regimes_expanding` (expanding-window, no look-ahead, the honest default for backtest).
  BTC dominance feature deferred (needs market-cap data) — recorded, not blocking.
- Done: `regime/correlation_spike.py` — ironclad rule 2: `correlation_spike_signal`
  (absolute >threshold OR relative z-score vs shift(1) trailing baseline) and
  `detect_correlation_spike`. Thresholds parameterized, default conservative, finalize on
  real data and record the reason.
- Done: `regime/risk_params.py` — REGIME_RISK_PARAMS (crisis→0.2x), `effective_gross_
  multiplier` (HMM regime AND corr-spike, take the more conservative), and hard clamps
  `clamp_gross`/`clamp_single_side`/`apply_regime_leverage` (single ≤1x, gross ≤2x, fixed).
- Done: tests `tests/test_market_regime.py`, `test_correlation_spike.py`,
  `test_risk_params.py` (107 tests total with Phases 0–2).
- Done: teaching demo `notebooks/demo_phase3_regime.py` (calm→crisis→calm: features rise,
  HMM flags crisis, spike fires earlier than HMM, gross leverage auto-cut 2.0x→0.4x, never
  breaches the 2x limit).
- HMM state count fixed at 3 per user decision. hmmlearn added to the `.venv`.

Phase 4 (cross-sectional long/short strategy) is now complete in `strategy/`:

- Done: `strategy/signal.py` — `cross_sectional_score` (centered rank per factor,
  oriented by IC sign via `signs_from_mean_ic`, equal-weight `equal_weights`, NaN-friendly).
- Done: `strategy/portfolio.py` — `build_target_weights` (long top / short bottom quantile,
  equal-weight within leg, dollar-neutral, base gross 2x) and `apply_regime_leverage`
  (regime+spike multiplier then hard clamp single≤1x/gross≤2x via `_clamp_row`).
- Done: `strategy/rebalance.py` — `apply_no_trade_band` (decision 4b, default 0.05,
  force-close on delist), `to_execution` (shift 1 bar = close-after-bar→next-open,
  decision 9), `turnover`/`turnover_reduction`.
- Done: `strategy/risk.py` — exposure helpers + `check_portfolio`/`assert_within_limits`
  (reuses data-layer ValidationReport; gross/single-side breaches = error, off-neutral = warning).
- Done: tests `tests/test_signal.py`, `test_portfolio.py`, `test_rebalance.py`,
  `test_strategy_risk.py` (136 tests total with Phases 0–3).
- Done: teaching demo `notebooks/demo_phase4_strategy.py` (factors→score→book→band→
  regime-deleverage→execution→risk).
- Pipeline-order finding from the demo: the no-trade band can drift gross/net, so the
  leverage clamp MUST be the LAST transform (band on target first, then
  apply_regime_leverage). A weight-delta band barely binds on an equal-weight quantile
  book (discrete ±0.25 jumps) — membership hysteresis is the real turnover lever (Phase 5).
- Decisions fixed by user: equal-weight factor combination; no-trade band parameterized
  (default 0.05, finalize in Phase 5).

Phase 5 (backtest and statistical-honesty checks) is now complete in `backtest/`:

- Done: `backtest/xsec_runner.py` — cost-aware cross-sectional long/short backtester:
  score → quantile target weights → no-trade band → regime/correlation deleverage →
  next-open execution shift → open-to-open PnL; deducts taker fee, slippage, and funding
  each period. Funding PnL uses perpetual convention: positive funding means longs pay
  and shorts receive.
- Done: `backtest/metrics.py` — annualized return/volatility, Sharpe, Sortino, Calmar,
  max drawdown, win rate, realized BTC beta helper, turnover summary, and bootstrap
  Sharpe confidence intervals. Reports are CI-first rather than naked point estimates.
- Done: `backtest/walkforward.py` — three-way sample split with locked-holdout guard
  (holdout is not returned unless explicitly unlocked) plus train 18M / validation 6M /
  step 3M walk-forward windows.
- Done: `backtest/deflated_sharpe.py` — Deflated Sharpe Ratio approximation that penalizes
  the number of parameter trials and adjusts for skew/kurtosis.
- Done: `backtest/overfitting_check.py` — parameter-grid expansion, parameter sensitivity,
  validation-only candidate selection, Monte Carlo bootstrap wrapper, DSR report, and
  walk-forward summary.
- Done: tests `tests/test_backtest_runner.py`, `test_backtest_metrics.py`,
  `test_walkforward.py`, `test_deflated_sharpe.py`, `test_overfitting_check.py`
  (154 tests total with Phases 0–4).
- Done: teaching demo `notebooks/demo_phase5_backtest.py` (synthetic predictive panel;
  three-way split without holdout unlock; cost/funding-aware backtest; bootstrap CI;
  Deflated Sharpe with recorded parameter-trial count; walk-forward summary).
- Important: Phase 5 adds the backtest/statistics machinery but does **not** inspect real
  locked holdout data. Before any real holdout unlock, tag `pre-holdout-freeze`, freeze
  parameters, then inspect holdout once only.
- Not done: Phase 6+ implementation. `live/` currently contains only `__init__.py`.

## Verification Status

Environment setup:

```bash
/home/pc/miniconda3/bin/conda install -n xsec-crypto-quant \
  -c conda-forge --override-channels python=3.12 -y
/home/pc/miniconda3/bin/conda run -n xsec-crypto-quant \
  python -m pip install -e ".[dev]"
```

Verification commands run:

```bash
/home/pc/miniconda3/bin/conda run -n xsec-crypto-quant python -m pytest -q
/home/pc/miniconda3/bin/conda run -n xsec-crypto-quant python -m ruff check .
/home/pc/miniconda3/bin/conda run -n xsec-crypto-quant python -m mypy data tests
/home/pc/miniconda3/bin/conda run -n xsec-crypto-quant jupyter nbconvert \
  --to notebook --execute --inplace \
  notebooks/01_data_fetch_storage_validate.ipynb \
  notebooks/02_dynamic_universe_evolution.ipynb
```

Results:

- `pytest`: 15 passed.
- `ruff`: all checks passed.
- `mypy data tests`: no issues found.
- Both Phase 0 notebooks executed successfully.
- OKX public-data smoke path worked for BTC OHLCV/funding, validation, parquet save, and load.
- Small OKX dynamic-universe demo worked on BTC/ETH/SOL/XRP/DOGE with Top 3 selection.

Phase 1 verification (this session ran on Windows 11, no conda available):

- Verified through a gitignored `.venv` at the repo root, created with
  `python -m venv .venv --system-site-packages` (reuses system pandas/numpy/scipy) plus
  `pip install loguru pytest ruff mypy pyarrow`. Run e.g.
  `& .\.venv\Scripts\python.exe -m pytest -q`.
- `pytest`: 50 passed. `ruff check`: passed. `mypy features`: no issues found.
- Both Phase 1 demos run clean to completion; perf acceptance (single-day all-factor
  compute) measured ~0.03 ms, well under the 5 s bar.

Phase 2 verification (also on Windows via the same gitignored `.venv`):

- `pytest`: 89 passed. `ruff check factors/ tests/`: passed. `mypy factors features`:
  no issues found (12 source files).
- `notebooks/demo_phase2_factor_analysis.py` runs clean and exports
  `reports/factor_analysis_demo.html`.

Phase 3 verification (Windows, same `.venv` + `pip install hmmlearn`):

- `pytest`: 107 passed. `ruff check regime/ tests/`: passed. `mypy regime factors
  features`: no issues found (16 source files).
- `notebooks/demo_phase3_regime.py` runs clean (hmmlearn convergence logs silenced).

Phase 4 verification (Windows, same `.venv`):

- `pytest`: 136 passed. `ruff check strategy/ tests/`: passed. `mypy strategy regime
  factors features`: no issues found (21 source files).
- `notebooks/demo_phase4_strategy.py` runs clean; risk check passes (gross ≤ 2x held).

Phase 5 verification (Linux/Miniconda env `/home/pc/miniconda3/envs/xsec-crypto-quant`):

- `pytest`: 154 passed. `ruff check .`: passed. `mypy backtest strategy regime factors
  features tests`: no issues found (52 source files).
- `notebooks/demo_phase5_backtest.py` runs clean. It uses synthetic data only, keeps the
  holdout locked by default, records 12 parameter trials for DSR, and prints bootstrap
  Sharpe confidence intervals plus walk-forward summary.

For future sessions:

```bash
source /home/pc/miniconda3/etc/profile.d/conda.sh
conda activate xsec-crypto-quant
python -m pytest -q
python -m ruff check .
python -m mypy data tests
```

Do not use the bare system `python3` for project verification; it is Python 3.13 and
does not have the project dev dependencies installed. If conda is not on PATH, call
`/home/pc/miniconda3/bin/conda` directly.

## Highest-Priority Rules

These are project invariants, not suggestions:

- Dynamic universe must be timestamp-correct. Never backtest using today's live symbol
  list as if it existed historically.
- Signal timing is close-after-bar; execution is next bar open.
- Include taker fee, slippage, and funding in backtests.
- Funding is both a carry factor candidate and a PnL/cost component.
- Multiple factor trials require FDR correction. Do not report naked p-values as proof.
- Locked holdout may be inspected once only. Tag before unlock, then do not change
  parameters after seeing holdout results.
- Leverage clamps are hard limits: single side <= 1x and gross <= 2x.
- L2/order-book features do not enter historical backtests unless a real historical
  source exists. Current plan deletes them or records them only for future paper trading.

## File Map

- `CLAUDE.md`: full project charter, phase plan, and original Claude instructions.
- `README.md`: high-level project description and setup.
- `docs/data_availability_audit.md`: authoritative note on OKX switch and feature availability.
- `data/universe.py`: pure dynamic-universe logic plus OKX-backed helpers.
- `data/fetcher.py`: OKX OHLCV and funding history pagination.
- `data/storage.py`: local parquet sharding and idempotent merge.
- `data/validate.py`: continuity, OHLCV, outlier, and universe consistency checks.
- `notebooks/01_data_fetch_storage_validate.ipynb`: OKX fetch/storage/validate demo.
- `notebooks/02_dynamic_universe_evolution.ipynb`: recent dynamic-universe demo.
- `tests/test_universe.py`: offline dynamic-universe tests.
- `tests/test_data.py`: offline storage and validation tests.
- `features/preprocess.py`: cross-sectional rank normalization (decision 5/11).
- `features/cross_sectional.py`: momentum / realized-vol / volume-change raw factors.
- `features/carry.py`: funding carry factor (8h→daily aggregation + rolling mean).
- `features/orderflow.py`: CVD / Trade Delta, confirmation-only, not for historical backtest.
- `tests/test_features.py` / `test_cross_sectional.py` / `test_carry.py` / `test_orderflow.py`: Phase 1 offline tests.
- `notebooks/demo_phase1_cross_sectional.py` / `demo_phase1_all_factors.py`: Phase 1 teaching demos.
- `factors/ic_analysis.py`: forward returns + cross-sectional IC/ICIR/t-stat.
- `factors/multiple_testing.py`: Benjamini-Hochberg FDR (q parameterized, default 0.10).
- `factors/quantile_backtest.py`: Q1→Q5 layered backtest + monotonicity.
- `factors/correlation.py`: factor correlation matrix + low-correlation greedy selection.
- `factors/selection.py`: `analyze_factors` end-to-end Phase 2 orchestration → `FactorAnalysis`.
- `factors/report.py`: self-contained HTML factor-analysis report (no external deps).
- `tests/test_ic_analysis.py` / `test_multiple_testing.py` / `test_quantile_backtest.py` / `test_correlation.py` / `test_selection.py` / `test_report.py`: Phase 2 offline tests.
- `notebooks/demo_phase2_factor_analysis.py`: Phase 2 end-to-end teaching demo + HTML export.
- `regime/market_regime.py`: market features + 3-state HMM (full=research, expanding=honest).
- `regime/correlation_spike.py`: correlation-spike detector (ironclad rule 2).
- `regime/risk_params.py`: regime→leverage multiplier + hard single≤1x/gross≤2x clamps.
- `tests/test_market_regime.py` / `test_correlation_spike.py` / `test_risk_params.py`: Phase 3 offline tests.
- `notebooks/demo_phase3_regime.py`: Phase 3 teaching demo (crisis → auto-deleverage).
- `strategy/signal.py`: multi-factor cross-sectional score (IC-signed, equal-weight).
- `strategy/portfolio.py`: long/short dollar-neutral weights + regime leverage + hard clamps.
- `strategy/rebalance.py`: no-trade band + next-bar-open execution shift + turnover.
- `strategy/risk.py`: portfolio exposure checks (gross/single-side/neutrality).
- `tests/test_signal.py` / `test_portfolio.py` / `test_rebalance.py` / `test_strategy_risk.py`: Phase 4 offline tests.
- `notebooks/demo_phase4_strategy.py`: Phase 4 end-to-end strategy demo.
- `backtest/xsec_runner.py`: cost/funding-aware cross-sectional long/short backtester.
- `backtest/metrics.py`: performance metrics, bootstrap CI, realized beta, turnover/win rate.
- `backtest/walkforward.py`: three-way split with locked-holdout guard + rolling windows.
- `backtest/deflated_sharpe.py`: Deflated Sharpe Ratio approximation for trial-count penalty.
- `backtest/overfitting_check.py`: parameter sensitivity, bootstrap, DSR and walk-forward summaries.
- `tests/test_backtest_runner.py` / `test_backtest_metrics.py` / `test_walkforward.py` /
  `test_deflated_sharpe.py` / `test_overfitting_check.py`: Phase 5 offline tests.
- `notebooks/demo_phase5_backtest.py`: Phase 5 synthetic end-to-end backtest/statistics demo.

## Next Agent Checklist

1. Run `git status --short --branch` and preserve any user changes.
2. Read this file, `README.md`, `CLAUDE.md`, and the specific module under edit.
3. Activate `xsec-crypto-quant` in Miniconda or call it via `conda run`.
4. Run `python -m pytest -q` and `python -m ruff check .` before and after edits.
5. If starting Phase 1, implement one small file/function at a time with focused tests.

## Immediate Next Work

Recommended next step after Phase 5 (Phase 6: paper trading):

1. Run the Phase 5 machinery on real, timestamp-correct OKX data using only in-sample and
   validation first. Freeze final parameters from validation and record trial counts.
2. Before inspecting locked holdout, create tag `pre-holdout-freeze`; then unlock holdout
   once and do not change parameters after seeing it.
3. If the validation + holdout verdict is robust (FDR-corrected factors, cost/funding-
   adjusted Sharpe CI positive, DSR passes, no regime/risk rule breach), start Phase 6
   paper trading in `live/`.
4. Phase 6 should wire vnpy paper trading, order/execution monitoring, funding/cost
   reconciliation, and at least 4 weeks of live-vs-backtest consistency checks.
5. If Phase 5 real-data verdict fails, record the honest negative result and stop rather
   than tuning to force profitability.
6. Carry forward: no early-year unbiased claims until a delisted-contract calendar exists;
   orderflow confirmation-only.

## Handoff Protocol

At the end of each agent session, update this file or leave a concise note elsewhere
that includes:

```text
Handoff:
- Last commit/branch:
- Working tree state:
- What changed:
- Commands run:
- Test results:
- Known blockers:
- Next recommended step:
```

Keep handoffs factual. Do not claim a phase is complete until code, docs, tests, and
the required artifact/tag all agree.

## Current Handoff

- Last commit/branch: Phase 5 closeout implemented locally on `main`; planned tag
  `v0.5-backtest`. Earlier tags: Phase 4 = `v0.4-strategy`, Phase 3 = `v0.3-regime`,
  Phase 2 = `v0.2-factors`, Phase 1 = `v0.1-features`, Phase 0 = `v0.0-data`.
- Working tree changes: Phase 5 backtest modules, their tests, one teaching demo, and this
  handoff update.
- Environment: Linux/Miniconda env `/home/pc/miniconda3/envs/xsec-crypto-quant`.
- What changed: added `backtest/{xsec_runner,metrics,walkforward,deflated_sharpe,
  overfitting_check}.py`, tests, and `notebooks/demo_phase5_backtest.py`.
- Commands run: initial `pytest -q`, initial `ruff check .`, focused Phase 5 tests,
  full `pytest -q`, full `ruff check .`, `mypy backtest strategy regime factors features
  tests`, and executed the Phase 5 demo.
- Test results: `pytest` 154 passed; `ruff` passed; `mypy` no issues (52 source/test files);
  Phase 5 demo clean.
- Pending decisions: real-data parameter choices (window N, quantile width, no-trade band,
  FDR q, corr-spike thresholds) must be chosen out-of-sample from validation/walk-forward.
  Fixed: HMM states = 3; equal-weight factor combination.
- Known blockers: early-history fully unbiased universe still needs a delisted-contract calendar.
- Push status: confirm with the user before pushing `main` + `v0.5-backtest` to origin.
- Next recommended step: run Phase 5 on real timestamp-correct OKX data; if validation and
  locked holdout pass after `pre-holdout-freeze`, start Phase 6 paper trading in `live/`.
