"""
kiln — world & temporal awareness (v0.8): pure formatters for "now" and the recent timeline.

`world_now(now, location)` renders the current moment as a short Ukrainian paragraph — weekday +
date, time + time-of-day, season, location, and a rhythm cue — for the `## Зараз` system-prompt
section (composed into the prompt by `build_system`, KILN-034). Pure: the clock is **injected**,
so the output is deterministic and unit-testable (no `datetime.now()` inside).
"""

from __future__ import annotations

import datetime as _dt

from .history import fmt_stamp, role_label, strip_leading_name, strip_leading_stamp

# Ukrainian names (persona layer). Weekday index matches datetime.weekday() (Mon=0).
_WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]
_MONTHS = [
    "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]  # fmt: skip

# Tone cue per time-of-day band — shapes how her reply should feel at this hour.
_RHYTHM = {
    "ранок": "вранці тон бадьоріший, легший",
    "день": "вдень — спокійний, робочий ритм",
    "вечір": "ввечері тепліше й повільніше",
    "ніч": "вночі тихо й інтимно, не поспішай",
}


def _part_of_day(hour: int) -> str:
    """Ukrainian time-of-day band for an hour (0–23)."""
    if 5 <= hour < 11:
        return "ранок"
    if 11 <= hour < 17:
        return "день"
    if 17 <= hour < 22:
        return "вечір"
    return "ніч"  # 22–04


def _season(month: int) -> str:
    """Ukrainian season for a month (1–12)."""
    if month in (12, 1, 2):
        return "зима"
    if month in (3, 4, 5):
        return "весна"
    if month in (6, 7, 8):
        return "літо"
    return "осінь"  # 9–11


def world_now(now: _dt.datetime, location: str = "") -> str:
    """A short Ukrainian "now" paragraph: weekday, date, time + time-of-day, season, location,
    and a one-line rhythm cue. Pure — `now` is injected; `location` is omitted when empty."""
    part = _part_of_day(now.hour)
    weekday = _WEEKDAYS[now.weekday()].capitalize()
    date = f"{now.day} {_MONTHS[now.month - 1]} {now.year}"
    head = f"{weekday}, {date}, {now:%H:%M} — {part}, {_season(now.month)}."
    if location.strip():
        head += f" {location.strip()}."
    return f"{head}\nОрієнтуйся на час доби: {_RHYTHM[part]}."


def recent_timed(history: list[dict], n: int) -> str:
    """The last `n` turns as a timestamped timeline — `[Сб 28.06.2026 11:52] Віталік: …`. Every
    line carries the full date+time (`fmt_stamp`); turns without a parseable `at` show no stamp.
    Empty / `n <= 0` → ""."""
    if n <= 0 or not history:
        return ""
    lines = []
    for t in history[-n:]:
        who = role_label(t.get("role") or "?")
        # drop any echoed stamp + name label baked into the stored reply
        text = strip_leading_name(strip_leading_stamp((t.get("text") or "").strip()))
        stamp = fmt_stamp(t.get("at"))
        prefix = f"{stamp} " if stamp else ""
        lines.append(f"{prefix}{who}: {text}")
    return "\n".join(lines)


def world_block(now: _dt.datetime, location: str, history: list[dict], n: int) -> str:
    """The full world block for the system prompt: the `## Зараз` paragraph + the
    `## Повідомлення з минулої сесії` timeline. Each section is omitted when empty (the timeline
    is empty when there's no prior session); `""` when both are. Pure (now injected)."""
    parts = []
    nowtext = world_now(now, location)
    if nowtext.strip():
        parts.append("## Зараз\n" + nowtext)
    timed = recent_timed(history, n)
    if timed.strip():
        parts.append("## Повідомлення з минулої сесії\n" + timed)
    return "\n\n".join(parts)
