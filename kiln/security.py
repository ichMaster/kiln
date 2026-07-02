"""
kiln — the deep-branch security profile (v1.4, KILN-068).

Every `claude -p` subprocess kiln spawns is gated by a per-agent **capability profile**: a
committed `state/{id}/security.yaml` describing the workspace, the tool ceiling, and which
sub-agents the persona may fire. This module owns the profile shape (`SecurityProfile`), the
built-in `DEFAULT_SECURITY` baseline, and `load_security` — **the one loader in kiln whose
fallback is deny-first**: a missing, unparsable, or structurally invalid file heals to
`DEFAULT_SECURITY` (locked-down, not permissive), unlike the friendly `DEFAULT_*` heals elsewhere.

The builder that turns a profile into the actual argv/env/cwd (`claude_cmd`) lands in KILN-069;
this issue is the profile + loader + the `AgentConfig` wiring + the `GET /agents` surface.

Env overrides use the **`KILN_SEC_`** prefix (e.g. `KILN_SEC_BASH=on`) rather than bare
UPPER_SNAKE — the security scalars are namespaced deliberately so a stray `BASH`/`WEB` in the
operator's environment can never silently widen an agent's capabilities.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The deny-first baseline: what a fresh clone, a broken `security.yaml`, or a narrow companion
# without its own file resolves to. Reasonably usable (read-only tools + web + workspace writes)
# but no Bash, no MCP, no reach outside the workspace. This is the FLOOR — a malformed file never
# heals to anything broader than this.
DEFAULT_SECURITY = {
    "workspace": "auto",  # "auto" → .kiln/{id}/workspace/ ; or an explicit path (KILN-069)
    "tools": ["Read", "Glob", "Grep"],  # tool CEILING — a sub-agent gets its frontmatter ∩ this
    "write": "workspace",  # off | workspace — Write/Edit confined to the workspace
    "bash": "off",  # off | sandbox | on (on = explicit operator opt-in)
    "web": True,  # WebFetch/WebSearch — the single default-on capability
    "mcp": [],  # kiln-owned MCP servers (state/mcp.yaml) this persona may load
    "add_dirs": [],  # extra readable dirs (--add-dir), granted deliberately
    "agents": ["deep", "hands", "session-wiki"],  # the ONLY sub-agents this persona may fire
    "max_turns": 10,  # NB: no --max-turns flag in the CLI (KILN-067) — documented no-op placeholder
    "timeout_seconds": 180,  # the real per-call limiter (subprocess timeout)
}

_WRITE_MODES = ("off", "workspace")
_BASH_MODES = ("off", "sandbox", "on")

# Scalar keys overridable from the environment (KILN_SEC_<UPPER>); lists are not env-overridable.
_ENV_SCALARS = ("workspace", "write", "bash", "web", "max_turns", "timeout_seconds")


@dataclass(frozen=True)
class SecurityProfile:
    """One agent's resolved capability profile. Immutable; built by `load_security`."""

    workspace: str
    tools: tuple
    write: str
    bash: str
    web: bool
    mcp: tuple
    add_dirs: tuple
    agents: tuple
    max_turns: int
    timeout_seconds: int

    def allows_agent(self, agent: str) -> bool:
        """Whether this persona may fire the named sub-agent (the gate the builder checks)."""
        return agent in self.agents

    def summary(self) -> dict:
        """A compact, JSON-safe view for `GET /agents` / operator inspection (lists → lists)."""
        return {
            "workspace": self.workspace,
            "tools": list(self.tools),
            "write": self.write,
            "bash": self.bash,
            "web": self.web,
            "mcp": list(self.mcp),
            "add_dirs": list(self.add_dirs),
            "agents": list(self.agents),
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
        }


def _default_profile() -> SecurityProfile:
    return _build(dict(DEFAULT_SECURITY))


def _build(cfg: dict) -> SecurityProfile:
    """Coerce + validate a merged config dict into a `SecurityProfile`; raises on a bad shape so the
    caller can fall back to the deny-first default."""
    write = str(cfg["write"])
    bash = str(cfg["bash"])
    if write not in _WRITE_MODES:
        raise ValueError(f"write must be one of {_WRITE_MODES}")
    if bash not in _BASH_MODES:
        raise ValueError(f"bash must be one of {_BASH_MODES}")
    return SecurityProfile(
        workspace=str(cfg["workspace"]),
        tools=tuple(str(t) for t in cfg["tools"]),
        write=write,
        bash=bash,
        web=bool(cfg["web"]),
        mcp=tuple(str(m) for m in cfg["mcp"]),
        add_dirs=tuple(str(d) for d in cfg["add_dirs"]),
        agents=tuple(str(a) for a in cfg["agents"]),
        max_turns=int(cfg["max_turns"]),
        timeout_seconds=int(cfg["timeout_seconds"]),
    )


def _apply_env(cfg: dict) -> dict:
    """Overlay `KILN_SEC_<UPPER>` env vars onto the scalar keys (the operator escape hatch). Bad
    values are ignored (kept as the file/default), never crashing the load."""
    for key in _ENV_SCALARS:
        raw = os.environ.get("KILN_SEC_" + key.upper())
        if raw is None:
            continue
        if key == "web":
            cfg[key] = raw == "1"
        elif key in ("max_turns", "timeout_seconds"):
            try:
                cfg[key] = int(raw)
            except ValueError:
                continue
        else:
            cfg[key] = raw
    return cfg


def load_security(path: Path) -> SecurityProfile:
    """Resolve an agent's capability profile from `state/{id}/security.yaml`.

    **Fail-closed:** a missing file, no PyYAML, a parse error, a non-mapping, or any structurally
    invalid value heals to `DEFAULT_SECURITY` — the deny-first baseline, never something broader.
    On a valid file, its keys override the default per key; then `KILN_SEC_*` env vars override the
    scalars.
    """
    try:
        import yaml
    except ImportError:
        return _default_profile()
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return _default_profile()
    if not isinstance(data, dict):
        return _default_profile()
    merged = _apply_env({**DEFAULT_SECURITY, **data})
    try:
        return _build(merged)
    except (KeyError, TypeError, ValueError):
        return _default_profile()  # structurally malformed → the deny-first default
