"""
kiln — world & temporal awareness (v0.8): pure formatters for "now" and the recent timeline.

`world_now(now, location)` renders the current moment as a short Ukrainian paragraph — weekday +
date, time + time-of-day, season, location, and a rhythm cue — for the `## Зараз` system-prompt
section (composed into the prompt by `build_system`, KILN-034). Pure: the clock is **injected**,
so the output is deterministic and unit-testable (no `datetime.now()` inside).
"""

from __future__ import annotations

import datetime as _dt

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
