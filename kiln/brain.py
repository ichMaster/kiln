"""
kiln — seam «мозок»: обидві гілки за одним тонким інтерфейсом.

Ядро (respond/run) кличе модель ЛИШЕ через цей seam — ніколи напряму SDK чи CLI.
Реалізації:
  - LiveBrain — справжні виклики: чат через Anthropic Messages API (Haiku),
    роздум/тули через `claude -p` (Opus, субпроцес);
  - MockBrain — детерміновані канонічні відповіді + синтетичний usage, без
    мережі й субпроцесів (dry-run демо й усі тести йдуть через нього → 0 платних викликів).

Контракт: кожен метод повертає (text, usage), де usage — {model, input, output, total}
або None (помилка/без виклику). Парсинг токенів — у usage.usage_record.
"""

from __future__ import annotations

import json
import subprocess
from typing import Protocol, runtime_checkable

from .config import CHAT_MODEL, DEEP_MODEL, DEEP_TOOLS
from .history import to_messages, to_transcript
from .usage import _cli_error_detail, usage_record

# usage: {model, input, output, total} або None
Usage = dict | None


@runtime_checkable
class Brain(Protocol):
    """Seam між ядром і моделлю. Дві гілки — дешевий чат і глибокий хід."""

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
        """Дешева швидка репліка (вся історія — як messages)."""
        ...

    def deep(self, prompt: str, history: list[dict], system: str,
             with_tools: bool) -> tuple[str, Usage]:
        """Глибокий хід (роздум або тули); історія — транскриптом у промпт."""
        ...


class LiveBrain:
    """Справжній мозок: Haiku через SDK (чат) + Opus через `claude -p` (роздум/тули)."""

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
        # Локальний імпорт: dry-run/тести працюють без пакета anthropic — він
        # потрібен лише цій живій гілці. Ключ — з ANTHROPIC_API_KEY (.env -> os.environ).
        from anthropic import Anthropic

        try:
            msg = Anthropic().messages.create(
                model=CHAT_MODEL,                 # Haiku 4.5 — дешево і швидко
                max_tokens=512,                   # коротка репліка
                system=system,                    # канон + довга пам'ять
                messages=to_messages(history),    # уся стрічка, з поточним ходом
            )
        except Exception as e:                    # мережа / ліміти / помилка API
            return f"(chat error: {e})", None
        text = next((b.text for b in msg.content if b.type == "text"), "")
        return text, usage_record(msg.model, msg.usage)

    def deep(self, prompt: str, history: list[dict], system: str,
             with_tools: bool) -> tuple[str, Usage]:
        # Субпроцес не тримає сесію між викликами, тож попередні ходи вкладаємо
        # текстовим транскриптом, а поточний промпт — у кінець.
        prior = history[:-1] if history else []
        full_prompt = prompt
        if prior:
            full_prompt = (
                "Контекст розмови:\n" + to_transcript(prior) +
                "\n\nПоточне повідомлення:\n" + prompt
            )

        # --output-format json: дістаємо і текст (result), і usage одним викликом.
        cmd = ["claude", "-p", "--model", DEEP_MODEL, "--output-format", "json",
               "--append-system-prompt", system]
        if with_tools and DEEP_TOOLS:
            cmd += ["--allowedTools", ",".join(DEEP_TOOLS)]
        cmd.append(full_prompt)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except Exception as e:                         # таймаут / процес не стартував
            return f"(deep error: {e})", None
        if result.returncode != 0:
            # CLI інколи падає (ліміт, тимчасова помилка) — не валимо цикл.
            return f"(deep error: claude CLI {result.returncode}: {_cli_error_detail(result)})", None
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.stdout.strip(), None         # несподіваний вивід — як є
        return (data.get("result") or "").strip(), usage_record(DEEP_MODEL, data.get("usage"))


class MockBrain:
    """
    Детермінований мозок для dry-run і тестів: канонічні відповіді + синтетичний
    usage. Жодних мережевих викликів і субпроцесів — отже, нуль платних викликів.
    Тексти відповідей навмисно збігаються з колишніми dry-run-заглушками.
    """

    def chat(self, history: list[dict], system: str) -> tuple[str, Usage]:
        text = f"(dry-run chat: messages={len(history)})"
        return text, usage_record(CHAT_MODEL, {"input_tokens": 8, "output_tokens": 12})

    def deep(self, prompt: str, history: list[dict], system: str,
             with_tools: bool) -> tuple[str, Usage]:
        prior = len(history) - 1 if history else 0
        text = f"(dry-run deep: transcript={prior} turns, tools={with_tools})"
        return text, usage_record(DEEP_MODEL, {"input_tokens": 20, "output_tokens": 30})
