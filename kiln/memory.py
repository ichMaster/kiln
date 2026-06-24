"""
kiln — довга пам'ять між сесіями, промпти/канон і транскрипти сесій.

При виході розмова підсумовується через Claude і дописується в memory.md, а
сира історія сесії лягає у history/*.json (для RAG). При старті підсумки
вантажаться в системний промпт обох гілок.
"""

from __future__ import annotations

import datetime as _dt
import json
import random
import re
import subprocess

from .config import (MEMORY_FILE, CANON_FILE, HISTORY_DIR, PROMPTS_FILE,
                     DEFAULT_CANON, DEEP_MODEL)
from .history import to_transcript
from .usage import log_model, _cli_error_detail


def load_prompts() -> dict[str, list[str]]:
    """
    Читає state/prompts.md: секції [потреба] зі списком промптів self-тригера.
    Формат:
        [connection]
        Текст промпта 1
        Текст промпта 2
        [novelty]
        ...
    """
    out: dict[str, list[str]] = {}
    if not PROMPTS_FILE.exists():
        return out
    cur = None
    for line in PROMPTS_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"\[([a-zA-Z_]+)\]$", s)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(s)
    return out


def pick_prompt(prompts: dict[str, list[str]], need: str) -> str:
    """Випадковий промпт для потреби; запасний — якщо для потреби списку немає."""
    options = prompts.get(need)
    if options:
        return random.choice(options)
    return f"(внутрішній імпульс: '{need}') Озвися першим, коротко."


def load_memory() -> str:
    """Усі попередні підсумки розмов одним текстом (або '')."""
    return MEMORY_FILE.read_text(encoding="utf-8") if MEMORY_FILE.exists() else ""


def load_canon() -> str:
    """Канон (персона/голос обох гілок) зі state/canon.md; запасний — DEFAULT_CANON."""
    if CANON_FILE.exists():
        text = CANON_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    return DEFAULT_CANON


def summarize(history: list[dict], live: bool) -> str:
    """Підсумок розмови через Claude (`claude -p`). У dry-run — заглушка."""
    if not history:
        return ""
    transcript = to_transcript(history)
    prompt = (
        "Стисло підсумуй цю розмову українською (3-5 речень): про що говорили, "
        "які висновки, що варто пам'ятати наступного разу.\n\n" + transcript
    )
    if not live:
        return f"(dry-run summary: {len(history)} ходів)"
    cmd = ["claude", "-p", "--model", DEEP_MODEL, "--output-format", "json", prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:                         # таймаут / процес не стартував
        print(f"[exit] підсумок не зроблено: {e}")
        return ""
    if result.returncode != 0:
        # Не валимо вихід через невдалий підсумок — транскрипт уже збережено.
        print(f"[exit] підсумок не зроблено (claude CLI {result.returncode}: {_cli_error_detail(result)})")
        return ""
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()
    log_model("SUMMARY", DEEP_MODEL, data.get("usage"))
    return (data.get("result") or "").strip()


def save_summary(text: str) -> None:
    """Дописує підсумок у memory.md з датою-роздільником."""
    if not text:
        return
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n## Розмова {stamp}\n{text}\n"
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(block)


def save_session(history: list[dict], live: bool, started: str):
    """
    Зберігає СИРУ історію сесії у history/session-<stamp>.json (для майбутнього RAG).
    Один файл = одна сесія; ensure_ascii=False, щоб українська лишалась читомою.
    Повертає шлях до файлу або None (порожня історія).
    """
    if not history:
        return None
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ended = _dt.datetime.now()
    stamp = ended.strftime("%Y-%m-%d_%H-%M-%S")
    record = {
        "session": stamp,
        "started_at": started,
        "ended_at": ended.isoformat(timespec="seconds"),
        "mode": "live" if live else "dry",
        "turns": len(history),
        "history": history,            # [{role, text}, ...] у хронологічному порядку
    }
    # Не перетирати наявний файл, якщо дві сесії закрилися в ту саму секунду.
    path = HISTORY_DIR / f"session-{stamp}.json"
    n = 2
    while path.exists():
        path = HISTORY_DIR / f"session-{stamp}-{n}.json"
        n += 1
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_system(canon: str, memory: str) -> str:
    """Системний промпт = канон (персона) + довга пам'ять (якщо є)."""
    if not memory.strip():
        return canon
    return (
        canon +
        "\n\nДовга пам'ять про попередні розмови (для контексту):\n" + memory.strip()
    )
