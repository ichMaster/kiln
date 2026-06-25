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

from .config import CHAT_MODEL, DEEP_MODEL, DEEP_TOOLS
from .history import to_messages, to_transcript
from .usage import _cli_error_detail, usage_record

# usage: {model, input, output, total} or None
Usage = dict | None


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


class LiveBrain:
    """The real brain: Haiku via the SDK (chat) + Opus via `claude -p` (think/tools)."""

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
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
        cmd.append(full_prompt)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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
        return (data.get("result") or "").strip(), usage_record(DEEP_MODEL, data.get("usage"))


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
