"""
Contract for the "brain" seam (ARCHITECTURE §Contracts, §Two brains and cost routing).

Each branch returns (text, usage), where usage = {model, input, output, total}.
respond() calls the model ONLY through brain — verified here against a mock, with no
network or subprocess calls (zero paid calls).
"""

from __future__ import annotations

from kiln.brain import Brain, LiveBrain, MockBrain
from kiln.config import CHAT_MODEL, DEEP_MODEL
from kiln.engine import State, respond
from kiln.usage import usage_record

USAGE_KEYS = {"model", "input", "output", "cache_read", "cache_write", "total", "cost_usd"}


# --- usage_record: token normalization (shared by both adapters) -------------


def test_usage_record_from_cli_dict():
    # CLI dict carries cache fields + the actual cost (total_cost_usd passed as cost_usd).
    rec = usage_record(
        DEEP_MODEL,
        {
            "input_tokens": 3,
            "output_tokens": 4,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
        },
        cost_usd=0.42,
    )
    assert rec == {
        "model": DEEP_MODEL,
        "input": 3,
        "output": 4,
        "cache_read": 100,
        "cache_write": 20,
        "total": 7,  # input + output only (cache tracked separately)
        "cost_usd": 0.42,
    }


def test_usage_record_from_sdk_object():
    class _U:  # mimics msg.usage from the SDK
        input_tokens = 10
        output_tokens = 5
        cache_read_input_tokens = 30
        cache_creation_input_tokens = 0

    rec = usage_record(CHAT_MODEL, _U())  # SDK path -> cost_usd None (estimated later)
    assert rec == {
        "model": CHAT_MODEL,
        "input": 10,
        "output": 5,
        "cache_read": 30,
        "cache_write": 0,
        "total": 15,
        "cost_usd": None,
    }


def test_usage_record_none_is_zeroed():
    assert usage_record(CHAT_MODEL, None) == {
        "model": CHAT_MODEL,
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "total": 0,
        "cost_usd": None,
    }


# --- Brain seam: the (text, usage) shape ------------------------------------


def test_mockbrain_chat_contract():
    text, usage = MockBrain().chat([{"role": "user", "text": "привіт"}], "sys")
    assert isinstance(text, str) and text
    assert set(usage) == USAGE_KEYS
    assert usage["total"] == usage["input"] + usage["output"]


def test_mockbrain_tool_contract():
    text, usage = MockBrain().tool("session-wiki", [{"role": "user", "text": "привіт"}], "sys")
    assert isinstance(text, str) and text
    assert set(usage) == USAGE_KEYS
    assert usage["total"] == usage["input"] + usage["output"]


def test_both_brains_satisfy_protocol():
    # runtime_checkable Protocol: structural conformance to the seam.
    assert isinstance(MockBrain(), Brain)
    assert isinstance(LiveBrain(), Brain)


# --- respond() through a mock: routing + 0 IO -------------------------------


class RecordingBrain:
    """Mock that records which branch was called; no IO."""

    def __init__(self):
        self.calls: list = []

    def chat(self, history, system):
        self.calls.append("chat")
        return "CHAT-REPLY", usage_record(CHAT_MODEL, {"input_tokens": 1, "output_tokens": 1})

    def tool(self, agent, history, system):
        self.calls.append(("tool", agent))
        return "TOOL-REPLY", usage_record("sonnet", {"input_tokens": 3, "output_tokens": 3})


def test_respond_chat_routes_to_brain_chat():
    brain = RecordingBrain()
    out = respond("привіт", State(needs={"intensity": 0.0, "connection": 0.0}), [], "sys", brain)
    assert brain.calls == ["chat"]
    assert out["class"] == "chat"
    assert out["reply"] == "CHAT-REPLY"
    assert set(out["usage"]) == USAGE_KEYS


def test_respond_think_routes_to_deep_subagent():
    brain = RecordingBrain()
    # Prompt carries THINK_HINTS markers -> the `deep` sub-agent (v1.4), tool-less.
    out = respond(
        "поясни, чому так", State(needs={"intensity": 0.0, "connection": 0.0}), [], "sys", brain
    )
    assert brain.calls == [("tool", "deep")]
    assert out["reply"] == "TOOL-REPLY"


def test_respond_force_deep_routes_to_deep_subagent():
    brain = RecordingBrain()
    out = respond("будь-що", State(needs={}), [], "sys", brain, force="deep")
    assert brain.calls == [("tool", "deep")]
    assert out["class"] == "deep"


def test_respond_force_tool_routes_to_named_agent():
    brain = RecordingBrain()
    out = respond(
        "розкажи щось нове",
        State(needs={"novelty": 0.9}),
        [],
        "sys",
        brain,
        force="tool",
        agent="session-wiki",
    )
    assert brain.calls == [("tool", "session-wiki")]
    assert out["class"] == "tool"
    assert out["reply"] == "TOOL-REPLY"
    assert out["route"] == "TOOL/session-wiki"


def test_respond_with_mock_never_touches_subprocess(monkeypatch):
    """With a mock brain, respond never touches subprocess (zero paid calls)."""
    import kiln.brain as brainmod

    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not be called with MockBrain")

    monkeypatch.setattr(brainmod.subprocess, "run", _boom)
    respond("поясни, чому так", State(needs={}), [], "sys", MockBrain(), force="deep")


# --- claude -p prompt delivery: stdin, NOT a positional arg ------------------
# Regression guard: --allowedTools is variadic (<tools...>) and would swallow a trailing
# positional prompt, so deep()/tool() must hand the prompt to claude via stdin.


class _FakeProc:
    returncode = 0
    stdout = '{"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}'
    stderr = ""


