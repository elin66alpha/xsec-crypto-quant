"""Trial ledger utilities for statistical-honesty accounting."""

from __future__ import annotations

from datetime import date
from pathlib import Path

LEDGER_HEADER = "| date | batch | n_combos | note |\n|---|---|---:|---|\n"


def _sanitize_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "/").strip()


def _ensure_ledger(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LEDGER_HEADER, encoding="utf-8")


def total_trials(path: str | Path = "docs/trials_ledger.md") -> int:
    """Return cumulative parameter-combination trials recorded in a Markdown ledger."""
    ledger_path = Path(path)
    if not ledger_path.exists():
        return 0

    total = 0
    for raw_line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line or "n_combos" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        try:
            total += int(cells[2])
        except ValueError:
            continue
    return total


def record_trials(
    batch: str,
    n_combos: int,
    note: str,
    path: str | Path = "docs/trials_ledger.md",
) -> int:
    """Append one trial batch to the ledger and return the cumulative total."""
    if n_combos < 0:
        raise ValueError("n_combos must be non-negative")

    ledger_path = Path(path)
    _ensure_ledger(ledger_path)
    row = (
        f"| {date.today().isoformat()} | {_sanitize_cell(batch)} | {int(n_combos)} | "
        f"{_sanitize_cell(note)} |\n"
    )
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(row)
    return total_trials(ledger_path)
