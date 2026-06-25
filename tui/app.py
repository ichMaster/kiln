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
from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog, Static

from kiln.engine import run

from .bridge import Bridge
from .channel import TuiChannel
from .output import TuiOutput
from .render import needs_panel_lines, status_line1, status_line2

# Line colors (Rich markup) — equivalents of the ANSI ones from usage.py.
_USER_STYLE = "bold cyan"
_BOT_STYLE = "bold green"
_TECH_STYLE = "dim green"


def _short_model(model: str) -> str:
    return model.split("-")[1] if "-" in model else model


class KilnApp(App):
    """Thin client: log + input line, wired to the engine through the bridge."""

    CSS = """
    #statusbar { dock: top; height: 2; padding: 0 1; background: $panel; color: $text-muted; }
    #needspanel {
        dock: top; height: auto; padding: 0 1;
        border-bottom: solid $panel; color: $text-muted;
    }
    RichLog { height: 1fr; padding: 0 1; }
    Input { dock: bottom; }
    """
    BINDINGS = [("ctrl+q", "quit", "Quit")]

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

    def compose(self) -> ComposeResult:
        yield Static("status: starting…", id="statusbar")
        yield Static("needs: …", id="needspanel")
        yield RichLog(markup=True, wrap=True, highlight=False)
        yield Input(placeholder="Type a message…  (Ctrl+Q — quit)")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
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
        log = self.query_one(RichLog)
        for event in self.bridge.drain_output():
            if event.get("kind") == "status":
                self._update_status(event["snapshot"])
            else:
                self._render(log, event)

    def _update_status(self, snap: dict) -> None:
        self.query_one("#statusbar", Static).update(f"{status_line1(snap)}\n{status_line2(snap)}")
        rows = needs_panel_lines(snap)
        self.query_one("#needspanel", Static).update("\n".join(rows) if rows else "needs: …")

    def _render(self, log: RichLog, event: dict) -> None:
        kind = event.get("kind")
        if kind == "agent":
            label = "Agnika (self)" if event.get("is_self") else "Agnika"
            log.write(f"[{_BOT_STYLE}]{label}:[/] {escape(event['text'])}")
        elif kind == "usage":
            u = event.get("usage")
            if u:
                log.write(
                    f"[{_TECH_STYLE}]      · {_short_model(u['model'])} · "
                    f"{u['input']}→{u['output']} tok ({u['total']})[/]"
                )
        elif kind == "notice":
            log.write(escape(event["text"]))
        elif kind == "user":  # echo-free: not expected (TuiOutput.user is a no-op)
            log.write(f"[{_USER_STYLE}]you:[/] {escape(event['text'])}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if line:
            # UI shows the typed text itself (echo-free: the engine does not echo it).
            self.query_one(RichLog).write(f"[{_USER_STYLE}]you:[/] {escape(line)}")
            self.bridge.submit(line)
        event.input.value = ""

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
