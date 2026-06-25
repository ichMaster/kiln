"""
kiln.tui.bridge — echo-free місток між циклом тіків і потоком UI.

Дві НЕЗАЛЕЖНІ черги (thread-safe, бо двіжок крутиться в фоновому потоці, а UI — у
своєму):
  - inbox  (UI -> двіжок): введені користувачем рядки; двіжок читає їх через
    TuiChannel.poll() (KILN-009);
  - outbox (двіжок -> UI): події рендера, які кладе TuiOutput (KILN-008), а UI
    вичерпує й малює.

**Echo-free структурно:** ввід іде ЛИШЕ в inbox, рендер — ЛИШЕ в outbox; введений
рядок ніколи не «відлунює» в outbox. Тому UI сам показує набране (один раз), а
двіжок пише в outbox тільки СВОЇ відповіді — без подвійного відлуння й перемішування
вводу з виводом.

Подія рендера — простий dict (foreshadow серверного протоколу подій v1.1/1.2),
ключ `kind` ∈ {"user", "agent", "usage", "notice"} віддзеркалює методи seam'а Output:
  {"kind": "user",   "text": str}
  {"kind": "agent",  "text": str, "is_self": bool, "lead": bool}
  {"kind": "usage",  "usage": dict | None}
  {"kind": "notice", "text": str}
"""

from __future__ import annotations

import queue


class Bridge:
    """Thread-safe inbox/outbox між двіжком і UI (без логіки агента)."""

    def __init__(self) -> None:
        self._inbox: queue.Queue[str] = queue.Queue()  # UI -> двіжок (рядки)
        self._outbox: queue.Queue[dict] = queue.Queue()  # двіжок -> UI (події рендера)

    # --- бік UI: ввід -> двіжок --------------------------------------------
    def submit(self, line: str) -> None:
        """UI кладе набраний рядок для двіжка."""
        self._inbox.put(line)

    # --- бік двіжка: читання вводу (неблокуюче) ----------------------------
    def poll_input(self) -> str | None:
        """Двіжок (TuiChannel) забирає черговий рядок або None, не блокуючи цикл."""
        try:
            return self._inbox.get_nowait()
        except queue.Empty:
            return None

    # --- бік двіжка: рендер -> UI ------------------------------------------
    def emit(self, event: dict) -> None:
        """Двіжок (TuiOutput) кладе подію рендера для UI."""
        self._outbox.put(event)

    # --- бік UI: читання рендера (неблокуюче) ------------------------------
    def poll_output(self) -> dict | None:
        """UI забирає чергову подію рендера або None."""
        try:
            return self._outbox.get_nowait()
        except queue.Empty:
            return None

    def drain_output(self) -> list[dict]:
        """Усі наявні події рендера одразу (UI малює їх за один прохід)."""
        events: list[dict] = []
        while (event := self.poll_output()) is not None:
            events.append(event)
        return events
