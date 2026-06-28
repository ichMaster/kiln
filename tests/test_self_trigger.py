"""Unit: the reach-out — only connection self-triggers; intensity/novelty pick the brain."""

from __future__ import annotations

from kiln.config import SELF_COOLDOWN, THOUGHT_COOLDOWN
from kiln.engine import (
    State,
    TriggerBook,
    reach_out_branch,
    select_self_trigger,
    select_thought_trigger,
)

# --- select_self_trigger: ONLY connection fires (hysteresis + cooldown) ------


def test_no_trigger_below_connection_threshold():
    assert select_self_trigger(State(needs={"connection": 0.5}), TriggerBook()) is None


def test_fires_on_connection_upward_crossing():
    assert select_self_trigger(State(needs={"connection": 0.85}), TriggerBook()) == "connection"


def test_only_connection_self_triggers():
    # novelty/intensity over their own thresholds do NOT self-initiate
    st = State(needs={"connection": 0.0, "novelty": 0.95, "intensity": 0.95})
    assert select_self_trigger(st, TriggerBook()) is None


def test_hysteresis_one_fire_while_staying_above():
    tg = TriggerBook()
    st = State(needs={"connection": 0.85})  # above threshold 0.80
    assert select_self_trigger(st, tg) == "connection"  # fired
    assert select_self_trigger(st, tg) is None  # hysteresis discharged
    assert select_self_trigger(st, tg) is None


def test_hysteresis_rearms_after_drop_below():
    tg = TriggerBook()
    st = State(needs={"connection": 0.85})
    assert select_self_trigger(st, tg) == "connection"
    st.needs["connection"] = 0.50  # dropped below -> re-arm
    for _ in range(SELF_COOLDOWN + 1):  # also let the cooldown drain
        select_self_trigger(st, tg)
    st.needs["connection"] = 0.85  # second upward crossing
    assert select_self_trigger(st, tg) == "connection"


def test_reflection_does_not_self_trigger_a_message():
    # reflection over its threshold is NOT a reach-out (select_self_trigger ignores it)
    st = State(needs={"connection": 0.0, "reflection": 0.95})
    assert select_self_trigger(st, TriggerBook()) is None


# --- select_thought_trigger: reflection fires the inner monologue (KILN-040) ---


def test_no_thought_below_reflection_threshold():
    assert select_thought_trigger(State(needs={"reflection": 0.5}), TriggerBook()) is None


def test_thought_fires_on_reflection_upward_crossing():
    # NEED_TRIGGERS["reflection"]["threshold"] == 0.60
    assert select_thought_trigger(State(needs={"reflection": 0.7}), TriggerBook()) == "reflection"


def test_thought_hysteresis_and_rearm():
    tg = TriggerBook()
    st = State(needs={"reflection": 0.7})
    assert select_thought_trigger(st, tg) == "reflection"  # fired
    assert select_thought_trigger(st, tg) is None  # hysteresis discharged
    st.needs["reflection"] = 0.3  # drop below threshold -> re-arm
    for _ in range(THOUGHT_COOLDOWN + 1):  # also drain the cooldown
        select_thought_trigger(st, tg)
    st.needs["reflection"] = 0.7  # second upward crossing
    assert select_thought_trigger(st, tg) == "reflection"


def test_cooldown_blocks_refire_until_drained():
    tg = TriggerBook()
    st = State(needs={"connection": 0.85})
    assert select_self_trigger(st, tg) == "connection"  # fired, cooldown = SELF_COOLDOWN
    st.needs["connection"] = 0.0
    assert select_self_trigger(st, tg) is None  # re-armed, cooldown ticking down
    st.needs["connection"] = 0.85  # above threshold again, but cooldown still running
    blocked = [select_self_trigger(st, tg) for _ in range(SELF_COOLDOWN - 2)]
    assert all(x is None for x in blocked)  # silence until the cooldown drains
    assert select_self_trigger(st, tg) == "connection"  # cooldown = 0 -> fired again


# --- reach_out_branch: intensity/novelty shape WHICH brain answers ----------


def test_reach_out_chat_when_calm():
    # neither intensity nor novelty over threshold -> connection's baseline (chat)
    assert reach_out_branch(State(needs={"connection": 0.85})) == ("chat", None)


def test_reach_out_deep_when_intensity_high():
    st = State(needs={"connection": 0.85, "intensity": 0.80})  # intensity >= 0.75
    assert reach_out_branch(st) == ("deep", None)


def test_reach_out_session_wiki_when_novelty_high():
    st = State(needs={"connection": 0.85, "novelty": 0.90})  # novelty >= 0.85, intensity low
    assert reach_out_branch(st) == ("tool", "session-wiki")


def test_reach_out_intensity_wins_over_novelty():
    # both high -> intensity has priority (REACH_OUT_MODELS order)
    st = State(needs={"connection": 0.85, "intensity": 0.80, "novelty": 0.95})
    assert reach_out_branch(st) == ("deep", None)
