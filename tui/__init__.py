"""
kiln.tui — клієнт термінального UI (Textual) над двіжком.

Тонкий клієнт: НЕ містить логіки агента. Спілкується з циклом тіків лише через
echo-free місток (bridge.Bridge) — інбокс (UI -> двіжок: введені рядки) та аутбокс
(двіжок -> UI: події рендера). Сам двіжок (kiln.engine) цей пакет не імпортує.
"""

from __future__ import annotations

from .bridge import Bridge

__all__ = ["Bridge"]
