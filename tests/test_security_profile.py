"""
KILN-068: the deep-branch security profile + its fail-closed loader.

Unit + contract, no brain / no paid calls: the profile shape is pinned; a missing / corrupt /
structurally-invalid `security.yaml` heals to the deny-first `DEFAULT_SECURITY` (NOT to anything
broader); `KILN_SEC_*` env vars override the scalars; two agents' profiles are independent; and
`AgentConfig` carries the resolved profile.
"""

from __future__ import annotations

import kiln.config as config
from kiln.security import (
    DEFAULT_SECURITY,
    SecurityProfile,
    _default_profile,
    load_security,
)

_KEYS = {
    "workspace",
    "tools",
    "write",
    "bash",
    "web",
    "mcp",
    "add_dirs",
    "agents",
    "max_turns",
    "timeout_seconds",
}


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_default_profile_shape_and_deny_first_values():
    """Contract: the profile has exactly the documented keys, and the built-in default is
    deny-first (no Bash, writes confined, no MCP) with web the single default-on capability."""
    p = _default_profile()
    assert set(p.summary()) == _KEYS
    assert p.bash == "off"
    assert p.write == "workspace"
    assert p.tools == ("Read", "Glob", "Grep")
    assert p.mcp == ()
    assert p.web is True  # the one default-on capability
    assert p.agents == ("deep", "hands", "session-wiki")


def test_valid_file_overrides_per_key(tmp_path):
    prof = load_security(
        _write(tmp_path / "security.yaml", "bash: sandbox\nweb: false\nagents: [deep]\n")
    )
    assert prof.bash == "sandbox"
    assert prof.web is False
    assert prof.agents == ("deep",)
    # unspecified keys keep the deny-first defaults
    assert prof.tools == ("Read", "Glob", "Grep")
    assert prof.write == "workspace"


def test_missing_file_heals_closed(tmp_path):
    prof = load_security(tmp_path / "does-not-exist.yaml")
    assert prof == _default_profile()


def test_corrupt_yaml_heals_closed(tmp_path):
    prof = load_security(_write(tmp_path / "security.yaml", "bash: [unterminated\n:::"))
    assert prof == _default_profile()


def test_non_mapping_heals_closed(tmp_path):
    prof = load_security(_write(tmp_path / "security.yaml", "- just\n- a\n- list\n"))
    assert prof == _default_profile()


def test_invalid_enum_heals_closed(tmp_path):
    """A bad `bash`/`write` value must NOT be silently accepted — the whole profile fails closed
    to the deny-first default rather than granting an unknown mode."""
    prof = load_security(_write(tmp_path / "security.yaml", "bash: yes-please\n"))
    assert prof == _default_profile()
    assert prof.bash == "off"


def test_env_override_wins_over_file(tmp_path, monkeypatch):
    f = _write(tmp_path / "security.yaml", "bash: off\ntimeout_seconds: 180\nweb: true\n")
    monkeypatch.setenv("KILN_SEC_BASH", "on")
    monkeypatch.setenv("KILN_SEC_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("KILN_SEC_WEB", "0")
    prof = load_security(f)
    assert prof.bash == "on"
    assert prof.timeout_seconds == 42
    assert prof.web is False


def test_profiles_are_independent(tmp_path):
    a = load_security(_write(tmp_path / "a" / "security.yaml", "agents: [deep]\n"))
    b = load_security(_write(tmp_path / "b" / "security.yaml", "agents: [hands]\n"))
    assert a.agents == ("deep",)
    assert b.agents == ("hands",)  # no bleed between two loads
    assert isinstance(a, SecurityProfile) and a is not b


def test_allows_agent_gate():
    p = _default_profile()
    assert p.allows_agent("deep") and p.allows_agent("session-wiki")
    assert not p.allows_agent("nonexistent")


def test_default_dict_is_not_mutated_by_a_load(tmp_path):
    """Loading a file that overrides keys must not mutate the module-level DEFAULT_SECURITY."""
    before = dict(DEFAULT_SECURITY)
    load_security(_write(tmp_path / "security.yaml", "bash: on\ntools: [Read]\n"))
    assert DEFAULT_SECURITY == before


def test_agent_config_carries_the_profile():
    """The default agent resolves the committed `state/security.yaml` (reproducing the defaults)."""
    c = config.AgentConfig.for_agent("agnika")
    assert isinstance(c.security, SecurityProfile)
    assert set(c.security.summary()) == _KEYS
    assert c.security.bash == "off"
