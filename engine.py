"""
kiln — простий чат-двіжок із двома "мозками".

Дві гілки відповіді:
  - ЧАТ  -> Haiku через просту (HTTP) API. Дешево, швидко, без тулів.
            Це звичайна розмова: привітання, репліки, легкі відповіді.
  - РОЗДУМ / ТУЛИ -> Claude як зовнішній процес (`claude -p`). Тут можна
            вказати модель (Opus), дозволені тули та скіли. Дорожче, але
            з міркуванням і доступом до інструментів.

Двіжок крутиться циклом коротких тіків. Кожен тік дешевий: потреби самі
дрейфують, без звернень до моделі. Мозок вмикається лише за умовою
(ввід користувача або потреба перейшла поріг), і тоді хід КЛАСИФІКУЄТЬСЯ
на одну з гілок.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

STATE_DIR = Path(__file__).parent / "state"
MEMORY_FILE = STATE_DIR / "memory.md"     # довга пам'ять: підсумки минулих розмов
CANON_FILE = STATE_DIR / "canon.md"       # канон: персона/голос (системний промпт)
HISTORY_DIR = Path(__file__).parent / "history"   # сирі транскрипти сесій (JSON, для RAG)
ENV_FILE = Path(__file__).parent / ".env"  # локальна конфігурація (моделі + калібрування)


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
    "rest":       {"threshold": 0.90, "action": "chat"},
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
    "chat":  {"connection": -0.50, "novelty": -0.10, "intensity": -0.10},
    # подія "відповіли через роздум або тули" (виклик Клода)
    "deep":  {"connection": -0.50, "rest": -0.15, "novelty": -0.40, "intensity": -0.35},
    # подія "тиша" (тік без відповіді): відпочинок + вистигання напруги
    "idle":  {"rest": -0.05, "intensity": -0.02},
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
TOOL_HINTS = ("файл", "запусти", "збережи", "прочитай", "知", "search", "пошук")


# === Стан ===================================================================

@dataclass
class State:
    needs: dict[str, float] = field(default_factory=dict)

    @property
    def intensity(self) -> float:
        return self.needs.get("intensity", 0.0)

    @property
    def connection(self) -> float:
        return self.needs.get("connection", 0.0)

    def hottest_need(self) -> tuple[str, float]:
        if not self.needs:
            return ("", 0.0)
        k = max(self.needs, key=self.needs.get)
        return (k, self.needs[k])


def _read_floats(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([a-zA-Z_]+)\s*:\s*([0-9]*\.?[0-9]+)\s*$", line)
        if m:
            values[m.group(1)] = float(m.group(2))
    return values


def load_state(state_dir: Path = STATE_DIR) -> State:
    return State(needs=_read_floats(state_dir / "needs.md"))


def save_state(state: State, state_dir: Path = STATE_DIR) -> None:
    lines = ["# needs"]
    for k, v in state.needs.items():
        lines.append(f"{k}: {round(v, 3)}")
    (state_dir / "needs.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# === Тік ====================================================================

def drift(state: State) -> None:
    """Кожна потреба росте на свій коефіцієнт із DRIFT (з відсіканням на 1.0)."""
    for k in state.needs:
        state.needs[k] = min(1.0, state.needs[k] + DRIFT.get(k, 0.0))


def apply_satiation(state: State, event: str) -> None:
    """Закриває потреби за подією ('chat' | 'deep' | 'idle'), відсікаючи на 0."""
    for k, delta in SATIATION.get(event, {}).items():
        if k in state.needs:
            state.needs[k] = max(0.0, state.needs[k] + delta)


@dataclass
class TriggerBook:
    """Рантайм-стан тригерів (не зберігається): гістерезис + кулдаун на потребу."""
    armed: dict[str, bool] = field(default_factory=dict)      # готова спрацювати?
    cooldown: dict[str, int] = field(default_factory=dict)    # лишилось тіків тиші


def select_self_trigger(state: State, tg: TriggerBook) -> tuple[str | None, str | None]:
    """
    Повертає (потреба, дія) для self-виклику, або (None, None).
    Гістерезис: спрацьовує лише при перетині порога ВГОРУ (переозброєння,
    коли потреба впала нижче). Кулдаун: після спрацювання — тиша N тіків.
    """
    # Кулдаун тікає для всіх потреб раз на тік.
    for name in NEED_TRIGGERS:
        if tg.cooldown.get(name, 0) > 0:
            tg.cooldown[name] -= 1

    eligible = []
    for name, cfg in NEED_TRIGGERS.items():
        level = state.needs.get(name, 0.0)
        over = level >= cfg["threshold"]
        if not over:
            tg.armed[name] = True                      # переозброїти нижче порога
        elif tg.armed.get(name, True) and tg.cooldown.get(name, 0) == 0:
            eligible.append((level - cfg["threshold"], name, cfg["action"]))

    if not eligible:
        return None, None

    eligible.sort(reverse=True)                        # найбільший overshoot перший
    _, name, action = eligible[0]
    tg.armed[name] = False                             # розрядити гістерезис
    tg.cooldown[name] = SELF_COOLDOWN                  # завести кулдаун
    return name, action


# === Класифікація ходу ======================================================

def turn_weight(state: State) -> float:
    """Вага ходу ~0..1 за станом. Вище -> ближче до роздуму."""
    return max(0.0, min(1.0, 0.55 * state.intensity + 0.45 * state.connection))


def classify(prompt: str, state: State) -> str:
    """
    Повертає клас ходу: 'chat' | 'think' | 'tools'.

    Рішення — комбінація змісту повідомлення та стану:
      - явні маркери інструментів -> 'tools';
      - маркери міркування АБО висока вага стану -> 'think';
      - інакше -> 'chat'.
    """
    low = prompt.lower()
    if any(h in low for h in TOOL_HINTS):
        return "tools"
    if any(h in low for h in THINK_HINTS) or turn_weight(state) >= THINK_THRESHOLD:
        return "think"
    return "chat"


# === Історія розмови ========================================================
# Спільний для обох гілок список усіх повідомлень у пам'яті сесії.
# Без обрізання й самарізації: накопичуємо все і додаємо до промпта.

ROLE_USER = "user"
ROLE_BOT = "assistant"


def to_messages(history: list[dict]) -> list[dict]:
    """Історія -> формат Anthropic Messages API ({role, content})."""
    return [{"role": h["role"], "content": h["text"]} for h in history]


def to_transcript(history: list[dict]) -> str:
    """Історія -> простий текстовий транскрипт для вкладання у промпт CLI."""
    label = {ROLE_USER: "Користувач", ROLE_BOT: "Ти"}
    return "\n".join(f"{label.get(h['role'], h['role'])}: {h['text']}" for h in history)


# === Довга пам'ять (між сесіями) ============================================
# При виході розмова підсумовується через Claude і дописується в memory.md.
# При старті ці підсумки вантажаться в системний промпт обох гілок.

import datetime as _dt
import random

PROMPTS_FILE = STATE_DIR / "prompts.md"


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
    cmd = ["claude", "-p", "--model", DEEP_MODEL, prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"summarize failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def save_summary(text: str) -> None:
    """Дописує підсумок у memory.md з датою-роздільником."""
    if not text:
        return
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n## Розмова {stamp}\n{text}\n"
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(block)


def save_session(history: list[dict], live: bool, started: str) -> Path | None:
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


# === Гілка 1: ЧАТ (проста API, Haiku) =======================================

def chat_reply(prompt: str, live: bool, history: list[dict], system: str) -> str:
    """
    Дешева швидка відповідь через звичайну Anthropic Messages API (Haiku).
    Уся історія йде масивом messages; канон + довга пам'ять — у system.
    """
    if not live:
        return f"(dry-run chat: messages={len(history)})"

    # Локальний імпорт: dry-run працює без пакета anthropic — він потрібен
    # лише цій живій гілці. Ключ береться з ANTHROPIC_API_KEY (.env -> os.environ).
    from anthropic import Anthropic

    try:
        msg = Anthropic().messages.create(
            model=CHAT_MODEL,                 # Haiku 4.5 — дешево і швидко
            max_tokens=512,                   # коротка репліка
            system=system,                    # канон (canon.md/DEFAULT_CANON) + довга пам'ять
            messages=to_messages(history),    # уся стрічка, з поточним ходом
        )
    except Exception as e:                    # мережа / ліміти / помилка API
        # TODO: для тоншого контролю — ловити типізовані винятки SDK
        # (anthropic.RateLimitError, APIConnectionError, APIStatusError).
        return f"(chat error: {e})"

    # content — список блоків; беремо перший текстовий (або порожньо).
    return next((b.text for b in msg.content if b.type == "text"), "")


# === Гілка 2: РОЗДУМ / ТУЛИ (Claude CLI) ====================================

def deep_reply(prompt: str, live: bool, with_tools: bool, history: list[dict], system: str) -> str:
    """
    Глибокий хід через Claude як зовнішній процес.
    Історія вкладається в промпт текстовим транскриптом (субпроцес
    не тримає сесію між викликами), довга пам'ять — через --append-system-prompt.
    """
    # Транскрипт — усі попередні ходи; поточний промпт іде в кінці.
    prior = history[:-1] if history else []
    full_prompt = prompt
    if prior:
        full_prompt = (
            "Контекст розмови:\n" + to_transcript(prior) +
            "\n\nПоточне повідомлення:\n" + prompt
        )

    cmd = ["claude", "-p", "--model", DEEP_MODEL,
           "--append-system-prompt", system]
    if with_tools and DEEP_TOOLS:
        cmd += ["--allowedTools", ",".join(DEEP_TOOLS)]
    cmd.append(full_prompt)

    if not live:
        return f"(dry-run deep: transcript={len(prior)} turns, tools={with_tools})"
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


# === Канал вводу ============================================================
# Простий канал на вхід: тіки крутяться безперервно, а повідомлення
# користувача надходять асинхронно й підхоплюються на найближчому тіку.

import queue
import sys
import threading


class ScriptedChannel:
    """Детермінований канал для тестів: ввід прив'язаний до номерів тіків."""

    def __init__(self, inputs: dict[int, str] | None = None):
        self._inputs = inputs or {}
        self._tick = -1

    def poll(self) -> str | None:
        self._tick += 1
        return self._inputs.get(self._tick)


