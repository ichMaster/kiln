"""Unit: self-тригери — гістерезис (одне спрацювання на перетин) + кулдаун."""

from __future__ import annotations

from kiln.config import SELF_COOLDOWN
from kiln.engine import State, TriggerBook, select_self_trigger


def test_no_trigger_below_threshold():
    assert select_self_trigger(State(needs={"novelty": 0.5}), TriggerBook()) == (None, None)


def test_fires_on_upward_crossing_with_its_action():
    # novelty thr 0.85 -> дія 'deep'
    fired = select_self_trigger(State(needs={"novelty": 0.90}), TriggerBook())
    assert fired == ("novelty", "deep")


def test_hysteresis_one_fire_while_staying_above():
    tg = TriggerBook()
    st = State(needs={"intensity": 0.80})  # над порогом 0.75
    assert select_self_trigger(st, tg)[0] == "intensity"  # спрацювало
    # лишається над порогом -> більше не спрацьовує (розряджений гістерезис)
    assert select_self_trigger(st, tg) == (None, None)
    assert select_self_trigger(st, tg) == (None, None)


def test_hysteresis_rearms_after_drop_below():
    tg = TriggerBook()
    st = State(needs={"connection": 0.85})  # над 0.80 -> дія 'chat'
    assert select_self_trigger(st, tg) == ("connection", "chat")
    st.needs["connection"] = 0.50  # впало нижче -> переозброєння
    for _ in range(SELF_COOLDOWN + 1):  # заодно даємо кулдауну стекти
        select_self_trigger(st, tg)
    st.needs["connection"] = 0.85  # перетин угору вдруге
    assert select_self_trigger(st, tg) == ("connection", "chat")


def test_cooldown_blocks_refire_until_drained():
    tg = TriggerBook()
    st = State(needs={"novelty": 0.90})
    assert select_self_trigger(st, tg)[0] == "novelty"  # спрацювало, кулдаун = SELF_COOLDOWN
    st.needs["novelty"] = 0.0
    assert select_self_trigger(st, tg) == (None, None)  # переозброїлось, кулдаун тікає
    st.needs["novelty"] = 0.90  # знову над порогом, але кулдаун ще йде
    blocked = [select_self_trigger(st, tg)[0] for _ in range(SELF_COOLDOWN - 2)]
    assert all(x is None for x in blocked)  # тиша, поки кулдаун не стік
    assert select_self_trigger(st, tg)[0] == "novelty"  # кулдаун = 0 -> спрацювало знову


def test_largest_overshoot_fires_first():
    tg = TriggerBook()
    # intensity перевищує на 0.20 (0.95-0.75); novelty — на 0.05 (0.90-0.85)
    st = State(needs={"intensity": 0.95, "novelty": 0.90})
    assert select_self_trigger(st, tg)[0] == "intensity"
