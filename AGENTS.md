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
- Not done: Phase 2+ implementation. `factors/`, `regime/`, `strategy/`, `backtest/`,
  and `live/` currently contain only `__init__.py`.

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

## Next Agent Checklist

1. Run `git status --short --branch` and preserve any user changes.
2. Read this file, `README.md`, `CLAUDE.md`, and the specific module under edit.
3. Activate `xsec-crypto-quant` in Miniconda or call it via `conda run`.
4. Run `python -m pytest -q` and `python -m ruff check .` before and after edits.
5. If starting Phase 1, implement one small file/function at a time with focused tests.

## Immediate Next Work

Recommended next step after Phase 1 (Phase 2: cross-sectional factor analysis):

1. Begin Phase 2 in `factors/`: cross-sectional IC/ICIR via alphalens-reloaded against
   `forward_return` at 1D/3D/5D/10D horizons. Factors consume `features/` raw outputs
   ranked via `features.preprocess.cross_sectional_rank`.
2. Add `factors/multiple_testing.py` (Benjamini-Hochberg FDR) BEFORE claiming any factor
   is significant — N factors × 4 horizons will produce false positives uncorrected.
3. Add `factors/quantile_backtest.py` (5-layer Q1→Q5 monotonicity) and
   `factors/correlation.py` (drop >0.7-correlated, keep higher ICIR; final pool ≤ 10).
4. Window N stays parameterized (`CANDIDATE_WINDOWS = 7/14/30/60/90`). Do NOT pick the
   best N on full data — that is deferred to Phase 5 walk-forward.
5. orderflow CVD/delta is confirmation-only; do not feed it into historical IC claims.
6. Do not claim early-year unbiased results until a delisted-contract calendar is added.

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

- Last commit/branch: Phase 1 closeout merged into `main`, tag `v0.1-features`; work was
  done on branch `phase-1-features`.
- Working tree changes: Phase 1 feature modules, their tests, two teaching demos, and this
  handoff update.
- Environment: this session ran on Windows 11 with no conda. Verified via a gitignored
  `.venv` (`--system-site-packages` + loguru/pytest/ruff/mypy/pyarrow). The canonical
  Linux/Miniconda path above still applies on the original dev machine.
- What changed: added `features/{preprocess,cross_sectional,carry,orderflow}.py` plus
  tests and `notebooks/demo_phase1_{cross_sectional,all_factors}.py`.
- Commands run: `pytest -q`, `ruff check`, `mypy features`, and executed both demos.
- Test results: `pytest` 50 passed; `ruff` passed; `mypy features` no issues; demos clean.
- Known blockers: early-history fully unbiased universe still needs a delisted-contract calendar.
- Not pushed: merge + tag are local only; `origin` not updated this session (push on request).
- Next recommended step: start Phase 2 factor analysis (IC/ICIR + FDR) in `factors/`.