class StdinChannel:
    """
    Живий канал: фоновий потік-демон читає stdin і кладе рядки в чергу.
    `poll()` неблокуюче забирає черговий рядок (або None, якщо порожньо),
    тож цикл тіків не зупиняється в очікуванні вводу.
    """

    def __init__(self):
        self._q: queue.Queue[str] = queue.Queue()
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self) -> None:
        while True:
            line = sys.stdin.readline()
            if line == "":          # EOF
                break
            line = line.strip()
            if line:
                self._q.put(line)

    def poll(self) -> str | None:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None


# === Системні слеш-команди ==================================================
# Перехоплюють ввід ДО класифікації, тож не йдуть у мозок як повідомлення.
# handle_command() повертає:
#   "handled" — команда виконана, продовжуємо цикл;
#   "quit"    — користувач просить вийти;
#   None      — це не команда, далі звичайна обробка.

def _fmt_needs(state: State) -> str:
    return "  ".join(f"{k}={state.needs[k]:.2f}" for k in state.needs)


def handle_command(line: str, state: State, history: list[dict],
                   system: str, live: bool) -> str | None:
    if not line.startswith("/"):
        return None

    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("quit", "exit", "q"):
        return "quit"

    elif cmd in ("help", "h", "?"):
        print("Команди: /status  /needs  /memory  /history  /ask <текст>  "
              "/clear  /help  /quit")

    elif cmd == "status":
        name, level = state.hottest_need()
        print(f"[status] ходів={len(history)}  найгарячіша={name}={level:.2f}  "
              f"режим={'live' if live else 'dry'}")
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
        if not arg:
            print("[ask] вкажи текст: /ask <питання>")
        else:
            history.append({"role": ROLE_USER, "text": arg})
            reply = deep_reply(arg, live, with_tools=False, history=history, system=system)
            history.append({"role": ROLE_BOT, "text": reply})
            apply_satiation(state, "deep")
            print(f"[ask -> THINK/{DEEP_MODEL.split('-')[1]}]: {reply}")

    elif cmd == "clear":
        history.clear()
        print("[clear] історію сесії очищено")

    else:
        print(f"[?] невідома команда: /{cmd} (спробуй /help)")

    return "handled"


