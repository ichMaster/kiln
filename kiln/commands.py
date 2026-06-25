"""
kiln — system slash commands.

They intercept input BEFORE classification, so they don't reach the brain as a message.
Command output goes through the Output seam (output.notice), not print — so any
client (console today, TUI/web later) receives the result. handle_command() returns:
  "handled"        — command executed, continue the loop;
  "quit"           — user asks to exit;
  ("ask", text)    — forced deep turn (the run loop makes the call itself);
  None             — not a command, fall through to normal handling.
"""

from __future__ import annotations

from .history import ROLE_USER
from .memory import load_memory
from .output import Output

# Canonical list of implemented slash commands — the single source for both /help
# and the TUI hint line, so they never advertise a command kiln doesn't have.
COMMANDS = ("status", "needs", "memory", "history", "ask", "clear", "help", "quit")


def command_hints() -> str:
    """Display string of the implemented slash commands (/ask takes an argument)."""
    return "  ".join(f"/{c} <text>" if c == "ask" else f"/{c}" for c in COMMANDS)


def _fmt_needs(state) -> str:
    return "  ".join(f"{k}={state.needs[k]:.2f}" for k in state.needs)


def handle_command(line: str, state, history: list[dict], system: str, live: bool, output: Output):
    if not line.startswith("/"):
        return None

    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("quit", "exit", "q"):
        return "quit"

    elif cmd in ("help", "h", "?"):
        output.notice("Commands: " + command_hints())

    elif cmd == "status":
        name, level = state.hottest_need()
        output.notice(
            f"[status] turns={len(history)}  hottest={name}={level:.2f}  "
            f"mode={'live' if live else 'dry'}"
        )
        output.notice(f"         needs: {_fmt_needs(state)}")

    elif cmd == "needs":
        output.notice(f"[needs] {_fmt_needs(state)}")

    elif cmd == "memory":
        mem = load_memory().strip()
        output.notice("[memory]\n" + (mem if mem else "(empty)"))

    elif cmd == "history":
        if not history:
            output.notice("[history] (empty)")
        else:
            for h in history[-10:]:
                who = "USER" if h["role"] == ROLE_USER else "BOT "
                output.notice(f"  {who}: {h['text']}")

    elif cmd == "ask":
        # Forced Claude call (deep), bypassing the classifier.
        # The run() loop makes the turn itself — to avoid pulling deep_reply here (cycle break).
        if not arg:
            output.notice("[ask] provide text: /ask <question>")
        else:
            return ("ask", arg)

    elif cmd == "clear":
        history.clear()
        output.notice("[clear] session history cleared")

    else:
        output.notice(f"[?] unknown command: /{cmd} (try /help)")

    return "handled"
