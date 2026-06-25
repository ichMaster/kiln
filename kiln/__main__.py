"""
kiln — консольний вхід.

Команда `kiln` (або `python -m kiln`) кличе main(). Живий режим читає stdin і
крутить тіки безперервно; dry-run — детермінований демо-сценарій через
ScriptedChannel (без жодних викликів моделі), він же — димовий тест двіжка.

Вхід винесено з engine.py навмисно: engine лишається бібліотечним модулем без
__main__, тож його можна імпортувати (зокрема в тестах) без побічних ефектів.
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
    """`kiln --tui` — Textual-клієнт (екстра `tui`); двіжок крутиться у фоні."""
    try:
        from tui.app import main as tui_main
    except ImportError:
        print("Для TUI постав екстру: pip install -e '.[tui]'")
        return
    tui_main()


def _demo() -> None:
    """Детермінований dry-run демо-сценарій (також димовий тест)."""
    # Демо 1 (dry-run): чат, команди й вихід.
    run(
        ticks=12,
        live=False,
        channel=ScriptedChannel(
            {
                1: "привіт, як справи?",  # -> chat / haiku
                2: "/status",  # системна команда
                4: "поясни, чому так виходить",  # -> think / opus
                5: "/history",  # показати стрічку
                6: "/needs",  # стан потреб
                8: "/quit",  # вихід
            }
        ),
    )

    # Демо 2 (dry-run): self-тригер. Піднімаємо novelty над порогом —
    # двіжок озивається сам через deep, потім кулдаун тримає тишу.
    print("\n--- демо self-тригера (novelty над порогом) ---")
    state = load_state()
    state.needs["novelty"] = 0.92
    save_state(state)
    run(ticks=8, live=False, channel=ScriptedChannel({}))
    # повертаємо спокійний дефолт
    st = load_state()
    st.needs["novelty"] = 0.25
    save_state(st)


def main() -> None:
    if "--tui" in sys.argv[1:]:
        _run_tui()
        return
    live = os.environ.get("KILN_LIVE") == "1"
    if live:
        # Живий режим: пиши повідомлення в термінал, тіки крутяться самі.
        print("kiln: пиши повідомлення (Ctrl-C щоб вийти)…")
        try:
            run(ticks=None, live=True, channel=StdinChannel())
        except KeyboardInterrupt:
            print("\nбувай.")
    else:
        _demo()


if __name__ == "__main__":
    main()
