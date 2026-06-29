"""
Needs config: state/needs_model.yaml -> kiln.config constants, with a DEFAULT_NEEDS fallback.

The need MODEL (drift / satiation / triggers + the trigger-wiring scalars) loads from YAML; a
missing / broken file (or no PyYAML) falls back to DEFAULT_NEEDS. No model calls.
"""

from __future__ import annotations

import pytest

import kiln.config as c


def test_load_needs_reads_the_committed_yaml():
    cfg = c.load_needs()  # the committed state/needs_model.yaml
    assert cfg["need_triggers"]["connection"] == {"threshold": 0.80, "action": "chat"}
    assert cfg["drift"]["curiosity"] == 0.002
    assert cfg["satiation"]["asked"] == {"curiosity": -0.4}
    assert cfg["reach_out_models"] == ["intensity", "novelty"]


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


def test_committed_yaml_stays_in_sync_with_default_needs():
    """The shipped needs_model.yaml must match the DEFAULT_NEEDS fallback — edit both together."""
    cfg = c.load_needs()
    assert cfg["need_triggers"] == c.DEFAULT_NEEDS["need_triggers"]
    assert cfg["drift"] == c.DEFAULT_NEEDS["drift"]
    assert cfg["satiation"] == c.DEFAULT_NEEDS["satiation"]
    for k in ("reach_out_need", "reflect_need", "self_cooldown", "thought_cooldown", "rest_wake"):
        assert cfg[k] == c.DEFAULT_NEEDS[k]
    assert tuple(cfg["reach_out_models"]) == tuple(c.DEFAULT_NEEDS["reach_out_models"])
