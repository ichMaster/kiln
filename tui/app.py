"""
kiln.tui.app — Textual application on top of the engine.

A scrollable reply log + a fixed input line. The engine (engine.run) runs in a BACKGROUND
thread and talks to the UI only through the bridge: TuiChannel reads typed lines from inbox,
TuiOutput places render events in outbox, and the app drains them on a timer and draws them.
So the tick loop lives in parallel with the UI (self-triggers work too), and model calls
do not freeze the interface.

Echo-free: the user sees the typed text immediately (the UI writes `you: …` itself), while
the engine does NOT echo input — it writes only its own replies to the log.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading

from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Footer, Header, RichLog, Static, TextArea

from kiln.commands import command_hints
from kiln.engine import run

from .bridge import Bridge
from .channel import TuiChannel
from .output import TuiOutput
from .render import agent_label, needs_panel_lines, status_line1, status_line2

# Line colors (Rich markup). Names are bold; reply/message bodies stay default (white).
_USER_STYLE = "bold cyan"  # "you" name
_BOT_STYLE = "bold green"  # "Agnika" name
_SELF_STYLE = "bold green"  # "Agnika (self)" name — same weight, marked by the (self) suffix
_MODEL_STYLE = "dark_green"  # the (model) tag next to the name
_NOTICE_STYLE = "grey50"  # notices / system lines + command output — grey


def _clipboard_argv() -> list[str] | None:
    """The platform's clipboard-write command (stdin → clipboard), or None if none is available."""
    if sys.platform == "darwin":
        return ["pbcopy"]
    if sys.platform == "win32":
        return ["clip"]
    for argv in (
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["wl-copy"],
    ):
        if shutil.which(argv[0]):
            return argv
    return None


