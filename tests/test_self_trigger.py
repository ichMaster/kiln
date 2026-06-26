"""Unit: self-triggers — hysteresis (one fire per crossing) + cooldown."""

from __future__ import annotations

from kiln.config import SELF_COOLDOWN
from kiln.engine import State, TriggerBook, select_self_trigger


def test_no_trigger_below_threshold():
    assert select_self_trigger(State(needs={"novelty": 0.5}), TriggerBook()) == (None, None)


def test_fires_on_upward_crossing_with_its_action():
    # novelty thr 0.85 -> action 'tool' (delegates to the session-wiki sub-agent)
    fired = select_self_trigger(State(needs={"novelty": 0.90}), TriggerBook())
    assert fired == ("novelty", "tool")


def test_hysteresis_one_fire_while_staying_above():
    tg = TriggerBook()
    st = State(needs={"intensity": 0.80})  # above threshold 0.75
    assert select_self_trigger(st, tg)[0] == "intensity"  # fired
    # stays above threshold -> does not fire again (hysteresis discharged)
    assert select_self_trigger(st, tg) == (None, None)
    assert select_self_trigger(st, tg) == (None, None)


def test_hysteresis_rearms_after_drop_below():
    tg = TriggerBook()
    st = State(needs={"connection": 0.85})  # above 0.80 -> action 'chat'
    assert select_self_trigger(st, tg) == ("connection", "chat")
    st.needs["connection"] = 0.50  # dropped below -> re-arm
    for _ in range(SELF_COOLDOWN + 1):  # also let the cooldown drain
        select_self_trigger(st, tg)
    st.needs["connection"] = 0.85  # second upward crossing
    assert select_self_trigger(st, tg) == ("connection", "chat")


def test_cooldown_blocks_refire_until_drained():
    tg = TriggerBook()
    st = State(needs={"novelty": 0.90})
    assert select_self_trigger(st, tg)[0] == "novelty"  # fired, cooldown = SELF_COOLDOWN
    st.needs["novelty"] = 0.0
    assert select_self_trigger(st, tg) == (None, None)  # re-armed, cooldown ticking down
    st.needs["novelty"] = 0.90  # above threshold again, but cooldown still running
    blocked = [select_self_trigger(st, tg)[0] for _ in range(SELF_COOLDOWN - 2)]
    assert all(x is None for x in blocked)  # silence until the cooldown drains
    assert select_self_trigger(st, tg)[0] == "novelty"  # cooldown = 0 -> fired again


def test_largest_overshoot_fires_first():
    tg = TriggerBook()
    # intensity overshoots by 0.20 (0.95-0.75); novelty by 0.05 (0.90-0.85)
    st = State(needs={"intensity": 0.95, "novelty": 0.90})
    assert select_self_trigger(st, tg)[0] == "intensity"
