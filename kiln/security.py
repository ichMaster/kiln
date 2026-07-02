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

import json
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


# --- The builder (KILN-069): profile → the argv/env/cwd of one `claude -p` sub-agent call ------
# Every `claude -p` kiln spawns is constructed here from the calling agent's profile — there is one
# shape, a sub-agent call (`--agent <name>`). The four layers of ROADMAP §1.4 land as:
#   capability → --allowedTools (frontmatter ∩ profile) + --disallowedTools (hard blocks);
#   settings   → a generated --settings file (permission deny rules) + --setting-sources "" (the
#                operator's own user/project/local settings excluded);
#   process    → cwd = the per-agent workspace, the profile's agents materialized into it, a minimal
#                env allowlist (verified in KILN-067: PATH+HOME alone breaks the CLI login);
#   MCP        → --strict-mcp-config (+ a generated --mcp-config for the profile's servers only).

# The subprocess env allowlist. PATH+HOME alone makes the CLI fall back to (absent) API-key auth;
# this is the verified-minimal set that keeps the subscription login working (KILN-067 correction 4)
# while dropping every other shell secret the old "everything minus the key" env used to leak.
_ENV_KEEP = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


def _agent_meta(agent: str, agents_dir: Path) -> dict:
    """Parse a Claude Code agent file's YAML frontmatter (top-level `key: value` lines only) —
    kiln reads `model` and `tools` from `<agents_dir>/<agent>.md`. Indented continuation lines
    (e.g. a folded `description: >-`) are skipped. Missing/malformed → {}."""
    meta: dict[str, str] = {}
    try:
        text = (agents_dir / f"{agent}.md").read_text(encoding="utf-8")
    except OSError:
        return meta
    if not text.startswith("---"):
        return meta
    block = text.partition("---")[2].partition("---")[0]  # between the two fences
    for line in block.splitlines():
        if not line[:1].strip() or ":" not in line:  # skip indented/continuation lines
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip()
    return meta


def _frontmatter_tools(agent: str, agents_dir: Path) -> list[str]:
    return [
        t.strip() for t in _agent_meta(agent, agents_dir).get("tools", "").split(",") if t.strip()
    ]


def effective_tools(profile: SecurityProfile, frontmatter_tools: list[str]) -> list[str]:
    """The grant a sub-agent actually gets: its declared `tools:` ∩ the profile ceiling. The ceiling
    is `profile.tools` widened by the capability switches (web → WebFetch/WebSearch; write →
    Write/Edit; bash → Bash). `Task` is always stripped (a sub-agent can't spawn further agents).
    An agent that declares no tools gets nothing — tool-less by construction (e.g. `deep`)."""
    ceiling = set(profile.tools)
    if profile.web:
        ceiling |= {"WebFetch", "WebSearch"}
    if profile.write == "workspace":
        ceiling |= {"Write", "Edit"}
    if profile.bash != "off":
        ceiling |= {"Bash"}
    granted = set(frontmatter_tools) & ceiling
    granted.discard("Task")
    return sorted(granted)


def deny_rules(profile: SecurityProfile) -> list[str]:
    """Permission `deny` rules for the generated settings file. `Read(//**)` is the load-bearing
    read boundary (KILN-067: cwd does NOT auto-confine Read); the cwd-relative tree stays readable.
    Writes/Bash/recursion are hard-blocked unless the profile grants them."""
    rules = [
        "Read(//**)",  # deny absolute-path reads (outside the workspace cwd); relative reads stay
        "Read(~/.ssh/**)",
        "Read(**/.env)",
        "Task",  # no agent recursion
    ]
    if profile.bash == "off":
        rules.append("Bash")
    if profile.write == "workspace":
        rules += ["Write(//**)", "Edit(//**)"]  # writes confined to the cwd (relative) tree
    elif profile.write == "off":
        rules += ["Write", "Edit"]
    if not profile.web:
        rules += ["WebFetch", "WebSearch"]
    return rules


def _env_allowlist(thinking_tokens: int) -> dict:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env["MAX_THINKING_TOKENS"] = str(thinking_tokens)  # extended thinking ON (never the API key)
    return env


def _inject_model(text: str, model: str) -> str:
    """Rewrite the frontmatter `model:` line to `model` (per-agent config stays authoritative — e.g.
    the persona's deep_model into deep.md). Appends the line if absent within the frontmatter."""
    lines = text.splitlines(keepends=True)
    fences = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(fences) < 2:
        return text  # no frontmatter block — leave as is
    lo, hi = fences[0], fences[1]
    for i in range(lo + 1, hi):
        if lines[i].startswith("model:"):
            lines[i] = f"model: {model}\n"
            return "".join(lines)
    lines.insert(hi, f"model: {model}\n")
    return "".join(lines)