def _capture_run(monkeypatch):
    seen = {}
    import kiln.brain as brainmod

    def _run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        seen["env"] = kwargs.get("env")
        seen["cwd"] = kwargs.get("cwd")
        return _FakeProc()

    monkeypatch.setattr(brainmod.subprocess, "run", _run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-be-stripped")
    return seen


def test_livebrain_deep_is_the_tool_less_deep_subagent(monkeypatch):
    """v1.4: reasoning is the `deep` sub-agent through the builder — --agent deep, tool-less
    (deep.md declares no tools → no --allowedTools), prompt on stdin, Opus never on the API key."""
    seen = _capture_run(monkeypatch)
    LiveBrain().tool("deep", [{"role": "user", "text": "поясни"}], "sys")
    assert "--agent" in seen["cmd"] and "deep" in seen["cmd"]
    assert "--allowedTools" not in seen["cmd"]  # deep is tool-less
    assert seen["input"] and seen["input"] not in seen["cmd"]  # prompt on stdin
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # Opus never bills via the API key
    assert "MAX_THINKING_TOKENS" in seen["env"]  # extended thinking ON


def test_livebrain_tool_built_by_the_security_builder(monkeypatch):
    """A sub-agent call goes through the builder: --agent, prompt on stdin, workspace cwd, the
    isolation flags, and no API key in the minimal env allowlist."""
    seen = _capture_run(monkeypatch)
    LiveBrain().tool("session-wiki", [{"role": "user", "text": "привіт"}], "sys")
    assert seen["input"] and "привіт" in seen["input"]  # transcript+prompt on stdin
    assert "--agent" in seen["cmd"] and "session-wiki" in seen["cmd"]
    assert seen["input"] not in seen["cmd"]  # the prompt is not a positional arg
    # v1.4 isolation flags from the builder:
    assert "--strict-mcp-config" in seen["cmd"]  # operator MCP servers ignored
    assert (
        "--setting-sources" in seen["cmd"] and "--settings" in seen["cmd"]
    )  # operator settings out
    assert seen["cwd"] and seen["cwd"].endswith("workspace")  # runs in the agent workspace
    assert "ANTHROPIC_API_KEY" not in seen["env"]  # claude -p uses its own login, not the key
    assert "MAX_THINKING_TOKENS" in seen["env"]  # extended thinking ON
    assert (
        "PATH" in seen["env"] and "HOME" in seen["env"]
    )  # minimal allowlist keeps the login working


def test_livebrain_tool_refuses_agent_outside_profile():
    """A sub-agent not in the profile's `agents:` list is refused before any spawn (fail closed)."""
    text, usage = LiveBrain().tool("rm-rf-everything", [{"role": "user", "text": "hi"}], "sys")
    assert usage is None
    assert "rm-rf-everything" in text and "error" in text.lower()


def test_tools_class_routes_to_hands(monkeypatch):
    """The 'tools' class no longer arms the deep prompt — respond() fires the `hands` sub-agent."""
    calls = {}

    class _Spy(MockBrain):
        def tool(self, agent, history, system):
            calls["agent"] = agent
            return super().tool(agent, history, system)

    out = respond("збережи це у файл", State(needs={}), [], "sys", _Spy(), force="tools")
    assert calls.get("agent") == "hands"
    assert out["route"] == "TOOLS/hands"


def _livebrain_in(tmp_path, monkeypatch):
    """A LiveBrain whose per-agent paths live under tmp (isolates the audit/workspace writes)."""
    from kiln.config import AgentPaths

    k = tmp_path / "kiln"
    s = tmp_path / "state"
    paths = AgentPaths(
        state_dir=s,
        needs_file=k / "needs.json",
        store_file=k / "store.json",
        usage_ledger=k / "usage-ledger.jsonl",
        usage_report=k / "usage-report.md",
        canon_file=s / "canon.md",
        prompts_file=s / "prompts.md",
    )
    return LiveBrain(paths=paths, agent_id="agnika"), k / "claude-audit.jsonl"


def test_audit_line_written_per_spawn(tmp_path, monkeypatch):
    """KILN-072: a spawn appends one audit line (ts/agent_id/sub_agent/argv/cwd/exit/usage)."""
    import json as _json

    seen = _capture_run(monkeypatch)  # mocks subprocess.run → returncode 0
    brain, audit = _livebrain_in(tmp_path, monkeypatch)
    brain.tool("session-wiki", [{"role": "user", "text": "привіт"}], "sys")
    assert seen["cmd"][2] == "--agent"
    lines = [_json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    rec = lines[0]
    assert set(rec) >= {"ts", "agent_id", "sub_agent", "argv", "cwd", "exit", "duration_s", "usage"}
    assert rec["sub_agent"] == "session-wiki" and rec["exit"] == 0


def test_audit_line_for_refused_agent(tmp_path, monkeypatch):
    """A sub-agent outside the profile is refused before any spawn — and audited as refused."""
    import json as _json

    brain, audit = _livebrain_in(tmp_path, monkeypatch)
    text, usage = brain.tool("rm-rf", [{"role": "user", "text": "hi"}], "sys")
    assert usage is None and "rm-rf" in text
    rec = _json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert rec["sub_agent"] == "rm-rf" and rec["refused"] is True


def test_livebrain_chat_refuses_opus_on_the_api_key(monkeypatch):
    """The SDK/API-key path must never run Opus — it degrades with a clear config error."""
    import kiln.brain as brainmod

    monkeypatch.setattr(brainmod, "CHAT_MODEL", "claude-opus-4-8")
    text, usage = brainmod.LiveBrain().chat([{"role": "user", "text": "привіт"}], "sys")
    assert "Opus" in text and "claude -p" in text  # refused before any SDK call
    assert usage is None
