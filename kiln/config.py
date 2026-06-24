"""
kiln — конфігурація: шляхи, .env та ручки калібрування.

Винесено окремо, щоб усі модулі (engine, memory, commands) могли імпортувати
сталі без кругових залежностей (engine запускається як __main__).
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Шляхи ------------------------------------------------------------------
# Модуль живе в пакеті kiln/, тож корінь проєкту — це БАТЬКО пакета (а не сам
# пакет). Усі мутабельні дані (state/, history/, .env) лежать у корені. Корінь
# можна перевизначити змінною KILN_HOME (напр. щоб тримати стан поза репо).
_PKG_DIR = Path(__file__).resolve().parent           # .../kiln/kiln (пакет)
PROJECT_ROOT = Path(os.environ.get("KILN_HOME", _PKG_DIR.parent))   # корінь репо за замовчанням

STATE_DIR = PROJECT_ROOT / "state"
MEMORY_FILE = STATE_DIR / "memory.md"       # довга пам'ять: підсумки минулих розмов
CANON_FILE = STATE_DIR / "canon.md"         # канон: персона/голос (системний промпт)
PROMPTS_FILE = STATE_DIR / "prompts.md"     # промпти self-тригера по потребах
HISTORY_DIR = PROJECT_ROOT / "history"      # сирі транскрипти сесій (JSON, для RAG)
ENV_FILE = PROJECT_ROOT / ".env"            # локальна конфігурація (моделі + калібрування)


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Мінімальний .env-завантажувач (KEY=VALUE) без сторонніх залежностей.
    Підтримує рядкові й інлайн (` # ...`) коментарі. Справжні змінні
    середовища мають пріоритет (setdefault не перетирає вже задані)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        val = val.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


load_dotenv()   # читаємо .env ДО визначення налаштувань нижче

# --- Тіки -------------------------------------------------------------------
TICK_SECONDS = float(os.environ.get("TICK_SECONDS", "0.5"))

# Self-тригери: на КОЖНУ потребу — свій поріг і дія (гілка) при перетині.
#   action: "chat" -> дешевий Haiku; "deep" -> Claude (Opus).
# connection/rest закриваються контактом/паузою -> дешевий чат вистачає;
# novelty/intensity потребують змісту/розрядки -> deep.
NEED_TRIGGERS = {
    "connection": {"threshold": 0.80, "action": "chat"},
    "rest":       {"threshold": 0.90, "action": "idle"},
    "novelty":    {"threshold": 0.85, "action": "deep"},
    "intensity":  {"threshold": 0.75, "action": "deep"},
}
SELF_COOLDOWN = int(os.environ.get("SELF_COOLDOWN", "5"))   # тіків мовчання після self-виклику (на потребу)

# Дрейф на тік для КОЖНОЇ потреби окремо (скільки додається щотіку).
# intensity накопичується найшвидше, rest — найповільніше.
DRIFT = {
    "connection": 0.020,
    "rest": 0.010,
    "novelty": 0.015,
    "intensity": 0.030,
}

# Закриття потреб подіями. Від'ємні значення = зниження рівня.
#
# Ключова ідея: ЯКА гілка відповіла, ВИЗНАЧАЄ, які потреби закрились.
#   - чат (Haiku) дає контакт, але майже не насичує новизною/розрядкою;
#   - роздум/тули (Claude CLI) — "ситна їжа": гасить novelty, rest, intensity.
SATIATION = {
    # подія "відповіли через чат"
    "chat":  {"connection": -0.50, "rest": +0.05, "novelty": -0.10, "intensity": -0.10},
    # подія "відповіли через роздум або тули" (виклик Клода)
    "deep":  {"connection": -0.50, "rest": +0.15, "novelty": -0.40, "intensity": -0.35},
    # подія "тиша" (тік без відповіді): відпочинок + вистигання напруги
    "idle":  {"connection": -0, "rest": -0.05, "novelty": -0, "intensity": +0.05},
}

# --- Класифікація / роутинг -------------------------------------------------
# Поріг "ваги" ходу: вище -> хід вважається роздумом, іде в Claude CLI.
THINK_THRESHOLD = float(os.environ.get("THINK_THRESHOLD", "0.45"))

CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-haiku-4-5-20251001")   # проста API для чату
DEEP_MODEL = os.environ.get("DEEP_MODEL", "claude-opus-4-8")             # Claude CLI для роздумів/тулів

# Тули/скіли, дозволені на гілці роздумів (приклад).
DEEP_TOOLS = ["Read", "Write", "Bash"]
DEEP_SKILLS: list[str] = []                # напр. ["search", "summarize"]

# Канон (персона/голос) береться зі state/canon.md; це лише запасний варіант.
DEFAULT_CANON = (
    "Ти — співрозмовник цього чат-двіжка. Відповідай стисло, природно, "
    "українською. Тримай сталий голос незалежно від гілки."
)

# Слова-маркери, що натякають на потребу в міркуванні чи діях.
THINK_HINTS = ("чому", "поясни", "проаналізуй", "порівняй", "розбери", "обґрунтуй")
TOOL_HINTS = ("файл", "запусти", "збережи", "прочитай", "пошукай", "знайди", "пошук")
