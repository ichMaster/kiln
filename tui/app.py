"""
kiln.tui.app — Textual-застосунок над двіжком.

Лог відповідей (прокручуваний) + фіксований рядок вводу. Двіжок (engine.run)
крутиться у ФОНОВОМУ потоці й спілкується з UI лише через місток: TuiChannel читає
введені рядки з inbox, TuiOutput кладе події рендера в outbox, а застосунок їх
вичерпує таймером і малює. Тож цикл тіків живе паралельно з UI (самотригери теж
працюють), а виклики моделі не морозять інтерфейс.

Echo-free: набране користувач бачить одразу (UI сам пише `you: …`), а двіжок ввід
НЕ відлунює — пише в лог лише свої відповіді.
"""

from __future__ import annotations

import threading

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog

from kiln.engine import run

from .bridge import Bridge
from .channel import TuiChannel
from .output import TuiOutput

# Кольори рядків (Rich markup) — відповідники ANSI з usage.py.
_USER_STYLE = "bold cyan"
_BOT_STYLE = "bold green"
_TECH_STYLE = "dim green"


def _short_model(model: str) -> str:
    return model.split("-")[1] if "-" in model else model


class KilnApp(App):
    """Тонкий клієнт: лог + рядок вводу, зв'язані з двіжком через місток."""

    CSS = """
    RichLog { height: 1fr; padding: 0 1; }
    Input { dock: bottom; }
    """
    BINDINGS = [("ctrl+q", "quit", "Вийти")]

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
        yield RichLog(markup=True, wrap=True, highlight=False)
        yield Input(placeholder="Напиши повідомлення…  (Ctrl+Q — вийти)")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        if self._start_engine:
            self._engine_thread = threading.Thread(target=self._run_engine, daemon=True)
            self._engine_thread.start()
        self.set_interval(0.1, self._drain)  # вичерпуємо outbox у такт UI

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
            self._render(log, event)

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
                    f"{u['input']}→{u['output']} ток ({u['total']})[/]"
                )
        elif kind == "notice":
            log.write(escape(event["text"]))
        elif kind == "user":  # echo-free: не очікується (TuiOutput.user — no-op)
            log.write(f"[{_USER_STYLE}]you:[/] {escape(event['text'])}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if line:
            # UI сам показує набране (echo-free: двіжок його не відлунює).
            self.query_one(RichLog).write(f"[{_USER_STYLE}]you:[/] {escape(line)}")
            self.bridge.submit(line)
        event.input.value = ""

    def action_quit(self) -> None:
        # Чистий вихід: просимо двіжок завершити цикл (його finally збереже сесію),
        # коротко чекаємо й закриваємось.
        self.bridge.submit("/quit")
        if self._engine_thread is not None:
            self._engine_thread.join(timeout=2.0)
        self.exit()


def main() -> None:
    """Запуск TUI наживо (LiveBrain) — потрібні ключі/CLI як у звичайному live-режимі."""
    KilnApp(live=True).run()
