"""
KILN-062 — the event queue + producers (kiln/fsm.py). Pure, no engine wired up, no paid calls: the
producers take the already-computed poll result / crossed need / rotate flag, and the queue drains
one event per tick. These tests pin that producers emit the right events and that the drain order
reproduces v1.2's `input > self-trigger > idle` selection.
"""

from kiln.fsm import (
    EVENT_PRIORITY,
    EventKind,
    EventQueue,
    gather_events,
    input_event,
    rotate_event,
    self_trigger_event,
    tick_event,
)

# --- producers ----------------------------------------------------------------


def test_input_event_none_when_no_input():
    assert input_event(None) is None


def test_input_event_plain_text_is_user_message():
    e = input_event("привіт")
    assert (e.kind, e.payload) == (EventKind.USER_MESSAGE, "привіт")


def test_input_event_slash_line_is_command():
    e = input_event("/status")
    assert (e.kind, e.payload) == (EventKind.COMMAND, "/status")


def test_self_trigger_event_none_when_neither_fires():
    assert self_trigger_event(None, None) is None


def test_self_trigger_event_reach_out_carries_the_need():
    e = self_trigger_event("connection", None)
    assert (e.kind, e.payload) == (EventKind.SELF_TRIGGER, "connection")


def test_self_trigger_event_thought_when_no_reach_out():
    e = self_trigger_event(None, "reflection")
    assert (e.kind, e.payload) == (EventKind.SELF_TRIGGER, "reflection")


def test_self_trigger_event_reach_out_precedes_thought():
    # v1.2 computes the thought only when no reach-out fired; if both are handed in, reach-out wins
    e = self_trigger_event("connection", "reflection")
    assert e.payload == "connection"


def test_rotate_event_only_when_due():
    assert rotate_event(False) is None
    assert rotate_event(True).kind == EventKind.ROTATE


def test_tick_event_always():
    assert tick_event().kind == EventKind.TICK


# --- the queue: one event per tick, by priority ------------------------------


def test_none_producer_results_are_dropped():
    q = EventQueue()
    q.put(None)
    q.put(input_event(None))
    q.put(tick_event())
    assert q.pending() == 1


def test_empty_queue_defensively_yields_a_tick():
    assert EventQueue().drain_one().kind == EventKind.TICK


def test_drain_prefers_input_over_self_trigger_and_tick():
    # input + a self-trigger in one tick → INPUT is acted on (self-trigger re-evaluated next tick)
    q = gather_events(EventQueue(), "hi", "connection", None, do_rotate=False)
    assert q.drain_one().kind == EventKind.USER_MESSAGE


def test_drain_prefers_self_trigger_over_tick_when_no_input():
    q = gather_events(EventQueue(), None, "connection", None, do_rotate=False)
    won = q.drain_one()
    assert (won.kind, won.payload) == (EventKind.SELF_TRIGGER, "connection")


def test_drain_falls_through_to_tick_on_a_silent_tick():
    q = gather_events(EventQueue(), None, None, None, do_rotate=False)
    assert q.drain_one().kind == EventKind.TICK


def test_drain_rotate_beats_tick_but_loses_to_input():
    # an otherwise-idle tick with a rotation due → rotate wins
    assert gather_events(EventQueue(), None, None, None, True).drain_one().kind == EventKind.ROTATE
    # but a turn this tick preempts it (rotation is orthogonal; 064 keeps it byte-for-byte)
    assert gather_events(EventQueue(), "hi", None, None, True).drain_one().kind == (
        EventKind.USER_MESSAGE
    )


def test_command_input_also_preempts_a_self_trigger():
    q = gather_events(EventQueue(), "/status", "connection", None, do_rotate=False)
    assert q.drain_one().kind == EventKind.COMMAND


def test_drain_one_clears_the_queue():
    q = gather_events(EventQueue(), "hi", "connection", None, do_rotate=True)
    q.drain_one()
    assert q.pending() == 0


def test_priority_order_matches_the_v12_selection():
    # input tier < self-trigger < rotate < tick (lower = wins)
    assert (
        EVENT_PRIORITY[EventKind.USER_MESSAGE]
        == EVENT_PRIORITY[EventKind.COMMAND]
        < EVENT_PRIORITY[EventKind.SELF_TRIGGER]
        < EVENT_PRIORITY[EventKind.ROTATE]
        < EVENT_PRIORITY[EventKind.TICK]
    )
