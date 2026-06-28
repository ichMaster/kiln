"""
kiln — conversation history: the shared message feed of a session.

No trimming or summarization: we accumulate everything and append it to the prompt.
Each item is {"role": "user"|"assistant", "text": ..., "at": "<ISO timestamp>"} (v0.8). In the live
conversation, the `[date time]` stamp (`fmt_stamp`) is prepended to **user** messages only — the
chat model mirrors a stamp on its OWN role, so assistant turns stay clean (any echoed stamp is
removed by `strip_leading_stamp`). The prior-session timeline (`world.recent_timed`) stamps every
line.
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


def strip_leading_name(text: str) -> str:
    """Drop a leading `**Агніка:**` / `Агніка:` the model echoed into a reply — it mirrors the
    timeline's `Name:` labels. Only her own AGENT_NAME (never the user's); bold markers optional.
    Built at call time so it respects the current AGENT_NAME."""
    pat = rf"^\s*\*{{0,2}}\s*{re.escape(AGENT_NAME)}\s*\*{{0,2}}\s*:\s*\*{{0,2}}\s*"
    return re.sub(pat, "", text or "")


def turn(role: str, text: str, at: str | None = None) -> dict:
    """A conversation turn: {role, text, at}. `at` is an ISO timestamp (seconds); when None it
    is stamped with the current local time (the source of `at` for the timeline, KILN-033)."""
    return {
        "role": role,
        "text": text,
        "at": at or _dt.datetime.now().isoformat(timespec="seconds"),
    }


def _user_prefix(h: dict) -> str:
    """`[date time] ` for a USER turn (only user turns are stamped — the model mirrors a stamp on
    its own role); "" otherwise."""
    if h.get("role") == ROLE_USER:
        stamp = fmt_stamp(h.get("at"))
        return f"{stamp} " if stamp else ""
    return ""


def _clean(h: dict) -> str:
    """Turn text — echoed stamp + name label stripped from ASSISTANT turns (user text left as
    typed)."""
    if h.get("role") == ROLE_USER:
        return h["text"]
    return strip_leading_name(strip_leading_stamp(h["text"]))


def to_messages(history: list[dict]) -> list[dict]:
    """History -> Anthropic Messages API format ({role, content}); user content carries its
    `[date time]` stamp, assistant content stays clean."""
    return [{"role": h["role"], "content": f"{_user_prefix(h)}{_clean(h)}"} for h in history]


def to_transcript(history: list[dict]) -> str:
    """History -> a plain text transcript: `[date time] Користувач: …` for user turns, `Агніка: …`
    (clean) for the bot — the stamp precedes the named speaker (`role_label`)."""
    return "\n".join(f"{_user_prefix(h)}{role_label(h['role'])}: {_clean(h)}" for h in history)
