"""
kiln — the output seam: the core writes replies through a port, not via print directly.

The goal is to keep the core (engine.run) independent of the interface: today the default sink
ConsoleOutput prints to the terminal (colors as before), in v0.3 an echo-free TUI bus will plug
into the same port, and in v1.1/1.2 a server-side event protocol (user.message, agnika.message,
usage, ...). Swapping the sink changes WHERE output goes without touching the engine.

Contract: the core calls only Output methods; no print in engine.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .usage import BOT_COLOR, BOT_NAME, USER_COLOR, _c, print_tech


@runtime_checkable
class Output(Protocol):
    """Output port. The methods roughly correspond to future protocol events."""

    def user(self, text: str) -> None:
        """Echo of the user's message."""
        ...

    def agent(
        self, text: str, *, is_self: bool = False, lead: bool = False, model: str | None = None
    ) -> None:
        """Agent reply (is_self — self-initiated; lead — blank line before; model — the brain)."""
        ...

    def usage(self, usage: dict | None, latency: float | None = None) -> None:
        """Technical line beneath the reply (model + tokens, optional per-turn latency)."""
        ...

    def notice(self, text: str) -> None:
        """System line ([exit] …, exit status, etc.)."""
        ...

    def status(self, snapshot: dict) -> None:
        """Per-tick status snapshot (needs + thresholds + session stats). See engine."""
        ...


class ConsoleOutput:
    """The default sink: prints to the terminal (behavior/colors — as before the seam)."""

    def user(self, text: str) -> None:
        print("\n" + _c(f"you: {text}", USER_COLOR))

    def agent(
        self, text: str, *, is_self: bool = False, lead: bool = False, model: str | None = None
    ) -> None:
        # The console keeps the per-turn tech line (print_tech) for the model/tokens, so the
        # label stays plain here; `model` is honoured by the TUI label instead.
        label = f"{BOT_NAME} (self)" if is_self else BOT_NAME
        nl = "\n" if (is_self or lead) else ""  # self-initiated/"fresh" reply — with an indent
        print(nl + _c(f"{label}: {text}", BOT_COLOR))

    def usage(self, usage: dict | None, latency: float | None = None) -> None:
        print_tech(usage, latency)

    def notice(self, text: str) -> None:
        print(text)

    def status(self, snapshot: dict) -> None:
        # The console has no live bar — the per-tick status is a no-op here.
        return
