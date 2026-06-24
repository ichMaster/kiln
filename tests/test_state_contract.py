"""
Контракт: state/needs.json = {need: рівень(0..1)} і переживає load/save round-trip.

Стабільний seam стану (ARCHITECTURE §Contracts). Тест ізольований через tmp_path —
не чіпає реальний state/ репозиторію. Без викликів моделі.
"""

from __future__ import annotations

import json

from kiln.engine import State, load_state, save_state


def test_needs_json_roundtrip(tmp_path):
    """load -> save -> load повертає ті самі потреби (round-trip identity)."""
    needs = {"connection": 0.5, "rest": 0.1, "novelty": 0.85, "intensity": 0.0}
    save_state(State(needs=dict(needs)), tmp_path)

    loaded = load_state(tmp_path)

    assert loaded.needs == needs


def test_needs_json_shape_on_disk(tmp_path):
    """Файл на диску — це плаский об'єкт {need: float у 0..1}."""
    save_state(State(needs={"connection": 0.42, "rest": 0.0}), tmp_path)

    data = json.loads((tmp_path / "needs.json").read_text(encoding="utf-8"))

    assert isinstance(data, dict)
    assert all(isinstance(k, str) for k in data)
    assert all(isinstance(v, (int, float)) and 0.0 <= v <= 1.0 for v in data.values())


def test_load_state_missing_file_is_empty(tmp_path):
    """Нема needs.json -> порожній стан (свіжий клон не падає)."""
    assert load_state(tmp_path).needs == {}
