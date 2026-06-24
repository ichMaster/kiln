"""
kiln — історія розмови: спільна стрічка повідомлень сесії.

Без обрізання й самарізації: накопичуємо все і додаємо до промпта.
Кожен елемент — {"role": "user"|"assistant", "text": ...}.
"""

from __future__ import annotations

ROLE_USER = "user"
ROLE_BOT = "assistant"


def to_messages(history: list[dict]) -> list[dict]:
    """Історія -> формат Anthropic Messages API ({role, content})."""
    return [{"role": h["role"], "content": h["text"]} for h in history]


def to_transcript(history: list[dict]) -> str:
    """Історія -> простий текстовий транскрипт для вкладання у промпт CLI."""
    label = {ROLE_USER: "Користувач", ROLE_BOT: "Ти"}
    return "\n".join(f"{label.get(h['role'], h['role'])}: {h['text']}" for h in history)
