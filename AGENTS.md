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
- Not done: Phase 3+ implementation. `regime/`, `strategy/`, `backtest/`, and `live/`
  currently contain only `__init__.py`.

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

## Next Agent Checklist

1. Run `git status --short --branch` and preserve any user changes.
2. Read this file, `README.md`, `CLAUDE.md`, and the specific module under edit.
3. Activate `xsec-crypto-quant` in Miniconda or call it via `conda run`.
4. Run `python -m pytest -q` and `python -m ruff check .` before and after edits.
5. If starting Phase 1, implement one small file/function at a time with focused tests.

## Immediate Next Work

Recommended next step after Phase 2 (Phase 3: market-level regime analysis):

1. Begin Phase 3 in `regime/`: `market_regime.py` — 3-state HMM (low-vol / trend /
   high-vol crisis) on market-level inputs (total-market realized vol, cross-sectional
   correlation median, BTC dominance change, mean abs pool return). Expanding-window
   refit only — NEVER fit on the full series then reuse states (look-ahead).
2. `regime/correlation_spike.py` — detect cross-sectional correlation spikes → trigger
   gross-leverage cut / flat (the correlation-crisis ironclad rule).
3. `regime/risk_params.py` — REGIME_RISK_PARAMS leverage multipliers; result must still
   respect the single-side ≤1x / gross ≤2x hard limits.
4. The number of HMM states is a statistical parameter — ask the user before fixing it.
5. Carry forward: window N parameterized (deferred to Phase 5 walk-forward); FDR q still
   a pending decision; orderflow confirmation-only; no early-year unbiased claims until a
   delisted-contract calendar exists.

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

- Last commit/branch: Phase 2 closeout merged into `main`, tag `v0.2-factors`; work was
  done on branch `phase-2-factors`. (Phase 1 = `v0.1-features`, Phase 0 = `v0.0-data`.)
- Working tree changes: Phase 2 factor-analysis modules, their tests, one teaching demo,
  and this handoff update.
- Environment: this session ran on Windows 11 with no conda. Verified via a gitignored
  `.venv` (`--system-site-packages` + loguru/pytest/ruff/mypy/pyarrow). The canonical
  Linux/Miniconda path above still applies on the original dev machine.
- What changed: added `factors/{ic_analysis,multiple_testing,quantile_backtest,
  correlation,selection,report}.py` plus tests and
  `notebooks/demo_phase2_factor_analysis.py`.
- Commands run: `pytest -q`, `ruff check factors/ tests/`, `mypy factors features`,
  and executed the Phase 2 demo (exports `reports/factor_analysis_demo.html`).
- Test results: `pytest` 89 passed; `ruff` passed; `mypy factors features` no issues; demo clean.
- Pending decisions: FDR q (default 0.10, finalize after seeing real p-values); HMM state
  count (ask before Phase 3); window N (deferred to Phase 5 walk-forward).
- Known blockers: early-history fully unbiased universe still needs a delisted-contract calendar.
- Push status: confirm with the user before pushing `main` + `v0.2-factors` to origin.
- Next recommended step: start Phase 3 market-level regime analysis in `regime/`.
