"""
Command surface contract: handle_command writes through the Output seam (output.notice),
not print — so TUI/web capture the result the same way the console does.

Pins the new signature (with output), output via notice, and the absence of direct prints.
"""

from __future__ import annotations

from kiln.commands import COMMANDS, command_hints, handle_command
from kiln.engine import State
from kiln.output import Output


class RecordingOutput:
    """Output sink that records events instead of printing."""

    def __init__(self):
        self.notices: list[str] = []

    def user(self, text: str) -> None: ...
    def agent(self, text, *, is_self=False, lead=False, model=None) -> None: ...
    def usage(self, usage, latency=None) -> None: ...
    def status(self, snapshot: dict) -> None: ...

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
    assert capsys.readouterr().out == ""  # nothing printed directly (no print)


def test_self_command_toggles_self_messages_and_notifies():
    out = RecordingOutput()
    st = State(needs={})
    assert st.self_messages is True  # default on
    assert handle_command("/self", st, [], "sys", False, out) == "handled"
    assert st.self_messages is False and any("off" in n for n in out.notices)
    handle_command("/self", st, [], "sys", False, out)  # toggles back
    assert st.self_messages is True and any("on" in n for n in out.notices)


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


def test_prompt_shows_system_and_messages():
    out = RecordingOutput()
    history = [{"role": "user", "text": "привіт"}, {"role": "assistant", "text": "вітаю"}]
    action = handle_command("/prompt", State(needs={}), history, "SYSTEM-PROMPT", False, out)
    assert action == "handled"
    joined = "\n".join(out.notices)
    assert "SYSTEM-PROMPT" in joined  # the system prompt is shown
    assert "привіт" in joined and "вітаю" in joined  # both messages are shown
    assert "messages (2)" in joined  # with the count


def test_unknown_command_notifies():
    out = RecordingOutput()
    handle_command("/wat", State(needs={}), [], "sys", False, out)
    assert any("unknown command" in n for n in out.notices)


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


def test_command_hints_match_handled_commands():
    """Every advertised command is actually handled (no phantom hints)."""
    out = RecordingOutput()
    for cmd in COMMANDS:
        action = handle_command(f"/{cmd}", State(needs={}), [], "sys", False, out)
        assert action in ("handled", "quit")  # recognized, not None / unknown
    assert not any("unknown command" in n for n in out.notices)


def test_command_hints_string_lists_all_commands():
    hints = command_hints()
    for cmd in COMMANDS:
        assert f"/{cmd}" in hints
