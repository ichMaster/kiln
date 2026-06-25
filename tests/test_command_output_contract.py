"""
Контракт командного surface: handle_command пише через seam Output (output.notice),
а не print — тож TUI/веб ловлять результат так само, як консоль.

Пінимо нову сигнатуру (з output), вивід через notice і відсутність прямих print.
"""

from __future__ import annotations

from kiln.commands import handle_command
from kiln.engine import State
from kiln.output import Output


class RecordingOutput:
    """Сінк Output, що записує події замість друку."""

    def __init__(self):
        self.notices: list[str] = []

    def user(self, text: str) -> None: ...
    def agent(self, text: str, *, is_self: bool = False, lead: bool = False) -> None: ...
    def usage(self, usage) -> None: ...

    def notice(self, text: str) -> None:
        self.notices.append(text)


def test_recording_output_satisfies_protocol():
    assert isinstance(RecordingOutput(), Output)


def test_status_emits_via_notice_not_print(capsys):
    out = RecordingOutput()
    action = handle_command(
        "/status", State(needs={"connection": 0.5, "rest": 0.2}), [], "sys", False, out
    )
    assert action == "handled"
    assert any("[status]" in n for n in out.notices)
    assert capsys.readouterr().out == ""  # нічого не друкувалось напряму (no print)


def test_needs_emits_via_notice():
    out = RecordingOutput()
    handle_command("/needs", State(needs={"connection": 0.5}), [], "sys", False, out)
    assert any("[needs]" in n for n in out.notices)


def test_help_lists_commands():
    out = RecordingOutput()
    handle_command("/help", State(needs={}), [], "sys", False, out)
    assert any("/status" in n for n in out.notices)


def test_clear_clears_history_and_notifies():
    out = RecordingOutput()
    history = [{"role": "user", "text": "x"}]
    handle_command("/clear", State(needs={}), history, "sys", False, out)
    assert history == []
    assert any("[clear]" in n for n in out.notices)


def test_unknown_command_notifies():
    out = RecordingOutput()
    handle_command("/wat", State(needs={}), [], "sys", False, out)
    assert any("невідома команда" in n for n in out.notices)


def test_non_command_returns_none():
    assert handle_command("привіт", State(needs={}), [], "sys", False, RecordingOutput()) is None


def test_quit_returns_quit():
    assert handle_command("/quit", State(needs={}), [], "sys", False, RecordingOutput()) == "quit"


def test_ask_returns_tuple():
    assert handle_command("/ask питання", State(needs={}), [], "sys", False, RecordingOutput()) == (
        "ask",
        "питання",
    )


def test_ask_without_arg_notifies():
    out = RecordingOutput()
    action = handle_command("/ask", State(needs={}), [], "sys", False, out)
    assert action == "handled"
    assert any("[ask]" in n for n in out.notices)
