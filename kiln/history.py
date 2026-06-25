"""
kiln — conversation history: the shared message feed of a session.

No trimming or summarization: we accumulate everything and append it to the prompt.
Each item is {"role": "user"|"assistant", "text": ...}.
"""

from __future__ import annotations

ROLE_USER = "user"
ROLE_BOT = "assistant"


def to_messages(history: list[dict]) -> list[dict]:
    """History -> Anthropic Messages API format ({role, content})."""
    return [{"role": h["role"], "content": h["text"]} for h in history]


def to_transcript(history: list[dict]) -> str:
    """History -> a plain text transcript for embedding into the CLI prompt."""
    label = {ROLE_USER: "Користувач", ROLE_BOT: "Ти"}
    return "\n".join(f"{label.get(h['role'], h['role'])}: {h['text']}" for h in history)
