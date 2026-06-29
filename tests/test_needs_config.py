"""
Needs config: state/needs_model.yaml -> kiln.config constants, with a DEFAULT_NEEDS fallback.

The need MODEL (drift / satiation / triggers + the trigger-wiring scalars) loads from YAML; a
missing / broken file (or no PyYAML) falls back to DEFAULT_NEEDS. No model calls.
"""

from __future__ import annotations

import pytest

import kiln.config as c


def test_load_needs_reads_the_committed_yaml():
    """Structure (not the exact tunable numbers — the operator edits those freely)."""
    cfg = c.load_needs()  # the committed state/needs_model.yaml
    needs = {"connection", "rest", "novelty", "intensity", "reflection", "curiosity"}
    assert set(cfg["need_triggers"]) == needs and set(cfg["drift"]) == needs
    assert cfg["need_triggers"]["connection"]["action"] == "chat"  # structural wiring
    assert "curiosity" in cfg["satiation"]["asked"]  # the question monitor's discharge event
    assert isinstance(cfg["reach_out_models"], list)


def test_load_needs_falls_back_when_missing(tmp_path):
    assert c.load_needs(tmp_path / "nope.yaml") == c.DEFAULT_NEEDS


def test_load_needs_falls_back_on_invalid_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("need_triggers: [: : :", encoding="utf-8")  # invalid YAML
    assert c.load_needs(bad) == c.DEFAULT_NEEDS


def test_build_needs_flattens_and_validates():
    built = c._build_needs(c.DEFAULT_NEEDS)
    assert built["NEED_TRIGGERS"] == c.DEFAULT_NEEDS["need_triggers"]
    assert built["REACH_OUT_MODELS"] == ("intensity", "novelty")  # list -> tuple
    assert isinstance(built["SELF_COOLDOWN"], int) and isinstance(built["REST_WAKE"], float)


def test_build_needs_raises_on_bad_shape():
    with pytest.raises((KeyError, TypeError, ValueError)):
        c._build_needs({"need_triggers": {}})  # missing keys -> raises (caller falls back)


def test_exported_constants_come_from_the_yaml():
    """The config constants reflect the loaded YAML (REACH_OUT_MODELS is a tuple, not a list)."""
    cfg = c.load_needs()
    assert c.NEED_TRIGGERS == cfg["need_triggers"]
    assert c.DRIFT == cfg["drift"]
    assert c.SATIATION == cfg["satiation"]
    assert c.REACH_OUT_MODELS == tuple(cfg["reach_out_models"])
    assert isinstance(c.REACH_OUT_MODELS, tuple)


def test_yaml_and_default_needs_have_the_same_shape():
    """The committed YAML and the DEFAULT_NEEDS fallback must share the same KEYS (so the fallback
    is structurally compatible) — but the VALUES are tunable and may legitimately differ."""
    cfg = c.load_needs()
    assert set(cfg) == set(c.DEFAULT_NEEDS)
    assert set(cfg["need_triggers"]) == set(c.DEFAULT_NEEDS["need_triggers"])
    assert set(cfg["drift"]) == set(c.DEFAULT_NEEDS["drift"])
    assert set(cfg["satiation"]) == set(c.DEFAULT_NEEDS["satiation"])
