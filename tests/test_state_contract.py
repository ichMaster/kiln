"""
Contract: state/needs.json = {need: level(0..1)} and survives a load/save round-trip.

Stable state seam (ARCHITECTURE §Contracts). The test is isolated via tmp_path —
it does not touch the repo's real state/. No model calls.
"""

from __future__ import annotations

import json

from kiln.engine import State, load_state, save_state


def test_needs_json_roundtrip(tmp_path):
    """load -> save -> load returns the same needs (round-trip identity)."""
    needs = {"connection": 0.5, "rest": 0.1, "novelty": 0.85, "intensity": 0.0}
    save_state(State(needs=dict(needs)), tmp_path)

    loaded = load_state(tmp_path)

    assert loaded.needs == needs


def test_needs_json_shape_on_disk(tmp_path):
    """The on-disk file is a flat object {need: float in 0..1}."""
    save_state(State(needs={"connection": 0.42, "rest": 0.0}), tmp_path)

    data = json.loads((tmp_path / "needs.json").read_text(encoding="utf-8"))

    assert isinstance(data, dict)
    assert all(isinstance(k, str) for k in data)
    assert all(isinstance(v, (int, float)) and 0.0 <= v <= 1.0 for v in data.values())


def test_load_state_missing_file_is_empty(tmp_path):
    """No needs.json -> empty state (a fresh clone does not crash)."""
    assert load_state(tmp_path).needs == {}
