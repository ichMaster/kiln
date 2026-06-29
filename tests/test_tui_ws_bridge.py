"""
KILN-054: remote TUI client — `WsBridge` (the TUI over a WS server, no local engine).

- Unit: inbound server events map 1:1 to the `kind`s `_render`/`_update_status` already handle; the
  `snapshot` expands to scrollback; `submit` formats `user.message` vs `command`. (No client agent
  logic.)
- Render reuse: a `KilnApp` fed a `WsBridge` (pre-filled outbox) renders the reply via the
  **unchanged** `_render` and updates the status panel.
- Integration: a `WsBridge` against a **real** server (uvicorn, ephemeral port, MockBrain) holds a
  full turn — the typed line goes out as `user.message`, the reply comes back.

Zero paid calls — MockBrain + stubbed model helpers + tmp `AgentPaths`.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time

import pytest

from server.protocol import encode, snapshot_event
from tui.ws_bridge import WsBridge

_SNAP = {
    "status": "responding",
    "model": "claude-opus-4-8",
    "branch": "think",
    "tick": 42,
    "needs": {"novelty": 0.9},
    "thresholds": {"novelty": 0.85},
    "actions": {"novelty": "deep"},
    "hottest": ["novelty", 0.9],
    "cooldowns": {},
    "stats": {
        "turns": 1,
        "tokens_total": 50,
        "tokens_by_branch": {"think": 50},
        "last_tokens": 50,
        "last_latency": 1.2,
        "avg_latency": 1.2,
    },
}


# --- unit: event mapping -----------------------------------------------------


def test_ingest_maps_events_to_render_kinds():
    br = WsBridge("ws://unused")  # not started — exercise the pure mapping
    br._ingest(encode({"kind": "agent", "text": "вітаю", "is_self": False, "model": "haiku"}))
    br._ingest(encode({"kind": "usage", "usage": {"model": "m", "total": 3}, "latency": 0.1}))
    br._ingest(encode({"kind": "notice", "text": "[exit] saved"}))
    br._ingest(encode({"kind": "status", "snapshot": _SNAP}))

    events = br.drain_output()
    assert [e["kind"] for e in events] == ["agent", "usage", "notice", "status"]
    # the kinds the local Bridge + _render/_update_status already handle (no new client logic)
    assert {e["kind"] for e in events} <= {"agent", "usage", "notice", "status", "user"}
    assert events[0]["model"] == "haiku"  # reply flags pass through untouched


def test_snapshot_expands_to_status_then_turns():
    br = WsBridge("ws://unused")
    snap = snapshot_event(
        _SNAP,
        [{"role": "user", "text": "привіт"}, {"role": "assistant", "text": "вітаю"}],
    )
    br._ingest(encode(snap))
    out = br.drain_output()
    assert out[0] == {"kind": "status", "snapshot": _SNAP}  # panel state first
    assert out[1] == {"kind": "user", "text": "привіт"}  # user turn → user kind
    assert out[2] == {"kind": "agent", "text": "вітаю"}  # assistant turn → agent kind


def test_submit_formats_message_vs_command():
    br = WsBridge("ws://unused")
    sent: list[str] = []
    br._ws = type("_FakeWs", (), {"send": lambda self, m: sent.append(m)})()
    br.submit("привіт")
    br.submit("/status")
    assert json.loads(sent[0]) == {"type": "user.message", "text": "привіт"}
    assert json.loads(sent[1]) == {"type": "command", "text": "/status"}


# --- render reuse: WsBridge events drive the unchanged _render + panel -------


def test_kilnapp_renders_ws_bridge_events(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import RichLog, Static

    from tui.app import KilnApp

    async def scenario():
        br = WsBridge("ws://unused")  # NOT started: no real connection, just a pre-filled outbox
        br._ingest(encode({"kind": "agent", "text": "вітаю", "is_self": False, "model": "haiku"}))
        br._ingest(encode({"kind": "status", "snapshot": _SNAP}))

        app = KilnApp(bridge=br, live=False, start_engine=False)  # local mode, injected WsBridge
        async with app.run_test() as pilot:
            app._drain()  # deterministic drain (instead of waiting for the 0.1s timer)
            await pilot.pause()
            assert app._last_reply == "вітаю"  # the reply went through the unchanged _render
            assert app.query_one(RichLog).lines  # something was written to the log
            status = str(app.query_one("#status", Static).render())
            assert "responding" in status  # the WsBridge status event updated the panel

    asyncio.run(scenario())


# --- integration: a real WS turn --------------------------------------------


def _tmp_paths(tmp_path):
    from kiln.config import AgentPaths

    s = tmp_path / "state"
    k = tmp_path / "kiln"
    return AgentPaths(
        state_dir=s,
        store_file=k / "store.json",
        usage_ledger=k / "usage-ledger.jsonl",
        usage_report=k / "usage-report.md",
        canon_file=s / "canon.md",
        prompts_file=s / "prompts.md",
    )


@contextlib.contextmanager
def _serve(app):
    """Run `app` under uvicorn on an ephemeral port in a thread; yield the port."""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(250):
            if server.started and server.servers:
                break
            time.sleep(0.02)
        else:
            raise RuntimeError("uvicorn did not start")
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _collect_kinds(br, want, timeout=5.0):
    deadline = time.monotonic() + timeout
    seen: set[str] = set()
    while time.monotonic() < deadline and not want <= seen:
        for event in br.drain_output():
            seen.add(event["kind"])
        time.sleep(0.02)
    return seen


def test_ws_bridge_holds_a_turn_over_real_ws(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")

    import kiln.engine as eng
    import server.app as appmod
    from kiln.brain import MockBrain

    monkeypatch.setattr(eng, "summarize", lambda *a, **k: "")
    monkeypatch.setattr(eng, "extract_facts", lambda *a, **k: [])
    monkeypatch.setattr(eng, "digest_facts", lambda *a, **k: "")
    monkeypatch.setattr(eng, "TICK_SECONDS", 0.01)  # fast ticks

    paths = _tmp_paths(tmp_path)
    appmod.host.start("agnika", brain=MockBrain(), ticks=None, live=True, paths=paths)
    try:
        with _serve(appmod.app) as port:
            br = WsBridge(f"ws://127.0.0.1:{port}/agent/agnika").start()
            assert br.wait_connected(5.0)
            assert "status" in _collect_kinds(br, {"status"})  # snapshot/stream arriving
            br.submit("привіт")  # → user.message over the wire
            seen = _collect_kinds(br, {"agent", "usage"})
            assert "agent" in seen and "usage" in seen  # the reply round-tripped
            br.close()
    finally:
        appmod.host.stop_all()
