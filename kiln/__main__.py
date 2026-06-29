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
from .memory import migrate_legacy


def _run_tui() -> None:
    """`kiln --tui` — Textual client (the `tui` extra); the engine runs in the background."""
    try:
        from tui.app import main as tui_main
    except ImportError:
        print("For the TUI, install the extra: pip install -e '.[tui]'")
        return
    tui_main()


def _demo() -> None:
    """Deterministic dry-run demo scenario (also the smoke test).

    Persists to a THROWAWAY temp store so the demo never pollutes the real `.kiln/` (otherwise
    each `kiln` run would leave a `(dry-run …)` session in Agnika's history). The canon/prompts are
    still read from `state/`, so the demo uses the real persona; only store/needs/ledger are temp.
    """
    import tempfile
    from pathlib import Path

    from .config import CANON_FILE, PROMPTS_FILE, STATE_DIR, AgentPaths

    with tempfile.TemporaryDirectory(prefix="kiln-demo-") as tmp:
        root = Path(tmp)
        paths = AgentPaths(
            state_dir=STATE_DIR,  # read the real canon/prompts/mood
            needs_file=root / "needs.json",  # throwaway runtime — no pollution
            store_file=root / "store.json",
            usage_ledger=root / "usage-ledger.jsonl",
            usage_report=root / "usage-report.md",
            canon_file=CANON_FILE,
            prompts_file=PROMPTS_FILE,
        )

        # Demo 1 (dry-run): chat, commands, and exit.
        run(
            ticks=12,
            live=False,
            paths=paths,
            channel=ScriptedChannel(
                {
                    1: "привіт, як справи?",  # -> chat / haiku
                    2: "/status",  # system command
                    4: "поясни, чому так виходить",  # -> think / opus
                    5: "/prompt",  # show the system prompt + message feed
                    6: "/needs",  # needs state
                    8: "/quit",  # exit
                }
            ),
        )

        # Demo 2 (dry-run): self-trigger. We raise novelty above the threshold —
        # the engine speaks first via deep, then the cooldown keeps it quiet.
        print("\n--- self-trigger demo (novelty above threshold) ---")
        state = load_state(paths.needs_file)
        state.needs["novelty"] = 0.92
        save_state(state, paths.needs_file)
        run(ticks=8, live=False, channel=ScriptedChannel({}), paths=paths)
    # temp dir auto-removed — the demo persisted nothing to the real store


def main() -> None:
    migrate_legacy()  # one-shot: fold legacy memory.md + history/*.json into .kiln/store.json
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
