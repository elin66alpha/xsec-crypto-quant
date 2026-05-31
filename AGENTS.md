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
- Not done: Phase 1+ implementation. `features/`, `factors/`, `regime/`,
  `strategy/`, `backtest/`, and `live/` currently contain only `__init__.py`.

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

## Next Agent Checklist

1. Run `git status --short --branch` and preserve any user changes.
2. Read this file, `README.md`, `CLAUDE.md`, and the specific module under edit.
3. Activate `xsec-crypto-quant` in Miniconda or call it via `conda run`.
4. Run `python -m pytest -q` and `python -m ruff check .` before and after edits.
5. If starting Phase 1, implement one small file/function at a time with focused tests.

## Immediate Next Work

Recommended next step after Phase 0:

1. Begin Phase 1 with `features/preprocess.py` and rank normalization tests before
   adding factor-specific modules.
2. Then add `features/cross_sectional.py` for momentum, volatility, and volume-change factors.
3. Keep `features/carry.py` separate because funding is both a factor and a PnL component.
4. Do not claim early-year unbiased results until a delisted-contract calendar is added.

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

- Last commit/branch before closeout: `ba760f4` on `main` and `phase-0-data`.
- Working tree changes: Phase 0 closeout docs, notebooks, lint fixes, and this handoff update.
- Dependency work: upgraded Miniconda env `xsec-crypto-quant` to Python 3.12.13 and installed `.[dev]`.
- Commands run: dependency resolution, OKX smoke fetch, `pytest`, `ruff`, `mypy`, and executed both notebooks.
- Test results: `pytest` 15 passed; `ruff` all checks passed; `mypy data tests` no issues; notebooks executed successfully.
- Known blockers: early-history fully unbiased universe still needs a delisted-contract calendar.
- Next recommended step: start Phase 1 feature preprocessing and cross-sectional factor tests.
