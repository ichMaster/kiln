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

import threading

from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Footer, Header, RichLog, Static, TextArea

from kiln.commands import command_hints
from kiln.engine import run

from .bridge import Bridge
from .channel import TuiChannel
from .output import TuiOutput
from .render import agent_label, needs_panel_lines, status_line1, status_line2, tech_line

# Line colors (Rich markup) — equivalents of the ANSI ones from usage.py.
_USER_STYLE = "bold cyan"
_BOT_STYLE = "bold green"
_SELF_STYLE = "green"  # self-triggered replies: dimmer than a direct reply
_TECH_STYLE = "dim green"


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
        margin: 0 1 2 1;    /* t r b l — bottom 2 lifts it off the footer; right/left 1 inset */
    }
    """
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+y", "copy_reply", "Copy reply"),
        ("ctrl+o", "copy_all", "Copy all"),
        ("ctrl+l", "clear_log", "Clear"),
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
            style = _SELF_STYLE if is_self else _BOT_STYLE
            label, text = agent_label(is_self), event["text"]
            self._last_reply = text
            log.write(f"[{style}]{label}:[/] {escape(text)}")
            self._transcript.append(f"{label}: {text}")
        elif kind == "usage":
            line = tech_line(event.get("usage"), event.get("latency"))
            if line:
                log.write(f"[{_TECH_STYLE}]{line}[/]")
                self._transcript.append(line)
        elif kind == "notice":
            log.write(escape(event["text"]))
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

    def action_copy_reply(self) -> None:
        if self._last_reply:
            self.copy_to_clipboard(self._last_reply)

    def action_copy_all(self) -> None:
        self.copy_to_clipboard("\n".join(self._transcript))

    def action_clear_log(self) -> None:
        self.query_one(RichLog).clear()
        self._transcript.clear()
        self._last_reply = ""

    def action_quit(self) -> None:
        # Clean exit: ask the engine to end the loop (its finally saves the session),
        # wait briefly, and close.
        self.bridge.submit("/quit")
        if self._engine_thread is not None:
            self._engine_thread.join(timeout=2.0)
        self.exit()


def main() -> None:
    """Launch the TUI live (LiveBrain) — needs keys/CLI as in normal live mode."""
    KilnApp(live=True).run()
