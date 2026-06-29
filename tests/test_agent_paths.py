"""
KILN-051: agent-scoped persistence — `AgentPaths` + `engine.run(paths=…)`.

Unit: `AgentPaths.for_agent` maps the default agent (agnika / unset) to today's flat global paths
and any other id under `state/{id}/` + `.kiln/{id}/`. Contract: a session run with an explicit agent
root writes its state/store/ledger/report THERE and leaves the global store untouched.

Runs against `MockBrain` with the model-calling helpers stubbed — no paid calls.
"""

from __future__ import annotations

import json

import kiln.engine as eng
from kiln import store as kstore
from kiln.brain import MockBrain
from kiln.config import (
    CANON_FILE,
    KILN_DIR,
    PROMPTS_FILE,
    STATE_DIR,
    STORE_FILE,
    USAGE_LEDGER,
    USAGE_REPORT_FILE,
    AgentPaths,
)


class _NullOutput:
    """Minimal Output sink — drops everything (the test asserts on the filesystem, not chrome)."""

    def user(self, text):
        pass

    def agent(self, text, **kw):
        pass

    def usage(self, usage, latency=None):
        pass

    def notice(self, text):
        pass

    def status(self, snapshot):
        pass


def test_default_agent_maps_to_global_paths():
    for agent in (None, "", "agnika"):
        p = AgentPaths.for_agent(agent)
        assert p.state_dir == STATE_DIR
        assert p.store_file == STORE_FILE
        assert p.usage_ledger == USAGE_LEDGER
        assert p.usage_report == USAGE_REPORT_FILE
        assert p.canon_file == CANON_FILE
        assert p.prompts_file == PROMPTS_FILE


def test_named_agent_nests_under_its_id():
    p = AgentPaths.for_agent("pashu")
    assert p.state_dir == STATE_DIR / "pashu"
    assert p.store_file == KILN_DIR / "pashu" / "store.json"
    assert p.usage_ledger == KILN_DIR / "pashu" / "usage-ledger.jsonl"
    assert p.usage_report == KILN_DIR / "pashu" / "usage-report.md"
    assert p.canon_file == STATE_DIR / "pashu" / "canon.md"
    assert p.prompts_file == STATE_DIR / "pashu" / "prompts.md"
    # fully separate from agnika's data
    assert p.store_file != STORE_FILE and p.state_dir != STATE_DIR


def test_run_with_agent_root_writes_only_under_it(monkeypatch, tmp_path):
    """Contract: a session given an agent root persists ONLY there; the global store is intact."""
    # No paid CLI/SDK: stub the summary/fact helpers, keep REAL persistence against the tmp root.
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "ПІДСУМОК")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")

    sdir = tmp_path / "state"
    kdir = tmp_path / "kiln"
    paths = AgentPaths(
        state_dir=sdir,
        store_file=kdir / "store.json",
        usage_ledger=kdir / "usage-ledger.jsonl",
        usage_report=kdir / "usage-report.md",
        canon_file=sdir / "canon.md",
        prompts_file=sdir / "prompts.md",
    )

    global_sessions_before = len(kstore.load_store().get("sessions", []))

    eng.run(
        ticks=2,
        live=False,
        channel=eng.ScriptedChannel({1: "привіт"}),
        brain=MockBrain(),
        output=_NullOutput(),
        paths=paths,
    )

    # everything the session persisted is under the agent root
    assert (sdir / "needs.json").exists()
    assert (kdir / "store.json").exists()
    assert (kdir / "usage-ledger.jsonl").exists()
    assert (kdir / "usage-report.md").exists()
    store = json.loads((kdir / "store.json").read_text(encoding="utf-8"))
    assert store["sessions"] and store["summaries"]  # the closed session + its summary

    # ...and nothing leaked to the global store
    assert len(kstore.load_store().get("sessions", [])) == global_sessions_before
