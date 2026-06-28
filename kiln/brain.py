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

from .config import AGENTS_DIR, CHAT_MODEL, DEEP_MODEL, DEEP_TOOLS, claude_env
from .history import to_messages, to_transcript
from .usage import _cli_error_detail, usage_record

# usage: {model, input, output, total} or None
Usage = dict | None


def _is_opus(model: str) -> bool:
    """Whether a model id is an Opus model (the expensive tier)."""
    return "opus" in model.lower()


def _agent_meta(agent: str) -> dict:
    """Parse a Claude Code agent file's YAML frontmatter (top-level `key: value` lines only).

    Used by `claude -p --agent <agent>`: kiln reads the agent's `.claude/agents/<agent>.md`
    frontmatter to learn its `model` and `tools` (the agent file is the single source of
    truth). Indented continuation lines (e.g. a folded `description: >-`) are skipped.
    """
    meta: dict[str, str] = {}
    try:
        text = (AGENTS_DIR / f"{agent}.md").read_text(encoding="utf-8")
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


class LiveBrain:
    """The real brain: Haiku via the SDK (chat) + Opus via `claude -p` (think/tools)."""

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

        # --output-format json: we get both the text (result) and usage in one call.
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
        if with_tools and DEEP_TOOLS:
            cmd += ["--allowedTools", ",".join(DEEP_TOOLS)]
        # --allowedTools is variadic (<tools...>), so a trailing positional prompt would be
        # swallowed as another tool name. Pass the prompt via stdin to avoid that. claude_env()
        # turns on extended thinking and strips the API key (Opus bills via the CLI login).
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
        # Run the named sub-agent via `claude -p --agent <agent>`: Claude Code loads
        # .claude/agents/<agent>.md (its body IS the system prompt). We read that file's
        # frontmatter only to pass --allowedTools (headless permission) and to label usage
        # with its model. The recent conversation goes in as a transcript; canon/memory ride
        # on --append-system-prompt. Degrades to "(<agent> error: …)" — never crashes the loop.
        meta = _agent_meta(agent)
        model = meta.get("model", "sonnet")
        tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
        convo = to_transcript(history) if history else "(no conversation yet)"
        prompt = (
            "Recent conversation (your source material):\n"
            + convo
            + "\n\nRun your agent instructions over this conversation. Output ONLY your "
            "final deliverable — no preamble, no narration, no description of your steps, "
            "no commentary before or after it."
        )
        cmd = ["claude", "-p", "--agent", agent, "--output-format", "json"]
        if system:
            cmd += ["--append-system-prompt", system]
        if tools:
            cmd += ["--allowedTools", ",".join(tools)]
        # --allowedTools is variadic (<tools...>), so a trailing positional prompt would be
        # swallowed as another tool name. Pass the prompt via stdin to avoid that. claude_env()
        # turns on extended thinking and strips the API key (sub-agent bills via the CLI login).
        try:
            result = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, timeout=180, env=claude_env()
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
        model = _agent_meta(agent).get("model", "sonnet")
        return text, usage_record(model, {"input_tokens": 16, "output_tokens": 24})
