"""
TUI bridge contract (ARCHITECTURE §Components/§Contracts): echo-free inbox/outbox.

Input goes ONLY to the inbox, rendering ONLY to the outbox; a typed line never echoes
into the outbox. Non-blocking reads return None when empty (so they don't stall the tick loop).
No model calls — pure stdlib transport.
"""

from __future__ import annotations

from tui.bridge import Bridge


def test_input_roundtrip_ui_to_engine():
    b = Bridge()
    b.submit("привіт")
    assert b.poll_input() == "привіт"
    assert b.poll_input() is None  # exhausted


def test_output_roundtrip_engine_to_ui():
    b = Bridge()
    b.emit({"kind": "agent", "text": "репліка", "is_self": False, "lead": False})
    event = b.poll_output()
    assert event == {"kind": "agent", "text": "репліка", "is_self": False, "lead": False}
    assert b.poll_output() is None


def test_echo_free_input_never_appears_on_outbox():
    """Structural echo-free: submit() puts only into the inbox, not the outbox."""
    b = Bridge()
    b.submit("моє повідомлення")
    assert b.poll_output() is None  # nothing echoed into the render
    assert b.poll_input() == "моє повідомлення"


def test_nonblocking_reads_return_none_when_idle():
    b = Bridge()
    assert b.poll_input() is None
    assert b.poll_output() is None
    assert b.drain_output() == []


def test_drain_output_returns_all_events_in_order():
    b = Bridge()
    b.emit({"kind": "user", "text": "ти"})
    b.emit({"kind": "agent", "text": "я", "is_self": False, "lead": False})
    b.emit({"kind": "usage", "usage": None})
    drained = b.drain_output()
    assert [e["kind"] for e in drained] == ["user", "agent", "usage"]
    assert b.drain_output() == []  # queue empty after draining


def test_fifo_order_preserved():
    b = Bridge()
    for i in range(5):
        b.submit(f"рядок {i}")
    got = [b.poll_input() for _ in range(5)]
    assert got == [f"рядок {i}" for i in range(5)]
