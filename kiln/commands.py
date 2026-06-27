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

from .history import to_messages
from .output import Output

# Canonical list of implemented slash commands — the single source for both /help
# and the TUI hint line, so they never advertise a command kiln doesn't have.
COMMANDS = ("status", "needs", "self", "prompt", "ask", "clear", "help", "quit")


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

    elif cmd == "self":
        # Toggle proactive self-messages (the connection reach-out). Off -> she never writes
        # first; she still answers you. Per-session (not persisted).
        state.self_messages = not state.self_messages
        output.notice(f"[self] proactive self-messages: {'on' if state.self_messages else 'off'}")

    elif cmd == "prompt":
        # Show exactly what the brain receives this turn: the system prompt (canon + long-term
        # memory) and the conversation as the messages array sent to the model.
        output.notice("[prompt] ── system ──")
        output.notice(system)
        msgs = to_messages(history)
        output.notice(f"[prompt] ── messages ({len(msgs)}) ──")
        if not msgs:
            output.notice("  (no messages yet)")
        for m in msgs:
            output.notice(f"  [{m['role']}] {m['content']}")

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
