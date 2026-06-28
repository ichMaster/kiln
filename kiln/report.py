"""
kiln — the generated usage report (`.kiln/usage-report.md`), regenerated from the ledger (KILN-029).

Pure aggregation of the ledger lines into Markdown, matching Lumi's layout: **Overall**, **Cost
breakdown** (cache-aware + a savings note), **By month / ISO week / day**, and **Recent sessions
(last 50)**. Costs are estimates (see `cost.py`); per-session figures use the ledger's `cost_usd`
(actual where the CLI reported it). An empty ledger yields a valid minimal report.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .config import USAGE_REPORT_FILE
from .cost import bucket_costs, cache_savings
from .ledger import read_ledger

_BUCKETS = ("input", "output", "cache_read", "cache_write")
_BUCKET_LABEL = {
    "input": "input",
    "output": "output",
    "cache_read": "cache read",
    "cache_write": "cache write",
}


def _num(n) -> str:
    return f"{int(n or 0):,}"


def _usd(x) -> str:
    x = float(x or 0)
    return f"${x:,.2f}" if abs(x) >= 1 else f"${x:.4f}"


def _row(cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _agg(entries) -> dict:
    """Aggregate the four buckets + cost + sessions + turns + cli_calls over ledger entries."""
    a = {b: 0 for b in _BUCKETS}
    a["sessions"], a["turns"], a["cost"], a["cli_calls"] = len(entries), 0, 0.0, 0
    for e in entries:
        for b in _BUCKETS:
            a[b] += int(e.get(b, 0) or 0)
        a["turns"] += int(e.get("turns", 0) or 0)
        a["cost"] += float(e.get("cost_usd", 0) or 0)
        a["cli_calls"] += int(e.get("cli_calls", 0) or 0)
    a["total"] = a["input"] + a["output"] + a["cache_read"] + a["cache_write"]
    return a


def _entry_models(e) -> dict:
    """A session's per-model breakdown — the `by_model` map, or a single combined row for legacy
    ledger lines (written before per-model tracking)."""
    bm = e.get("by_model")
    if isinstance(bm, dict) and bm:
        return bm
    return {
        e.get("model", "?"): {
            "calls": None,  # legacy line: per-model call counts weren't tracked
            "input": int(e.get("input", 0) or 0),
            "output": int(e.get("output", 0) or 0),
            "cache_read": int(e.get("cache_read", 0) or 0),
            "cache_write": int(e.get("cache_write", 0) or 0),
            "cost_usd": float(e.get("cost_usd", 0) or 0),
        }
    }


def _group(entries, keyfn) -> list[tuple[str, dict]]:
    """Group entries by keyfn(day); return (period, agg) rows newest-first. Bad dates dropped."""
    groups: dict[str, list] = {}
    for e in entries:
        day = (e.get("started_at") or "")[:10]
        try:
            key = keyfn(day)
        except ValueError:
            continue
        groups.setdefault(key, []).append(e)
    rows = [(k, _agg(v)) for k, v in groups.items()]
    rows.sort(key=lambda kv: kv[0], reverse=True)
    return rows


def _iso_week(day: str) -> str:
    y, w, _ = _dt.date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def _short_model(model: str) -> str:
    parts = [
        m.split("-")[1] if m.startswith("claude-") and "-" in m else m for m in model.split("+")
    ]
    return "+".join(p for p in parts if p) or "—"


def _breakdown(ledger, total) -> list[str]:
    bcost = {b: 0.0 for b in _BUCKETS}
    savings = 0.0
    for e in ledger:
        bc = bucket_costs(e, e.get("model", ""))
        for b in _BUCKETS:
            bcost[b] += bc[b]
        savings += cache_savings(e, e.get("model", ""))
    bsum = sum(bcost.values()) or 1.0
    out = ["## Cost breakdown", "", _row(["Bucket", "Tokens", "Rate /1M", "Cost", "Share"]),
           "|---|--:|--:|--:|--:|"]  # fmt: skip
    for b in _BUCKETS:
        tok = total[b]
        rate = (bcost[b] / tok * 1_000_000) if tok else 0.0
        share = bcost[b] / bsum * 100
        out.append(_row([_BUCKET_LABEL[b], _num(tok), _usd(rate), _usd(bcost[b]), f"{share:.0f}%"]))
    out += ["", f"> Caching saved ~{_usd(savings)} net (cache reads vs. the full input rate, "
            "minus cache writes).", ""]  # fmt: skip
    return out


def _period_section(title: str, rows) -> list[str]:
    head = title.split(" ")[1].capitalize()  # "By month" -> "Month"
    cols = [head, "Sessions", "Turns", "Input", "Output", "Cache read", "Cache write",
            "Total tokens", "Est. cost"]  # fmt: skip
    out = [f"## {title}", _row(cols), "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for period, a in rows:
        out.append(_row([period, a["sessions"], _num(a["turns"]), _num(a["input"]),
                         _num(a["output"]), _num(a["cache_read"]), _num(a["cache_write"]),
                         _num(a["total"]), _usd(a["cost"])]))  # fmt: skip
    out.append("")
    return out


def _model_row(label, m) -> str:
    """A per-model stats row: label | calls | input | output | cache r | cache w | total | cost."""
    total = m["input"] + m["output"] + m["cache_read"] + m["cache_write"]
    calls = m.get("calls")
    return _row([
        label, "—" if calls is None else calls, _num(m["input"]), _num(m["output"]),
        _num(m["cache_read"]), _num(m["cache_write"]), _num(total), _usd(m["cost_usd"]),
    ])  # fmt: skip


_MODEL_HEADER = "|---|--:|--:|--:|--:|--:|--:|--:|"
_MODEL_COLS = [
    "Model",
    "Calls",
    "Input",
    "Output",
    "Cache read",
    "Cache write",
    "Total",
    "Est. cost",
]


def _by_model_section(ledger) -> list[str]:
    """Per-model rollup across all sessions (which model spent what)."""
    agg: dict[str, dict] = {}
    sessions: dict[str, int] = {}
    for e in ledger:
        for model, m in _entry_models(e).items():
            cur = agg.setdefault(
                model,
                {
                    "calls": 0,
                    "input": 0,
                    "output": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "cost_usd": 0.0,
                },
            )
            for k in cur:
                cur[k] += m.get(k) or 0  # legacy `calls` may be None -> treat as 0 in the rollup
            sessions[model] = sessions.get(model, 0) + 1
    out = ["## By model", _row(["Model", "Sessions", *_MODEL_COLS[1:]]),
           "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]  # fmt: skip
    for model, m in sorted(agg.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True):
        total = m["input"] + m["output"] + m["cache_read"] + m["cache_write"]
        out.append(_row([
            _short_model(model), sessions[model], m["calls"], _num(m["input"]), _num(m["output"]),
            _num(m["cache_read"]), _num(m["cache_write"]), _num(total), _usd(m["cost_usd"]),
        ]))  # fmt: skip
    out.append("")
    return out


def _recent(ledger) -> list[str]:
    """Recent sessions (last 50), each expanded into a per-model detail table."""
    recent = sorted(ledger, key=lambda e: e.get("started_at", ""), reverse=True)[:50]
    out = ["## Recent sessions (last 50)", ""]
    for e in recent:
        started = (e.get("started_at") or "").replace("T", " ")[:16]
        sid = (e.get("session_id") or "")[:16]
        total = sum(int(e.get(b, 0) or 0) for b in _BUCKETS)
        cli = e.get("cli_calls")
        cli_str = "?" if cli is None else cli  # legacy lines didn't record the count
        out.append(
            f"### {started} · `{sid}` · {e.get('turns', 0)} turns · "
            f"claude -p ×{cli_str} · {_num(total)} tok · {_usd(e.get('cost_usd', 0))}"
        )
        out += [_row(_MODEL_COLS), _MODEL_HEADER]
        for model, m in _entry_models(e).items():
            out.append(_model_row(_short_model(model), m))
        out.append("")
    return out


def generate(ledger: list[dict]) -> str:
    """Render the full usage report Markdown from the ledger entries."""
    t = _agg(ledger)
    lines = ["# kiln — token usage & estimated cost", "", f"_{t['sessions']} sessions logged._", ""]
    lines += ["## Overall", ""]
    lines.append(f"- **Estimated cost:** **{_usd(t['cost'])}**")
    lines.append(
        f"- **Total tokens:** {_num(t['total'])} (input {_num(t['input'])} · "
        f"output {_num(t['output'])} · cache read {_num(t['cache_read'])} · "
        f"cache write {_num(t['cache_write'])})"
    )
    lines.append(f"- **Sessions:** {t['sessions']} · **Turns:** {_num(t['turns'])}")
    lines.append(
        f"- **`claude -p` calls:** {_num(t['cli_calls'])} (deep + tool; chat uses the SDK)"
    )
    lines += ["", "> Costs are **estimates** from list prices (cache read = 10% of input; "
              "cache write = 1.25× @ 5m, 2× @ 1h). Not a billing source of truth.", ""]  # fmt: skip
    lines += _breakdown(ledger, t)
    lines += _by_model_section(ledger)
    lines += _period_section("By month", _group(ledger, lambda d: d[:7]))
    lines += _period_section("By week (ISO)", _group(ledger, _iso_week))
    lines += _period_section("By day", _group(ledger, lambda d: d or ""))
    lines += _recent(ledger)
    return "\n".join(lines) + "\n"


def write_report(ledger: list[dict] | None = None, path: Path = USAGE_REPORT_FILE) -> str:
    """Regenerate the report from the ledger (reads it if not supplied) and write the Markdown."""
    if ledger is None:
        ledger = read_ledger()
    md = generate(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    return md
