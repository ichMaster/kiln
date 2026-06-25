"""
Контракт містка TUI (ARCHITECTURE §Components/§Contracts): echo-free inbox/outbox.

Ввід іде ЛИШЕ в inbox, рендер — ЛИШЕ в outbox; набраний рядок ніколи не відлунює
в outbox. Неблокуючі читання повертають None на порожнечі (не стопорять цикл тіків).
Без викликів моделі — чистий stdlib-транспорт.
"""

from __future__ import annotations

from tui.bridge import Bridge


def test_input_roundtrip_ui_to_engine():
    b = Bridge()
    b.submit("привіт")
    assert b.poll_input() == "привіт"
    assert b.poll_input() is None  # вичерпано


def test_output_roundtrip_engine_to_ui():
    b = Bridge()
    b.emit({"kind": "agent", "text": "репліка", "is_self": False, "lead": False})
    event = b.poll_output()
    assert event == {"kind": "agent", "text": "репліка", "is_self": False, "lead": False}
    assert b.poll_output() is None


def test_echo_free_input_never_appears_on_outbox():
    """Структурний echo-free: submit() кладе тільки в inbox, не в outbox."""
    b = Bridge()
    b.submit("моє повідомлення")
    assert b.poll_output() is None  # нічого не відлунилось у рендер
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
    assert b.drain_output() == []  # черга порожня після вичерпання


def test_fifo_order_preserved():
    b = Bridge()
    for i in range(5):
        b.submit(f"рядок {i}")
    got = [b.poll_input() for _ in range(5)]
    assert got == [f"рядок {i}" for i in range(5)]
