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

import json
import subprocess
from typing import Protocol, runtime_checkable

from .config import AGENTS_DIR, CHAT_MODEL, DEEP_MODEL, THINKING_TOKENS, claude_env
from .history import to_messages, to_transcript
from .security import SecurityProfile, _agent_meta, _default_profile, claude_cmd
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

    def deep(
        self, prompt: str, history: list[dict], system: str, with_tools: bool
    ) -> tuple[str, Usage]:
        """A deep turn (reasoning or tools); history goes into the prompt as a transcript."""
        ...

    def tool(self, agent: str, history: list[dict], system: str) -> tuple[str, Usage]:
        """Run a named Claude Code sub-agent (`claude -p --agent <agent>`); the recent
        history goes in as a transcript. Used by "tool" self-triggers (e.g. session-wiki)."""
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

    def __init__(self, profile: SecurityProfile | None = None, paths=None) -> None:
        # No-arg LiveBrain() (the CLI default + the contract tests) resolves the deny-first default
        # profile + the default agent's paths; the host passes the per-agent profile/paths.
        from .config import AgentPaths

        self._profile = profile if profile is not None else _default_profile()
        self._paths = paths if paths is not None else AgentPaths.for_agent()
        kiln_dir = self._paths.store_file.parent  # .kiln[/{id}]
        self._workspace = kiln_dir / "workspace"  # cwd of every claude -p call
        self._security = kiln_dir / "security"  # generated settings / mcp configs (kiln-owned)

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

    def deep(
        self, prompt: str, history: list[dict], system: str, with_tools: bool
    ) -> tuple[str, Usage]:
        # The subprocess holds no session between calls, so prior turns are embedded
        # as a text transcript, with the current prompt at the end.
        prior = history[:-1] if history else []
        full_prompt = prompt
        if prior:
            full_prompt = (
                "Контекст розмови:\n"
                + to_transcript(prior)
                + "\n\nПоточне повідомлення:\n"
                + prompt
            )

        # v1.4: deep is TOOL-LESS (the armed "tools" class now fires the `hands` sub-agent via
        # `tool()`; DEEP_TOOLS is retired). `with_tools` is kept for signature stability and
        # ignored. KILN-070 converts this raw call into the `deep` sub-agent through the builder.
        cmd = [
            "claude",
            "-p",
            "--model",
            DEEP_MODEL,
            "--output-format",
            "json",
            "--append-system-prompt",
            system,
        ]
        # The prompt goes via stdin (not a positional arg). claude_env() turns on extended thinking
        # and strips the API key (Opus bills via the CLI login).
        try:
            result = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=180,
                env=claude_env(),
            )
        except Exception as e:  # timeout / process failed to start
            return f"(deep error: {e})", None
        if result.returncode != 0:
            # The CLI occasionally fails (a limit, a transient error) — don't crash the loop.
            return (
                f"(deep error: claude CLI {result.returncode}: {_cli_error_detail(result)})",
                None,
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.stdout.strip(), None  # unexpected output — as is
        # claude -p reports the actual cost; carry it (over any estimate) into the record.
        rec = usage_record(DEEP_MODEL, data.get("usage"), data.get("total_cost_usd"))
        return (data.get("result") or "").strip(), rec

    def tool(self, agent: str, history: list[dict], system: str) -> tuple[str, Usage]:
        # Run a named sub-agent via the SECURITY BUILDER (KILN-069): `claude -p --agent <agent>`
        # under this agent's capability profile — cwd = its workspace, the profile's agents
        # materialized in, a minimal env, generated settings (deny rules) + MCP isolation, and the
        # tool grant = frontmatter ∩ profile. The agent file's body IS its system prompt;
        # canon/memory ride on --append-system-prompt. An agent not in the profile's `agents:`
        # list is refused before a spawn. Degrades to "(<agent> error: …)" — never crashes.
        model = _meta(agent).get("model", "sonnet")  # usage label only (builder reads tools itself)
        prompt = _agent_prompt(agent, history)
        try:
            argv, env, cwd = claude_cmd(
                self._profile,
                agent,
                system=system,
                workspace_dir=self._workspace,
                security_dir=self._security,
                thinking_tokens=THINKING_TOKENS,
                source_agents_dir=AGENTS_DIR,
            )
        except PermissionError as e:
            return f"({agent} error: {e})", None
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
            return f"({agent} error: {e})", None
        if result.returncode != 0:
            return (
                f"({agent} error: claude CLI {result.returncode}: {_cli_error_detail(result)})",
                None,
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.stdout.strip(), None
        rec = usage_record(model, data.get("usage"), data.get("total_cost_usd"))
        return (data.get("result") or "").strip(), rec


class MockBrain:
    """
    Deterministic brain for dry-run and tests: canned replies + synthetic
    usage. No network calls and no subprocesses — hence zero paid calls.
    The reply texts deliberately match the former dry-run stubs.
    """

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
        text = f"(dry-run chat: messages={len(history)})"
        return text, usage_record(CHAT_MODEL, {"input_tokens": 8, "output_tokens": 12})

    def deep(
        self, prompt: str, history: list[dict], system: str, with_tools: bool
    ) -> tuple[str, Usage]:
        prior = len(history) - 1 if history else 0
        text = f"(dry-run deep: transcript={prior} turns, tools={with_tools})"
        return text, usage_record(DEEP_MODEL, {"input_tokens": 20, "output_tokens": 30})

    def tool(self, agent: str, history: list[dict], system: str) -> tuple[str, Usage]:
        text = f"(dry-run tool[{agent}]: turns={len(history)})"
        model = _meta(agent).get("model", "sonnet")
        return text, usage_record(model, {"input_tokens": 16, "output_tokens": 24})
