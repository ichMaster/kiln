"""
kiln — conversation history: the shared message feed of a session.

No trimming or summarization: we accumulate everything and append it to the prompt.
Each item is {"role": "user"|"assistant", "text": ..., "at": "<ISO timestamp>"} (v0.8). Every
message carried into the prompt (the messages array, the transcript, the timeline) is prefixed
with its date+time stamp `[Сб 28.06.2026 11:52]` (v0.8).
"""

from __future__ import annotations

import datetime as _dt

from .config import AGENT_NAME, USER_NAME

ROLE_USER = "user"
ROLE_BOT = "assistant"

# short Ukrainian weekday names (index matches datetime.weekday(), Mon=0)
_WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def role_label(role: str) -> str:
    """Speaker name for a turn's role in transcripts / timeline / `/prompt` (USER_NAME /
    AGENT_NAME; falls back to the raw role for anything else)."""
    if role == ROLE_USER:
        return USER_NAME
    if role == ROLE_BOT:
        return AGENT_NAME
    return role


def fmt_stamp(at: str | None) -> str:
    """An ISO `at` -> a `[Сб 28.06.2026 11:52]` date+time label, or "" if absent/unparseable."""
    if not at:
        return ""
    try:
        d = _dt.datetime.fromisoformat(at)
    except ValueError:
        return ""
    return f"[{_WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m.%Y %H:%M}]"


def turn(role: str, text: str, at: str | None = None) -> dict:
    """A conversation turn: {role, text, at}. `at` is an ISO timestamp (seconds); when None it
    is stamped with the current local time (the source of `at` for the timeline, KILN-033)."""
    return {
        "role": role,
        "text": text,
        "at": at or _dt.datetime.now().isoformat(timespec="seconds"),
    }


def to_messages(history: list[dict]) -> list[dict]:
    """History -> Anthropic Messages API format ({role, content}); each content is prefixed with
    its `[date time]` stamp so the model sees when every message was sent."""
    out = []
    for h in history:
        stamp = fmt_stamp(h.get("at"))
        out.append({"role": h["role"], "content": f"{stamp} {h['text']}" if stamp else h["text"]})
    return out


def to_transcript(history: list[dict]) -> str:
    """History -> a plain text transcript for the CLI prompt: `[date time] Name: text` per turn
    (named speakers + a date+time stamp; the stamp is dropped when `at` is absent)."""
    lines = []
    for h in history:
        stamp = fmt_stamp(h.get("at"))
        prefix = f"{stamp} " if stamp else ""
        lines.append(f"{prefix}{role_label(h['role'])}: {h['text']}")
    return "\n".join(lines)
