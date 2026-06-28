"""Unit: usage report generation from a fixture ledger (KILN-029). Fixtures only, no paid calls."""

from __future__ import annotations

from kiln import report


def _e(sid, started, model="claude-opus-4-8", turns=1, inp=100, out=20, cr=0, cw=0, cost=0.5):
    return {
        "session_id": sid,
        "model": model,
        "started_at": started,
        "ended_at": started,
        "turns": turns,
        "input": inp,
        "output": out,
        "cache_read": cr,
        "cache_write": cw,
        "cache_ttl": "5m",
        "cost_usd": cost,
    }


LEDGER = [
    _e("a", "2026-06-28T10:00:00", inp=1000, out=100, cr=500, cw=50, cost=1.0),
    _e("b", "2026-06-28T12:00:00", inp=2000, out=200, cost=2.0),
    _e("c", "2026-06-21T09:00:00", inp=3000, out=300, cost=3.0),  # earlier week
    _e("d", "2026-05-30T09:00:00", inp=4000, out=400, cost=4.0),  # earlier month
]


def test_overall_totals():
    md = report.generate(LEDGER)
    assert "4 sessions logged" in md and "## Overall" in md
    assert "input 10,000" in md  # 1000+2000+3000+4000
    assert "$10.00" in md  # total cost 1+2+3+4
    assert "**Sessions:** 4" in md


def test_by_day_week_month_aggregation():
    md = report.generate(LEDGER)
    assert "## By month" in md and "2026-06" in md and "2026-05" in md
    assert "## By week (ISO)" in md and "2026-W" in md
    day_section = md.split("## By day")[1]
    assert "| 2026-06-28 | 2 |" in day_section  # the two 06-28 sessions group into one day row


def test_cost_breakdown_and_cache_savings_note():
    md = report.generate(LEDGER)
    assert "## Cost breakdown" in md and "cache read" in md and "Caching saved" in md


def test_recent_sessions_caps_at_50_newest_first():
    big = [_e(f"s{i}", f"2026-06-{(i % 28) + 1:02d}T0{i % 9}:00:00") for i in range(60)]
    section = report.generate(big).split("## Recent sessions")[1]
    headers = [ln for ln in section.splitlines() if ln.startswith("### ")]
    assert len(headers) == 50  # capped at 50 per-session sub-sections


def test_by_model_section_and_claude_p_count():
    ledger = [
        {
            **_e("a", "2026-06-28T10:00:00", cost=1.0),
            "cli_calls": 2,
            "by_model": {
                "claude-opus-4-8": {
                    "calls": 2,
                    "input": 100,
                    "output": 20,
                    "cache_read": 5,
                    "cache_write": 1,
                    "cost_usd": 0.9,
                },  # fmt: skip
                "claude-haiku-4-5": {
                    "calls": 1,
                    "input": 10,
                    "output": 5,
                    "cache_read": 0,
                    "cache_write": 0,
                    "cost_usd": 0.1,
                },  # fmt: skip
            },
        }
    ]
    md = report.generate(ledger)
    assert "## By model" in md and "opus" in md and "haiku" in md  # per-model rollup
    assert "calls:** 2" in md  # the overall claude -p execution count (Overall line)
    assert "### 2026-06-28 10:00" in md and "claude -p ×2" in md  # per-session detail header


def test_empty_ledger_is_valid_minimal_report():
    md = report.generate([])
    assert "0 sessions logged" in md and "## Overall" in md  # no crash


def test_write_report_writes_file(tmp_path):
    p = tmp_path / "usage-report.md"
    md = report.write_report(LEDGER, p)
    assert p.read_text(encoding="utf-8") == md and "## Overall" in md