# === Двіжок (цикл) ==========================================================

def respond(prompt: str, state: State, live: bool, history: list[dict],
            system: str, force: str | None = None) -> dict:
    # force ("chat"|"deep") задає гілку напряму (для self-тригерів),
    # інакше — звичайна класифікація.
    cls = force if force else classify(prompt, state)

    # Поточний хід користувача — у спільну історію перед викликом.
    history.append({"role": ROLE_USER, "text": prompt})

    if cls == "chat":
        reply = chat_reply(prompt, live, history, system)
        route = f"CHAT/{CHAT_MODEL.split('-')[1]}"      # напр. CHAT/haiku
        event = "chat"
    elif cls in ("think", "deep"):
        reply = deep_reply(prompt, live, with_tools=False, history=history, system=system)
        route = f"THINK/{DEEP_MODEL.split('-')[1]}"      # напр. THINK/opus
        event = "deep"
    else:  # tools
        reply = deep_reply(prompt, live, with_tools=True, history=history, system=system)
        route = f"TOOLS/{DEEP_MODEL.split('-')[1]}"
        event = "deep"

    # Відповідь — теж у історію.
    history.append({"role": ROLE_BOT, "text": reply})

    # Гілка визначає, які потреби закрились.
    apply_satiation(state, event)
    return {"class": cls, "route": route, "reply": reply}


