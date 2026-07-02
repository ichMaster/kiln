"""
KILN-059: multi-agent host — register Pashu beside Agnika + per-agent permission scope.

Integration (MockBrain): the host runs two agents concurrently, each on its own thread with its own
AgentPaths + AgentConfig (agnika on the globals, pashu its own; each writes only under its root) and
its own permission scope (agnika broad, pashu narrow). `boot_configured` starts every SERVER_AGENTS
id; `GET /agents` surfaces the scope. No paid calls.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import kiln.config as config  # noqa: E402
import kiln.engine as eng  # noqa: E402
from kiln.brain import MockBrain  # noqa: E402
from kiln.config import AgentPaths  # noqa: E402
from server.host import AgentHost  # noqa: E402


def _stub_model(monkeypatch) -> None:
    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")


def _tmp_paths(root) -> AgentPaths:
    s, k = root / "state", root / "kiln"
    return AgentPaths(
        state_dir=s,
        needs_file=k / "needs.json",
        store_file=k / "store.json",
        usage_ledger=k / "usage-ledger.jsonl",
        usage_report=k / "usage-report.md",
        canon_file=s / "canon.md",
        prompts_file=s / "prompts.md",
    )


def test_two_agents_run_concurrently_with_isolated_state(monkeypatch, tmp_path):
    _stub_model(monkeypatch)
    host = AgentHost()
    a = host.start(
        "agnika", brain=MockBrain(), ticks=3, live=False, paths=_tmp_paths(tmp_path / "a")
    )
    p = host.start(
        "pashu", brain=MockBrain(), ticks=3, live=False, paths=_tmp_paths(tmp_path / "p")
    )
    a.join(5)
    p.join(5)
    assert host.agents() == ["agnika", "pashu"]
    # both ticked server-side with no client (cached status present)
    assert a.latest_status() is not None and p.latest_status() is not None
    # per-agent permission scope (set, not enforced until 1.7)
    assert a.scope == "broad" and p.scope == "narrow"
    # per-agent config: agnika on the module globals (None → monkeypatch-safe); pashu its own
    assert a._config is None
    assert p._config is not None and p._config.agent_id == "pashu"
    # isolated state: each wrote ONLY under its own root
    assert (tmp_path / "a" / "kiln" / "needs.json").exists()
    assert (tmp_path / "p" / "kiln" / "needs.json").exists()


def test_boot_configured_starts_each_configured_agent(monkeypatch):
    host = AgentHost()
    started: list[str] = []
    monkeypatch.setattr(host, "start", lambda aid, **kw: started.append(aid) or aid)
    host.boot_configured(["agnika", "pashu"], live=False)
    assert started == ["agnika", "pashu"]


def test_server_agents_lists_both_from_yaml():
    assert "agnika" in config.SERVER_AGENTS and "pashu" in config.SERVER_AGENTS


def test_agents_endpoint_includes_scope(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    import server.app as appmod

    _stub_model(monkeypatch)
    host = AgentHost()
    monkeypatch.setattr(appmod, "host", host)
    host.start("agnika", brain=MockBrain(), ticks=1, live=False, paths=_tmp_paths(tmp_path / "a"))
    host.start("pashu", brain=MockBrain(), ticks=1, live=False, paths=_tmp_paths(tmp_path / "p"))
    with TestClient(appmod.app) as client:
        agents = {x["agent_id"]: x for x in client.get("/agents").json()}
    assert agents["agnika"]["scope"] == "broad"
    assert agents["pashu"]["scope"] == "narrow"