def _materialize_agents(
    profile: SecurityProfile,
    workspace: Path,
    source_agents_dir: Path,
    model_overrides: dict | None = None,
) -> None:
    """Copy the profile's allowed sub-agent definitions into `<workspace>/.claude/agents/` so the
    call resolves ONLY kiln-owned agents (never the repo's `.claude/`). Missing sources are skipped
    rather than crashing. `model_overrides` (e.g. `{"deep": deep_model}`) rewrites the frontmatter
    `model:` so per-agent config wins over the committed file."""
    overrides = model_overrides or {}
    dest = workspace / ".claude" / "agents"
    dest.mkdir(parents=True, exist_ok=True)
    for agent in profile.agents:
        src = source_agents_dir / f"{agent}.md"
        if not src.exists():
            continue
        text = src.read_text(encoding="utf-8")
        if agent in overrides:
            text = _inject_model(text, overrides[agent])
        (dest / f"{agent}.md").write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def claude_cmd(
    profile: SecurityProfile,
    agent: str,
    *,
    system: str,
    workspace_dir: Path,
    security_dir: Path,
    thinking_tokens: int,
    source_agents_dir: Path,
    model_overrides: dict | None = None,
) -> tuple[list[str], dict, Path]:
    """Build `(argv, env, cwd)` for one `claude -p --agent <agent>` call under `profile`.

    Raises `PermissionError` if the profile does not list `agent` (the gate — refused before any
    spawn). Side effects: mkdir the workspace, materialize the profile's agents into it (rewriting
    the `model:` of any in `model_overrides`), and write the generated settings (+ MCP) under
    `security_dir`.
    """
    if not profile.allows_agent(agent):
        raise PermissionError(
            f"agent {agent!r} is not in this profile's agents: {list(profile.agents)}"
        )

    workspace_dir.mkdir(parents=True, exist_ok=True)
    _materialize_agents(profile, workspace_dir, source_agents_dir, model_overrides)

    granted = effective_tools(profile, _frontmatter_tools(agent, source_agents_dir))
    settings = {"permissions": {"deny": deny_rules(profile)}}
    settings_path = _write_json(security_dir / f"{agent}.settings.json", settings)

    argv = [
        "claude",
        "-p",
        "--agent",
        agent,
        "--output-format",
        "json",
        "--append-system-prompt",
        system,
        "--setting-sources",
        "",  # exclude the operator's user/project/local settings
        "--settings",
        str(settings_path),
        "--strict-mcp-config",  # ignore the operator's MCP servers
        "--permission-mode",
        "default",  # in -p mode, unpermitted tools are denied (never a prompt)
    ]
    if granted:
        argv += ["--allowedTools", " ".join(granted)]
    hard_block = [
        r
        for r in ("Bash", "Write", "Edit", "WebFetch", "WebSearch", "Task")
        if _is_blocked(profile, r)
    ]
    if hard_block:
        argv += ["--disallowedTools", " ".join(hard_block)]
    if profile.mcp:
        mcp = {"mcpServers": _resolve_mcp(profile, source_agents_dir.parent.parent)}
        argv += ["--mcp-config", str(_write_json(security_dir / f"{agent}.mcp.json", mcp))]
    if profile.add_dirs:
        for d in profile.add_dirs:
            argv += ["--add-dir", d]

    return argv, _env_allowlist(thinking_tokens), workspace_dir


def _is_blocked(profile: SecurityProfile, tool: str) -> bool:
    """Whether a tool is hard-blocked for this profile (belt-and-suspenders vs allowedTools)."""
    if tool == "Task":
        return True  # recursion always denied
    if tool == "Bash":
        return profile.bash == "off"
    if tool in ("Write", "Edit"):
        return profile.write == "off"
    if tool in ("WebFetch", "WebSearch"):
        return not profile.web
    return False


def _resolve_mcp(profile: SecurityProfile, project_root: Path) -> dict:
    """Load the profile's named servers from the kiln-owned `state/mcp.yaml` registry (only these,
    never the operator's). Unknown names are skipped. Empty/absent registry → {}."""
    registry_path = project_root / "state" / "mcp.yaml"
    try:
        import yaml

        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — any failure → no servers (fail closed)
        return {}
    servers = registry.get("servers", {}) if isinstance(registry, dict) else {}
    return {name: servers[name] for name in profile.mcp if name in servers}
