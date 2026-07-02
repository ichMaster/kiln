"""
kiln — the "brain" seam: both branches behind one thin interface.

The core (respond/run) calls the model ONLY through this seam — never the SDK or CLI directly.
Implementations:
  - LiveBrain — real calls: chat via the Anthropic Messages API (Haiku),
    think/tools via `claude -p` (Opus, subprocess);
  - MockBrain — deterministic canned replies + synthetic usage, with no
    network or subprocesses (the dry-run demo and all tests go through it → 0 paid calls).

Contract: each method returns (text, usage), where usage is {model, input, output, total}
or None (error / no call). Token parsing lives in usage.usage_record.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import time
from typing import Protocol, runtime_checkable

from .config import AGENTS_DIR, CHAT_MODEL, DEEP_MODEL, THINKING_TOKENS
from .history import to_messages, to_transcript
from .security import SecurityProfile, _agent_meta, _default_profile, append_audit, claude_cmd
from .usage import _cli_error_detail, usage_record

# usage: {model, input, output, total} or None
Usage = dict | None


def _is_opus(model: str) -> bool:
    """Whether a model id is an Opus model (the expensive tier)."""
    return "opus" in model.lower()


def _meta(agent: str) -> dict:
    """Frontmatter (`model`/`tools`) of `.claude/agents/<agent>.md`. Thin wrapper over
    `security._agent_meta` bound to the repo's AGENTS_DIR (used for usage-label `model`; the
    builder reads tools itself). Kept so MockBrain can label usage without the builder."""
    return _agent_meta(agent, AGENTS_DIR)


@runtime_checkable
class Brain(Protocol):
    """Seam between the core and the model. Two branches — cheap chat and a deep turn."""

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
        """A cheap, fast reply (the whole history goes in as messages)."""
        ...

    def tool(self, agent: str, history: list[dict], system: str) -> tuple[str, Usage]:
        """Run a named Claude Code sub-agent (`claude -p --agent <agent>`); the recent history
        goes in as a transcript. v1.4: the ONLY `claude -p` shape — reasoning is the `deep`
        sub-agent, acting is `hands`, specialists are themselves (session-wiki)."""
        ...


# Sub-agents whose output is a standalone deliverable (session-wiki's Wikipedia paragraph), vs the
# conversational ones (hands, and deep in KILN-070) that answer the dialogue in the persona's voice.
_DELIVERABLE_AGENTS = {"session-wiki"}


def _agent_prompt(agent: str, history: list[dict]) -> str:
    """The stdin prompt for a sub-agent call: the recent conversation + an instruction shaped by
    whether the agent produces a deliverable or answers the dialogue."""
    convo = to_transcript(history) if history else "(no conversation yet)"
    if agent in _DELIVERABLE_AGENTS:
        return (
            "Recent conversation (your source material):\n"
            + convo
            + "\n\nRun your agent instructions over this conversation. Output ONLY your "
            "final deliverable — no preamble, no narration, no description of your steps, "
            "no commentary before or after it."
        )
    return (
        "Розмова:\n"
        + convo
        + "\n\nВиконай прохання з останнього повідомлення і дай коротку відповідь у голосі "
        "персони, українською. Тільки відповідь — без опису кроків, без преамбул."
    )


class LiveBrain:
    """The real brain: Haiku via the SDK (chat) + `claude -p` sub-agents (deep/tools) built by the
    security builder. Per-agent: `profile` (the capability profile) + `paths` (its workspace)."""

    def __init__(
        self,
        profile: SecurityProfile | None = None,
        paths=None,
        deep_model: str | None = None,
        agent_id: str = "agnika",
    ) -> None:
        # No-arg LiveBrain() (the CLI default + the contract tests) resolves the deny-first default
        # profile + the default agent's paths; the host passes the per-agent profile/paths/model.
        from .config import AgentPaths

        self._profile = profile if profile is not None else _default_profile()
        self._paths = paths if paths is not None else AgentPaths.for_agent()
        self._deep_model = deep_model or DEEP_MODEL  # injected into deep.md at materialization
        self._agent_id = agent_id
        kiln_dir = self._paths.store_file.parent  # .kiln[/{id}]
        self._workspace = kiln_dir / "workspace"  # cwd of every claude -p call
        self._security = kiln_dir / "security"  # generated settings / mcp configs (kiln-owned)
        self._audit = kiln_dir / "claude-audit.jsonl"  # one line per (refused) spawn (KILN-072)

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
        # Invariant: the API-key (SDK) path is for the CHEAP model only. Opus must NEVER be
        # billed via the API key — it runs only through `claude -p` (deep/tool). Refuse here.
        if _is_opus(CHAT_MODEL):
            return (
                f"(config error: CHAT_MODEL '{CHAT_MODEL}' is Opus; the API/chat branch must "
                "not call Opus — Opus runs only via `claude -p`. Set CHAT_MODEL to a cheap model.)",
                None,
            )
        # Local import: dry-run/tests work without the anthropic package — it's
        # needed only by this live branch. Key comes from ANTHROPIC_API_KEY (.env -> os.environ).
        from anthropic import Anthropic

        try:
            msg = Anthropic().messages.create(
                model=CHAT_MODEL,  # Haiku 4.5 — cheap and fast
                max_tokens=512,  # short reply
                system=system,  # canon + long-term memory
                messages=to_messages(history),  # the whole transcript, with the current turn
            )
        except Exception as e:  # network / limits / API error
            return f"(chat error: {e})", None
        text = next((b.text for b in msg.content if b.type == "text"), "")
        # SDK/Haiku has no per-call cost — leave cost_usd=None (estimated from the price table).
        return text, usage_record(msg.model, msg.usage)

    def tool(self, agent: str, history: list[dict], system: str) -> tuple[str, Usage]:
        # Run a named sub-agent via the SECURITY BUILDER (KILN-069): `claude -p --agent <agent>`
        # under this agent's capability profile — cwd = its workspace, the profile's agents
        # materialized in, a minimal env, generated settings (deny rules) + MCP isolation, and the
        # tool grant = frontmatter ∩ profile. The agent file's body IS its system prompt;
        # canon/memory ride on --append-system-prompt. An agent not in the profile's `agents:`
        # list is refused before a spawn. Degrades to "(<agent> error: …)" — never crashes.
        # usage label: deep runs on the persona's deep_model (injected at materialization), others
        # on their frontmatter model. The builder reads the tool grant itself.
        model = self._deep_model if agent == "deep" else _meta(agent).get("model", "sonnet")
        prompt = _agent_prompt(agent, history)
        base = {"ts": _dt.datetime.now().isoformat(timespec="seconds"), "agent_id": self._agent_id}
        try:
            argv, env, cwd = claude_cmd(
                self._profile,
                agent,
                system=system,
                workspace_dir=self._workspace,
                security_dir=self._security,
                thinking_tokens=THINKING_TOKENS,
                source_agents_dir=AGENTS_DIR,
                model_overrides={"deep": self._deep_model},
            )
        except PermissionError as e:
            # a sub-agent outside the profile's agents: — refused before any spawn, and audited
            append_audit(
                self._audit, {**base, "sub_agent": agent, "refused": True, "reason": str(e)}
            )
            return f"({agent} error: {e})", None

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._profile.timeout_seconds,
                cwd=str(cwd),
                env=env,
            )
        except Exception as e:  # timeout / process failed to start
            self._audit_spawn(base, agent, argv, cwd, t0, exit_code=None, usage=None, error=str(e))
            return f"({agent} error: {e})", None

        if result.returncode != 0:
            self._audit_spawn(base, agent, argv, cwd, t0, exit_code=result.returncode, usage=None)
            return (
                f"({agent} error: claude CLI {result.returncode}: {_cli_error_detail(result)})",
                None,
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self._audit_spawn(base, agent, argv, cwd, t0, exit_code=0, usage=None)
            return result.stdout.strip(), None
        rec = usage_record(model, data.get("usage"), data.get("total_cost_usd"))
        self._audit_spawn(base, agent, argv, cwd, t0, exit_code=0, usage=rec)
        return (data.get("result") or "").strip(), rec

    def _audit_spawn(self, base, agent, argv, cwd, t0, *, exit_code, usage, error=None) -> None:
        """Write one audit line for a spawn (KILN-072): timestamp, agent, argv, cwd, exit code,
        duration, usage. Best-effort via `append_audit` (a write failure never crashes the loop)."""
        rec = {
            **base,
            "sub_agent": agent,
            "argv": argv,
            "cwd": str(cwd),
            "exit": exit_code,
            "duration_s": round(time.monotonic() - t0, 3),
            "usage": usage,
        }
        if error is not None:
            rec["error"] = error
        append_audit(self._audit, rec)


class MockBrain:
    """
    Deterministic brain for dry-run and tests: canned replies + synthetic
    usage. No network calls and no subprocesses — hence zero paid calls.
    The reply texts deliberately match the former dry-run stubs.
    """

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
        text = f"(dry-run chat: messages={len(history)})"
        return text, usage_record(CHAT_MODEL, {"input_tokens": 8, "output_tokens": 12})

    def tool(self, agent: str, history: list[dict], system: str) -> tuple[str, Usage]:
        text = f"(dry-run tool[{agent}]: turns={len(history)})"
        # deep runs on the deep model; other sub-agents on their frontmatter model.
        model = DEEP_MODEL if agent == "deep" else _meta(agent).get("model", "sonnet")
        return text, usage_record(model, {"input_tokens": 16, "output_tokens": 24})
