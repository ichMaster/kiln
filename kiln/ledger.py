"""
kiln — the usage ledger (`.kiln/usage-ledger.jsonl`): one append-only JSON line per closed
session (Lumi-style), the raw record the generated usage report (`report.py`, KILN-029) reads.

Each line: `{session_id, model, started_at, ended_at, turns, input, output, cache_read,
cache_write, cache_ttl, cost_usd}`. `cost_usd` sums the CLI's actual cost for `claude -p` turns
and the price-table estimate for SDK/chat turns. Append-only; `ensure_ascii=False`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import USAGE_LEDGER


def append_session(entry: dict, path: Path = USAGE_LEDGER) -> None:
    """Append exactly one JSON line for a closed session (creates `.kiln/` if needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_ledger(path: Path = USAGE_LEDGER) -> list[dict]:
    """Parse the ledger into a list of session dicts; skip blank/malformed lines. Missing → []."""
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate a partially-written / corrupt line
        if isinstance(rec, dict):
            out.append(rec)
    return out
