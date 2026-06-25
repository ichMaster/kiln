"""
kiln — console entry point.

The `kiln` command (or `python -m kiln`) calls main(). Live mode reads stdin and
runs ticks continuously; dry-run is a deterministic demo scenario via
ScriptedChannel (without any model calls), which doubles as the engine's smoke test.

The entry point is split out of engine.py deliberately: engine stays a library module
without __main__, so it can be imported (including in tests) without side effects.
"""

from __future__ import annotations

import os
import sys

from .engine import (
    ScriptedChannel,
    StdinChannel,
    load_state,
    run,
    save_state,
)


def _run_tui() -> None:
    """`kiln --tui` — Textual client (the `tui` extra); the engine runs in the background."""
    try:
        from tui.app import main as tui_main
    except ImportError:
        print("For the TUI, install the extra: pip install -e '.[tui]'")
        return
    tui_main()


def _demo() -> None:
    """Deterministic dry-run demo scenario (also the smoke test)."""
    # Demo 1 (dry-run): chat, commands, and exit.
    run(
        ticks=12,
        live=False,
        channel=ScriptedChannel(
            {
                1: "привіт, як справи?",  # -> chat / haiku
                2: "/status",  # system command
                4: "поясни, чому так виходить",  # -> think / opus
                5: "/history",  # show the feed
                6: "/needs",  # needs state
                8: "/quit",  # exit
            }
        ),
    )

    # Demo 2 (dry-run): self-trigger. We raise novelty above the threshold —
    # the engine speaks first via deep, then the cooldown keeps it quiet.
    print("\n--- self-trigger demo (novelty above threshold) ---")
    state = load_state()
    state.needs["novelty"] = 0.92
    save_state(state)
    run(ticks=8, live=False, channel=ScriptedChannel({}))
    # restore the calm default
    st = load_state()
    st.needs["novelty"] = 0.25
    save_state(st)


def main() -> None:
    if "--tui" in sys.argv[1:]:
        _run_tui()
        return
    live = os.environ.get("KILN_LIVE") == "1"
    if live:
        # Live mode: type messages into the terminal, ticks run on their own.
        print("kiln: type a message (Ctrl-C to exit)…")
        try:
            run(ticks=None, live=True, channel=StdinChannel())
        except KeyboardInterrupt:
            print("\nbye.")
    else:
        _demo()


if __name__ == "__main__":
    main()
