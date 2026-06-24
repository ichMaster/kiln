"""
kiln — системні слеш-команди.

Перехоплюють ввід ДО класифікації, тож не йдуть у мозок як повідомлення.
handle_command() повертає:
  "handled"        — команда виконана, продовжуємо цикл;
  "quit"           — користувач просить вийти;
  ("ask", текст)   — примусовий deep-хід (сам виклик робить цикл run);
  None             — це не команда, далі звичайна обробка.
"""

from __future__ import annotations

from .history import ROLE_USER
from .memory import load_memory


def _fmt_needs(state) -> str:
    return "  ".join(f"{k}={state.needs[k]:.2f}" for k in state.needs)


def handle_command(line: str, state, history: list[dict], system: str, live: bool):
    if not line.startswith("/"):
        return None

    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("quit", "exit", "q"):
        return "quit"

    elif cmd in ("help", "h", "?"):
        print("Команди: /status  /needs  /memory  /history  /ask <текст>  /clear  /help  /quit")

    elif cmd == "status":
        name, level = state.hottest_need()
        print(
            f"[status] ходів={len(history)}  найгарячіша={name}={level:.2f}  "
            f"режим={'live' if live else 'dry'}"
        )
        print(f"         потреби: {_fmt_needs(state)}")

    elif cmd == "needs":
        print(f"[needs] {_fmt_needs(state)}")

    elif cmd == "memory":
        mem = load_memory().strip()
        print("[memory]\n" + (mem if mem else "(порожньо)"))

    elif cmd == "history":
        if not history:
            print("[history] (порожньо)")
        else:
            for h in history[-10:]:
                who = "USER" if h["role"] == ROLE_USER else "BOT "
                print(f"  {who}: {h['text']}")

    elif cmd == "ask":
        # Примусовий виклик Клода (deep), повз класифікатор.
        # Сам хід робить цикл run() — щоб не тягнути сюди deep_reply (розрив циклу).
        if not arg:
            print("[ask] вкажи текст: /ask <питання>")
        else:
            return ("ask", arg)

    elif cmd == "clear":
        history.clear()
        print("[clear] історію сесії очищено")

    else:
        print(f"[?] невідома команда: /{cmd} (спробуй /help)")

    return "handled"
