"""
kiln — seam виводу: ядро пише репліки через порт, а не у print напряму.

Мета — лишити ядро (engine.run) незалежним від інтерфейсу: сьогодні типовий сінк
ConsoleOutput друкує в термінал (кольори як раніше), а в v0.3 на той самий порт
стане echo-free TUI-шина, у v1.1/1.2 — серверний протокол подій (user.message,
agnika.message, usage, ...). Зміна сінка міняє, КУДИ йде вивід, не чіпаючи двіжок.

Контракт: ядро викликає лише методи Output; жодних print у engine.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .usage import BOT_COLOR, BOT_NAME, USER_COLOR, _c, print_tech


@runtime_checkable
class Output(Protocol):
    """Порт виводу. Методи приблизно відповідають майбутнім подіям протоколу."""

    def user(self, text: str) -> None:
        """Ехо повідомлення користувача."""
        ...

    def agent(self, text: str, *, is_self: bool = False, lead: bool = False) -> None:
        """Репліка агента (is_self — самоініційована; lead — з порожнім рядком перед нею)."""
        ...

    def usage(self, usage: dict | None) -> None:
        """Технічний рядок під відповіддю (модель + токени)."""
        ...

    def notice(self, text: str) -> None:
        """Системний рядок ([exit] …, статус виходу тощо)."""
        ...


class ConsoleOutput:
    """Типовий сінк: друк у термінал (поведінка/кольори — як до seam'а)."""

    def user(self, text: str) -> None:
        print("\n" + _c(f"you: {text}", USER_COLOR))

    def agent(self, text: str, *, is_self: bool = False, lead: bool = False) -> None:
        label = f"{BOT_NAME} (self)" if is_self else BOT_NAME
        nl = "\n" if (is_self or lead) else ""  # самоініційована/«свіжа» репліка — з відступом
        print(nl + _c(f"{label}: {text}", BOT_COLOR))

    def usage(self, usage: dict | None) -> None:
        print_tech(usage)

    def notice(self, text: str) -> None:
        print(text)
