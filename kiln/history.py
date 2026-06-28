"""
kiln — conversation history: the shared message feed of a session.

No trimming or summarization: we accumulate everything and append it to the prompt.
Each item is {"role": "user"|"assistant", "text": ..., "at": "<ISO timestamp>"} (v0.8). Date+time
stamps appear ONLY in the prior-session timeline (`world.recent_timed`, via `fmt_stamp`) — NOT in
the live conversation (`to_messages`/`to_transcript`), because the chat model mirrors a per-message
`[time]` prefix into its own replies. `strip_leading_stamp` cleans any such echo from stored text.
"""

from __future__ import annotations

import datetime as _dt
import re

from .config import AGENT_NAME, USER_NAME

ROLE_USER = "user"
ROLE_BOT = "assistant"

# short Ukrainian weekday names (index matches datetime.weekday(), Mon=0)
_WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
# one or more leading `[… HH:MM …]` stamps the model may have echoed into a reply
_ECHOED_STAMP = re.compile(r"^(?:\s*\[[^\]]*\d{1,2}:\d{2}[^\]]*\]\s*)+")


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


def strip_leading_stamp(text: str) -> str:
    """Drop any leading `[… time …]` stamp the model echoed into a reply (so old echoes don't
    show in the live conversation or the timeline). Leaves stamp-free text untouched."""
    return _ECHOED_STAMP.sub("", text or "").lstrip()


def turn(role: str, text: str, at: str | None = None) -> dict:
    """A conversation turn: {role, text, at}. `at` is an ISO timestamp (seconds); when None it
    is stamped with the current local time (the source of `at` for the timeline, KILN-033)."""
    return {
        "role": role,
        "text": text,
        "at": at or _dt.datetime.now().isoformat(timespec="seconds"),
    }


def to_messages(history: list[dict]) -> list[dict]:
    """History -> Anthropic Messages API format ({role, content}). NO timestamps in the live
    conversation (the model would mirror them); any echoed stamp is stripped from the text."""
    return [{"role": h["role"], "content": strip_leading_stamp(h["text"])} for h in history]


def to_transcript(history: list[dict]) -> str:
    """History -> a plain text transcript for the CLI prompt: `Name: text` per turn (named
    speakers; no timestamps in the live conversation; any echoed stamp is stripped)."""
    return "\n".join(f"{role_label(h['role'])}: {strip_leading_stamp(h['text'])}" for h in history)
