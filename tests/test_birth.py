"""Unit: load_birth — Agnika's birthday from the canon / AGENT_BIRTH (KILN-037). Zero paid calls."""

from __future__ import annotations

import datetime as dt

import kiln.memory as m
from kiln.memory import DEFAULT_BIRTH, load_birth

CANON = "## Натальні дані\nНародження: 12.08.2001, 17:10, Львів.\nСонце Лева · ASC Стрілець"


def test_load_birth_parses_canon_natal_line(monkeypatch):
    monkeypatch.setattr(m, "AGENT_BIRTH", "")  # no override -> parse the canon
    assert load_birth(CANON) == dt.datetime(2001, 8, 12, 17, 10)


def test_load_birth_date_only_is_midnight(monkeypatch):
    monkeypatch.setattr(m, "AGENT_BIRTH", "")
    assert load_birth("Народження: 1.2.1990, десь там") == dt.datetime(1990, 2, 1, 0, 0)


def test_load_birth_falls_back_when_absent(monkeypatch):
    monkeypatch.setattr(m, "AGENT_BIRTH", "")
    assert load_birth("канон без дати") == DEFAULT_BIRTH


def test_load_birth_falls_back_on_invalid_date(monkeypatch):
    monkeypatch.setattr(m, "AGENT_BIRTH", "")
    assert load_birth("Народження: 99.99.2001") == DEFAULT_BIRTH  # matches regex but invalid date


def test_agent_birth_env_overrides_canon(monkeypatch):
    monkeypatch.setattr(m, "AGENT_BIRTH", "01.01.2000 08:30")
    assert load_birth(CANON) == dt.datetime(2000, 1, 1, 8, 30)  # override wins over the canon line