def run(ticks: int | None = 12, live: bool = False, channel=None) -> None:
    """
    Цикл тіків. `channel.poll()` дає чергове повідомлення користувача або None.
    ticks=None -> крутитися безкінечно (для живого StdinChannel).
    """
    if channel is None:
        channel = ScriptedChannel()
    STATE_DIR.mkdir(parents=True, exist_ok=True)   # каталог стану має існувати для запису
    state = load_state()
    history: list[dict] = []          # спільна стрічка розмови на сесію
    started = _dt.datetime.now().isoformat(timespec="seconds")  # старт сесії (для транскрипту)
    canon = load_canon()              # персона/голос зі state/canon.md
    memory = load_memory()            # довга пам'ять з минулих сесій
    system = build_system(canon, memory)  # канон + пам'ять
    prompts = load_prompts()          # промпти self-тригера зі state/prompts.md
    tg = TriggerBook()                # гістерезис + кулдаун тригерів

    t = 0
    try:
        while ticks is None or t < ticks:
            drift(state)
            user_msg = channel.poll()
            # Пріоритет: ВВІД важливіший за self-тригер. Якщо є і те, і те —
            # цього тіку обробляємо ввід; тригер перевіримо наступного тіку.
            fired, faction = (None, None)
            if user_msg is None:
                fired, faction = select_self_trigger(state, tg)

            if user_msg is not None:
                action = handle_command(user_msg, state, history, system, live)
                if action == "quit":
                    print("[exit] вихід за командою")
                    break
                elif action == "handled":
                    pass                       # команда оброблена, мозок не чіпаємо
                else:
                    out = respond(user_msg, state, live, history, system)
                    print(f"[t{t:02d}] user: {user_msg!r} -> {out['route']}: {out['reply']}")
            elif fired is not None:
                prompt = pick_prompt(prompts, fired)
                out = respond(prompt, state, live, history, system, force=faction)
                print(f"[t{t:02d}] self ({fired}->{faction}) -> {out['route']}: {out['reply']}")
            else:
                apply_satiation(state, "idle")     # тиша: відпочинок + вистигання
                name, level = state.hottest_need()
                print(f"[t{t:02d}] tick (no brain) — hottest: {name}={round(level,2)}")

            time.sleep(TICK_SECONDS if live else 0)
            t += 1
    finally:
        save_state(state)
        if history:
            # Сирий транскрипт зберігаємо ПЕРШИМ — він найважливіший (для RAG)
            # і не має залежати від (можливо невдалого) виклику summarize.
            session_path = save_session(history, live, started)
            summary = summarize(history, live)
            save_summary(summary)
            print(f"[exit] збережено підсумок ({len(history)} ходів) -> {MEMORY_FILE.name}; "
                  f"транскрипт -> history/{session_path.name}")


if __name__ == "__main__":
    live = os.environ.get("KILN_LIVE") == "1"
    if live:
        # Живий режим: пиши повідомлення в термінал, тіки крутяться самі.
        print("kiln: пиши повідомлення (Ctrl-C щоб вийти)…")
        try:
            run(ticks=None, live=True, channel=StdinChannel())
        except KeyboardInterrupt:
            print("\nпока.")
    else:
        # Демо 1 (dry-run): чат, команди й вихід.
        run(ticks=12, live=False, channel=ScriptedChannel({
            1: "привіт, як справи?",          # -> chat / haiku
            2: "/status",                      # системна команда
            4: "поясни, чому так виходить",    # -> think / opus
            5: "/history",                     # показати стрічку
            6: "/needs",                       # стан потреб
            8: "/quit",                        # вихід
        }))

        # Демо 2 (dry-run): self-тригер. Піднімаємо novelty над порогом —
        # двіжок озивається сам через deep, потім кулдаун тримає тишу.
        print("\n--- демо self-тригера (novelty над порогом) ---")
        import copy
        state = load_state()
        state.needs["novelty"] = 0.92
        save_state(state)
        run(ticks=8, live=False, channel=ScriptedChannel({}))
        # повертаємо спокійний дефолт
        st = load_state(); st.needs["novelty"] = 0.25; save_state(st)
