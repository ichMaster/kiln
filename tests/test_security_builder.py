"""
KILN-069: the security builder — profile → the argv/env/cwd of one `claude -p` sub-agent call.

Unit + contract, no subprocess / no paid calls: the built command carries the isolation flags and
the frontmatter ∩ profile tool grant; a sub-agent outside the profile is refused; the profile's
agents are materialized into the workspace; the env is the minimal allowlist.
"""

from __future__ import annotations

import json

import pytest

from kiln.security import (
    SecurityProfile,
    _default_profile,
    _env_allowlist,
    claude_cmd,
    deny_rules,
    effective_tools,
)


def _agents_dir(tmp_path):
    """A fake kiln-owned .claude/agents/ with a couple of sub-agent files."""
    d = tmp_path / "repo" / ".claude" / "agents"
    d.mkdir(parents=True)
    (d / "hands.md").write_text(
        "---\nname: hands\ntools: Read, Glob, Grep, Write, Edit, WebFetch, WebSearch\n"
        "model: sonnet\n---\nbody\n",
        encoding="utf-8",
    )
    (d / "session-wiki.md").write_text(
        "---\nname: session-wiki\ntools: Read, WebFetch, WebSearch\nmodel: sonnet\n---\nbody\n",
        encoding="utf-8",
    )
    return d


def _cmd(tmp_path, profile, agent):
    return claude_cmd(
        profile,
        agent,
        system="sys",
        workspace_dir=tmp_path / "ws",
        security_dir=tmp_path / "sec",
        thinking_tokens=8000,
        source_agents_dir=_agents_dir(tmp_path),
    )


# --- effective_tools: frontmatter ∩ profile ---------------------------------


def test_effective_tools_intersect_ceiling():
    p = _default_profile()  # tools=[Read,Glob,Grep], web on, write workspace, bash off
    # hands asks for a lot; the ceiling clamps it (bash off → no Bash; write on → Write/Edit ok)
    got = effective_tools(
        p, ["Read", "Glob", "Grep", "Write", "Edit", "WebFetch", "WebSearch", "Bash"]
    )
    assert "Bash" not in got  # bash: off
    assert {"Read", "Glob", "Grep", "Write", "Edit", "WebFetch", "WebSearch"} == set(got)


def test_effective_tools_web_off_drops_web_tools():
    p = load_security_inline(web=False)
    got = effective_tools(p, ["Read", "WebFetch", "WebSearch"])
    assert got == ["Read"]  # web off → WebFetch/WebSearch not granted


def test_effective_tools_declared_none_gets_nothing():
    """A sub-agent that declares no tools is tool-less (the deep reasoner) — not 'all tools'."""
    assert effective_tools(_default_profile(), []) == []


def test_effective_tools_always_strips_task():
    p = load_security_inline(tools=["Read", "Task"])
    assert "Task" not in effective_tools(p, ["Read", "Task"])


# --- deny_rules --------------------------------------------------------------


def test_deny_rules_confine_reads_and_writes():
    rules = deny_rules(_default_profile())
    assert "Read(//**)" in rules  # the load-bearing outside-read boundary (KILN-067)
    assert "Task" in rules  # no recursion
    assert "Bash" in rules  # bash: off
    assert "Write(//**)" in rules and "Edit(//**)" in rules  # writes confined to the cwd tree


def test_deny_rules_write_off_blocks_writes_entirely():
    rules = deny_rules(load_security_inline(write="off"))
    assert "Write" in rules and "Edit" in rules


def test_deny_rules_bash_on_does_not_block_bash():
    rules = deny_rules(load_security_inline(bash="on"))
    assert "Bash" not in rules


# --- claude_cmd: the assembled call -----------------------------------------


def test_narrow_profile_command_shape(tmp_path):
    argv, env, cwd = _cmd(tmp_path, _default_profile(), "hands")
    # sub-agent shape + isolation flags
    assert argv[:4] == ["claude", "-p", "--agent", "hands"]
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--setting-sources") + 1] == ""  # operator settings excluded
    assert "--settings" in argv
    # tool grant present, Bash absent (bash: off)
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Read" in allowed and "Bash" not in allowed
    # cwd = the workspace; env is the minimal allowlist (login works, no API key)
    assert cwd == tmp_path / "ws"
    assert "ANTHROPIC_API_KEY" not in env and "PATH" in env and "MAX_THINKING_TOKENS" in env


def test_generated_settings_file_has_deny_rules(tmp_path):
    argv, _, _ = _cmd(tmp_path, _default_profile(), "hands")
    settings_path = argv[argv.index("--settings") + 1]
    data = json.loads(open(settings_path, encoding="utf-8").read())
    assert "Read(//**)" in data["permissions"]["deny"]


def test_agent_outside_profile_is_refused(tmp_path):
    with pytest.raises(PermissionError):
        _cmd(tmp_path, _default_profile(), "not-listed")


def test_materialization_copies_only_profile_agents(tmp_path):
    # profile lists deep+hands+session-wiki; only hands+session-wiki have sources (deep = KILN-070)
    _cmd(tmp_path, _default_profile(), "hands")
    materialized = {p.name for p in (tmp_path / "ws" / ".claude" / "agents").glob("*.md")}
    assert materialized == {"hands.md", "session-wiki.md"}  # deep.md skipped (no source), no others


def test_mcp_config_only_when_servers_listed(tmp_path):
    # default profile has mcp: [] → no --mcp-config, just --strict-mcp-config
    argv, _, _ = _cmd(tmp_path, _default_profile(), "hands")
    assert "--mcp-config" not in argv


def test_env_allowlist_drops_other_secrets(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _env_allowlist(8000)
    assert "AWS_SECRET_ACCESS_KEY" not in env  # only the allowlist keys survive
    assert env["MAX_THINKING_TOKENS"] == "8000"


# --- helper ------------------------------------------------------------------


def load_security_inline(**overrides) -> SecurityProfile:
    """A profile from DEFAULT_SECURITY with a few keys overridden (no file needed)."""
    from kiln.security import DEFAULT_SECURITY, _build

    return _build({**DEFAULT_SECURITY, **overrides})