def system_clipboard_copy(text: str) -> bool:
    """Best-effort copy to the OS clipboard via the platform tool (pbcopy/clip/xclip/xsel/wl-copy).
    Returns True on success. Complements Textual's OSC-52 copy, which Terminal.app ignores."""
    argv = _clipboard_argv()
    if not argv:
        return False
    try:
        subprocess.run(argv, input=text.encode("utf-8"), check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


class ChatInput(TextArea):
    """Multi-line chat input: Enter submits, Shift+Enter inserts a newline.

    Sized to ~3 lines (scrolls when longer/pasted) — the Lumi-style prompt box.
    """

    class Submitted(Message):
        """Posted when the user submits the input (Enter)."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)


class KilnApp(App):
    """Thin client: log + input line, wired to the engine through the bridge."""

    TITLE = "kiln — Agnika"

    CSS = """
    #status, #stats { height: 1; padding: 0 2; background: $panel; color: $text-muted; }
    #needspanel {
        height: auto; padding: 0 1; margin: 1 1 1 1;  /* bottom 1 = empty line before the chat */
        border: round $primary-darken-3; color: $text-muted;
    }
    RichLog { height: 1fr; padding: 0 1; }
    #prompt {
        /* Not docked: in the flow, RichLog (1fr) pushes it to the bottom above the
           Footer. Docking made margins offset-only, overflowing the right border. */
        height: 5;          /* border (2) + ~3 text lines; scrolls if longer */
        border: round $accent;
        margin: 1 1 1 1;    /* t r b l — one blank line above (chat) and below (footer) */
    }
    """
    # priority=True so these fire even while the ChatInput (TextArea) is focused — the TextArea
    # otherwise binds ctrl+y (redo) and ctrl+l, swallowing them before they reach the app.
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+y", "copy_reply", "Copy reply", priority=True),
        Binding("ctrl+o", "copy_all", "Copy all", priority=True),
        Binding("ctrl+l", "clear_log", "Clear", priority=True),
    ]

    def __init__(
        self,
        *,
        bridge: Bridge | None = None,
        live: bool = True,
        brain=None,
        start_engine: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bridge = bridge or Bridge()
        self._live = live
        self._brain = brain
        self._start_engine = start_engine
        self._engine_thread: threading.Thread | None = None
        self._last_reply = ""  # for Ctrl+Y (copy last reply)
        self._transcript: list[str] = []  # plain-text mirror of the log (for Ctrl+O)

    def compose(self) -> ComposeResult:
        yield Header()  # standard top bar: title + command palette (Lumi-style, no clock)
        yield Static("status: starting…", id="status")
        yield Static("stats: …", id="stats")
        needs = Static("needs: …", id="needspanel")
        needs.border_title = "Needs"
        needs.border_subtitle = "level / threshold · * hottest · ! over · cdN cooldown"
        yield needs
        yield RichLog(markup=True, wrap=True, highlight=False)
        prompt = ChatInput(id="prompt", show_line_numbers=False, soft_wrap=True)
        prompt.border_title = "You"
        prompt.border_subtitle = "Enter — send · Shift+Enter — newline · " + command_hints()
        yield prompt
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt", ChatInput).focus()
        if self._start_engine:
            self._engine_thread = threading.Thread(target=self._run_engine, daemon=True)
            self._engine_thread.start()
        self.set_interval(0.1, self._drain)  # drain outbox in step with the UI

    def _run_engine(self) -> None:
        run(
            ticks=None,
            live=self._live,
            channel=TuiChannel(self.bridge),
            brain=self._brain,
            output=TuiOutput(self.bridge),
        )

    def _drain(self) -> None:
        try:
            log = self.query_one(RichLog)
        except NoMatches:
            return  # DOM not mounted yet / torn down (e.g. on exit) — skip this tick
        for event in self.bridge.drain_output():
            if event.get("kind") == "status":
                self._update_status(event["snapshot"])
            else:
                self._render(log, event)

    def _update_status(self, snap: dict) -> None:
        self.query_one("#status", Static).update(status_line1(snap))
        self.query_one("#stats", Static).update(status_line2(snap))
        rows = needs_panel_lines(snap)
        panel = self.query_one("#needspanel", Static)
        panel.border_title = f"Needs · tick {snap.get('tick', 0)}"
        panel.update("\n".join(rows) if rows else "needs: …")

    def _render(self, log: RichLog, event: dict) -> None:
        kind = event.get("kind")
        if kind == "agent":
            is_self = event.get("is_self", False)
            name_style = _SELF_STYLE if is_self else _BOT_STYLE
            name = agent_label(is_self)
            model = event.get("model")
            # Each span is its own markup (and escaped) so a model like "opus" can't be
            # mistaken for a tag: bold colored name + dark-green (model); body stays default white.
            model_part = f" [{_MODEL_STYLE}]({escape(model)})[/]" if model else ""
            text = event["text"]
            self._last_reply = text
            # Name + (model) on one line; her reply body on the next line (default white).
            log.write(f"[{name_style}]{escape(name)}[/]{model_part}:")
            log.write(escape(text))
            self._transcript.append(f"{name}{f' ({model})' if model else ''}: {text}")
        elif kind == "notice":
            log.write(f"[{_NOTICE_STYLE}]{escape(event['text'])}[/]")
            self._transcript.append(event["text"])
        elif kind == "user":  # echo-free: not expected (TuiOutput.user is a no-op)
            log.write(f"[{_USER_STYLE}]you:[/] {escape(event['text'])}")
            self._transcript.append(f"you: {event['text']}")

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        line = event.value.strip()
        prompt = self.query_one("#prompt", ChatInput)
        if line:
            # UI shows the typed text itself (echo-free: the engine does not echo it).
            self.query_one(RichLog).write(f"[{_USER_STYLE}]you:[/] {escape(line)}")
            self._transcript.append(f"you: {line}")
            self.bridge.submit(line)
        prompt.text = ""

    def _copy(self, text: str) -> None:
        """Put `text` on the clipboard via BOTH OSC 52 (Textual — works over SSH / iTerm2) and the
        platform clipboard tool (pbcopy/xclip/…), since Terminal.app ignores OSC 52."""
        self.copy_to_clipboard(text)  # OSC 52 — terminals that support it (also sets app.clipboard)
        system_clipboard_copy(text)  # pbcopy / clip / xclip — the rest, incl. macOS Terminal.app

    def action_copy_reply(self) -> None:
        if self._last_reply:
            self._copy(self._last_reply)

    def action_copy_all(self) -> None:
        self._copy("\n".join(self._transcript))

    def action_clear_log(self) -> None:
        self.query_one(RichLog).clear()
        self._transcript.clear()
        self._last_reply = ""

    def action_quit(self) -> None:
        # Ask the engine to end its loop; its `finally` saves the session AND summarizes it
        # (Opus + extended thinking, ~10-20s). Don't block the UI on that — joining with a
        # short timeout used to kill the summary. Show "saving…" and poll until the engine
        # thread finishes (with a hard cap), then exit; the _drain timer keeps rendering.
        self.bridge.submit("/quit")
        try:
            self.query_one("#status", Static).update("status: saving session (summarizing)…")
        except NoMatches:
            pass
        self._quit_polls = 0
        self._quit_timer = self.set_interval(0.2, self._await_engine_then_exit)

    def _await_engine_then_exit(self) -> None:
        self._quit_polls += 1
        thread_done = self._engine_thread is None or not self._engine_thread.is_alive()
        if thread_done or self._quit_polls > 1000:  # ~200s cap (summarize times out at 180s)
            if self._quit_timer is not None:
                self._quit_timer.stop()
            self.exit()


def main() -> None:
    """Launch the TUI live (LiveBrain) — needs keys/CLI as in normal live mode."""
    KilnApp(live=True).run()
