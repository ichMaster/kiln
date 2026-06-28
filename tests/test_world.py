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
