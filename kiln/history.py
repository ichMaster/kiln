"""
kiln — conversation history: the shared message feed of a session.

No trimming or summarization: we accumulate everything and append it to the prompt.
Each item is {"role": "user"|"assistant", "text": ..., "at": "<ISO timestamp>"} (v0.8). The
`at` stamp powers the recent-timed-messages block; `to_messages`/`to_transcript` ignore it.
"""

from __future__ import annotations

import datetime as _dt

from .config import AGENT_NAME, USER_NAME

ROLE_USER = "user"
ROLE_BOT = "assistant"


def role_label(role: str) -> str:
    """Speaker name for a turn's role in transcripts / timeline / `/prompt` (USER_NAME /
    AGENT_NAME; falls back to the raw role for anything else)."""
    if role == ROLE_USER:
        return USER_NAME
    if role == ROLE_BOT:
        return AGENT_NAME
    return role


def turn(role: str, text: str, at: str | None = None) -> dict:
    """A conversation turn: {role, text, at}. `at` is an ISO timestamp (seconds); when None it
    is stamped with the current local time (the source of `at` for the timeline, KILN-033)."""
    return {
        "role": role,
        "text": text,
        "at": at or _dt.datetime.now().isoformat(timespec="seconds"),
    }


def to_messages(history: list[dict]) -> list[dict]:
    """History -> Anthropic Messages API format ({role, content}); the `at` stamp is ignored."""
    return [{"role": h["role"], "content": h["text"]} for h in history]


def to_transcript(history: list[dict]) -> str:
    """History -> a plain text transcript for embedding into the CLI prompt (named speakers)."""
    return "\n".join(f"{role_label(h['role'])}: {h['text']}" for h in history)
