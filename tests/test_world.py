"""Unit: world_now — the deterministic 'now' formatter (KILN-031). Pure, zero paid calls."""

from __future__ import annotations

import datetime as dt

import kiln.world as w
from kiln.world import world_now


def _at(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi)


def test_world_now_with_location():
    s = world_now(_at(2026, 6, 28, 11, 52), "Львів")
    assert "Львів" in s
    assert "червня" in s and "2026" in s  # date (Ukrainian month)
    assert "11:52" in s  # time
    assert "Орієнтуйся на час доби" in s  # the rhythm cue is present


def test_world_now_without_location_omits_place():
    s = world_now(_at(2026, 6, 28, 11, 52))
    assert "Львів" not in s and "11:52" in s  # no place, still a clock


def test_time_of_day_bands():
    assert "ранок" in world_now(_at(2026, 6, 28, 8))
    assert "день" in world_now(_at(2026, 6, 28, 13))
    assert "вечір" in world_now(_at(2026, 6, 28, 19))
    assert "ніч" in world_now(_at(2026, 6, 28, 2))  # past midnight
    assert "ніч" in world_now(_at(2026, 6, 28, 23))  # late evening -> ніч band


def test_seasons():
    assert "зима" in world_now(_at(2026, 1, 15, 12))
    assert "весна" in world_now(_at(2026, 4, 15, 12))
    assert "літо" in world_now(_at(2026, 7, 15, 12))
    assert "осінь" in world_now(_at(2026, 10, 15, 12))


def test_weekday_capitalized():
    s = world_now(_at(2026, 6, 28, 12))
    expected = w._WEEKDAYS[dt.datetime(2026, 6, 28).weekday()].capitalize()
    assert s.startswith(expected)


def test_config_knobs_exist():
    import kiln.config as c

    assert isinstance(c.USER_LOCATION, str) and isinstance(c.TIMEZONE, str)  # .env-overridable
    assert isinstance(c.RECENT_MESSAGES, int)


# --- recent_timed (KILN-033) ---

from kiln.history import ROLE_BOT, ROLE_USER, turn  # noqa: E402
from kiln.world import recent_timed  # noqa: E402


def test_recent_timed_same_day_shows_time_only():
    now = _at(2026, 6, 28, 12)
    h = [
        turn(ROLE_USER, "привіт", at="2026-06-28T11:50:00"),
        turn(ROLE_BOT, "вітаю", at="2026-06-28T11:51:00"),
    ]
    out = recent_timed(h, 10, now)
    assert "[11:50] Користувач: привіт" in out
    assert "[11:51] Ти: вітаю" in out
    assert "Сб" not in out and "Нд" not in out  # same day as `now` -> no weekday prefix


def test_recent_timed_day_change_shows_weekday():
    now = _at(2026, 6, 28, 12)  # Sunday
    h = [
        turn(ROLE_USER, "вчора", at="2026-06-27T22:00:00"),  # Saturday
        turn(ROLE_BOT, "сьогодні", at="2026-06-28T09:00:00"),  # Sunday
    ]
    out = recent_timed(h, 10, now)
    assert "[Сб 22:00] Користувач: вчора" in out  # day differs from now -> weekday
    assert "[Нд 09:00] Ти: сьогодні" in out  # day changed from the previous line -> weekday


def test_recent_timed_caps_to_last_n():
    now = _at(2026, 6, 28, 12)
    h = [turn(ROLE_USER, f"m{i}", at="2026-06-28T11:00:00") for i in range(20)]
    out = recent_timed(h, 3, now)
    assert len(out.splitlines()) == 3 and "m19" in out and "m17" in out and "m16" not in out


def test_recent_timed_off_and_empty():
    now = _at(2026, 6, 28, 12)
    assert recent_timed([turn(ROLE_USER, "x", at="2026-06-28T11:00:00")], 0, now) == ""
    assert recent_timed([], 10, now) == ""


def test_recent_timed_missing_at_has_no_timestamp():
    now = _at(2026, 6, 28, 12)
    out = recent_timed([{"role": "user", "text": "без часу"}], 10, now)
    assert out == "Користувач: без часу"  # no [time] prefix when `at` is absent
