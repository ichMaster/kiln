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

Обидва мозки — за seam'ом brain.Brain (LiveBrain — справжні виклики SDK/CLI;
MockBrain — для dry-run і тестів), тож ядро (respond/run) не залежить ні від SDK,
ні від CLI напряму. Решта коду — у модулях: config (сталі), history (стрічка),
usage (облік/кольори), memory (довга пам'ять + транскрипти), commands (слеш-команди).
"""

from __future__ import annotations

import datetime as _dt
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .brain import Brain, LiveBrain, MockBrain
from .commands import handle_command
from .config import (
    CHAT_MODEL,
    DEEP_MODEL,
    DRIFT,
    MEMORY_FILE,
    NEED_TRIGGERS,
    SATIATION,
    SELF_COOLDOWN,
    STATE_DIR,
    THINK_HINTS,
    THINK_THRESHOLD,
    TICK_SECONDS,
    TOOL_HINTS,
)
from .history import ROLE_BOT, ROLE_USER
from .memory import (
    build_system,
    load_canon,
    load_memory,
    load_prompts,
    pick_prompt,
    save_session,
    save_summary,
    summarize,
)
from .output import ConsoleOutput, Output

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


def load_state(state_dir: Path = STATE_DIR) -> State:
    """Читає потреби зі state/needs.json ({need: рівень}); порожній стан, якщо файлу нема."""
    path = state_dir / "needs.json"
    if not path.exists():
        return State()
    data = json.loads(path.read_text(encoding="utf-8"))
    needs = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    return State(needs=needs)


def save_state(state: State, state_dir: Path = STATE_DIR) -> None:
    """Пише потреби у state/needs.json ({need: рівень}, округлені до 3 знаків)."""
    data = {k: round(v, 3) for k, v in state.needs.items()}
    (state_dir / "needs.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# === Тік ====================================================================


def drift(state: State, ticks: int = 1) -> None:
    """Кожна потреба росте на DRIFT[k] × ticks (з відсіканням на 1.0).
    ticks > 1 — «надолуження» дрейфу за реальний час, що минув під час
    блокуючого виклику моделі (див. цикл у run)."""
    for k in state.needs:
        state.needs[k] = min(1.0, state.needs[k] + DRIFT.get(k, 0.0) * ticks)


def apply_satiation(state: State, event: str) -> None:
    """Закриває потреби за подією ('chat' | 'deep' | 'idle'), відсікаючи на 0."""
    for k, delta in SATIATION.get(event, {}).items():
        if k in state.needs:
            state.needs[k] = max(0.0, state.needs[k] + delta)


@dataclass
class TriggerBook:
    """Рантайм-стан тригерів (не зберігається): гістерезис + кулдаун на потребу."""

    armed: dict[str, bool] = field(default_factory=dict)  # готова спрацювати?
    cooldown: dict[str, int] = field(default_factory=dict)  # лишилось тіків тиші


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
            tg.armed[name] = True  # переозброїти нижче порога
        elif tg.armed.get(name, True) and tg.cooldown.get(name, 0) == 0:
            eligible.append((level - cfg["threshold"], name, cfg["action"]))

    if not eligible:
        return None, None

    eligible.sort(reverse=True)  # найбільший overshoot перший
    _, name, action = eligible[0]
    tg.armed[name] = False  # розрядити гістерезис
    tg.cooldown[name] = SELF_COOLDOWN  # завести кулдаун
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


# === Канал вводу ============================================================
# Простий канал на вхід: тіки крутяться безперервно, а повідомлення
# користувача надходять асинхронно й підхоплюються на найближчому тіку.


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
            if line == "":  # EOF
                break
            line = line.strip()
            if line:
                self._q.put(line)

    def poll(self) -> str | None:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None


# === Двіжок (цикл) ==========================================================


def respond(
    prompt: str,
    state: State,
    history: list[dict],
    system: str,
    brain: Brain,
    force: str | None = None,
) -> dict:
    # force ("chat"|"deep") задає гілку напряму (для self-тригерів і /ask),
    # інакше — звичайна класифікація. Модель кличемо ЛИШЕ через brain (seam):
    # ядро не знає ні про SDK, ні про CLI. usage приходить разом із текстом.
    cls = force if force else classify(prompt, state)

    # Поточний хід користувача — у спільну історію перед викликом.
    history.append({"role": ROLE_USER, "text": prompt})

    if cls == "chat":
        reply, usage = brain.chat(history, system)
        route = f"CHAT/{CHAT_MODEL.split('-')[1]}"  # напр. CHAT/haiku
        event = "chat"
    elif cls in ("think", "deep"):
        reply, usage = brain.deep(prompt, history, system, with_tools=False)
        route = f"THINK/{DEEP_MODEL.split('-')[1]}"  # напр. THINK/opus
        event = "deep"
    else:  # tools
        reply, usage = brain.deep(prompt, history, system, with_tools=True)
        route = f"TOOLS/{DEEP_MODEL.split('-')[1]}"
        event = "deep"

    # Відповідь — теж у історію.
    history.append({"role": ROLE_BOT, "text": reply})

    # Гілка визначає, які потреби закрились.
    apply_satiation(state, event)
    return {"class": cls, "route": route, "reply": reply, "usage": usage}


def run(
    ticks: int | None = 12,
    live: bool = False,
    channel=None,
    brain: Brain | None = None,
    output: Output | None = None,
) -> None:
    """
    Цикл тіків. `channel.poll()` дає чергове повідомлення користувача або None.
    ticks=None -> крутитися безкінечно (для живого StdinChannel).
    `brain` за замовчанням: LiveBrain наживо, MockBrain у dry-run (нуль платних
    викликів). `output` за замовчанням: ConsoleOutput (друк у термінал) — ядро
    пише репліки лише через цей порт, тож інтерфейс (TUI/шина) підмінний.
    """
    if channel is None:
        channel = ScriptedChannel()
    if brain is None:
        brain = LiveBrain() if live else MockBrain()
    if output is None:
        output = ConsoleOutput()
    STATE_DIR.mkdir(parents=True, exist_ok=True)  # каталог стану має існувати для запису
    state = load_state()
    history: list[dict] = []  # спільна стрічка розмови на сесію
    started = _dt.datetime.now().isoformat(timespec="seconds")  # старт сесії (для транскрипту)
    canon = load_canon()  # персона/голос зі state/canon.md
    memory = load_memory()  # довга пам'ять з минулих сесій
    system = build_system(canon, memory)  # канон + пам'ять
    prompts = load_prompts()  # промпти self-тригера зі state/prompts.md
    tg = TriggerBook()  # гістерезис + кулдаун тригерів

    t = 0
    last_tick = time.monotonic()  # для надолуження дрейфу за реальним часом
    try:
        while ticks is None or t < ticks:
            # Виклик моделі блокує цикл, тож одна ітерація може тривати багато
            # секунд. Рахуємо, скільки тіків РЕАЛЬНО минуло, і даємо стільки ж
            # дрейфу (надолуження). У dry-run час «не тече» — рівно 1 тік.
            now = time.monotonic()
            elapsed = now - last_tick
            last_tick = now
            steps = max(1, round(elapsed / TICK_SECONDS)) if (live and TICK_SECONDS > 0) else 1
            drift(state, steps)  # надолуження дрейфу за реальним часом (тихо)
            user_msg = channel.poll()
            # Пріоритет: ВВІД важливіший за self-тригер. Якщо є і те, і те —
            # цього тіку обробляємо ввід; тригер перевіримо наступного тіку.
            fired, faction = (None, None)
            if user_msg is None:
                fired, faction = select_self_trigger(state, tg)

            if user_msg is not None:
                action = handle_command(user_msg, state, history, system, live, output)
                if action == "quit":
                    output.notice("[exit] вихід за командою")
                    break
                elif action == "handled":
                    pass  # команда оброблена, мозок не чіпаємо
                elif isinstance(action, tuple):  # ("ask", текст) -> примусовий deep
                    out = respond(action[1], state, history, system, brain, force="deep")
                    output.agent(out["reply"], lead=True)
                    output.usage(out.get("usage"))
                else:  # None -> звичайний хід
                    out = respond(user_msg, state, history, system, brain)
                    output.user(user_msg)
                    output.agent(out["reply"])
                    output.usage(out.get("usage"))
            elif fired is not None:
                prompt = pick_prompt(prompts, fired)
                out = respond(prompt, state, history, system, brain, force=faction)
                output.agent(out["reply"], is_self=True)
                output.usage(out.get("usage"))
            else:
                apply_satiation(state, "idle")  # тиша: відпочинок + вистигання
                # тихий тік не друкуємо — стан дивись через /status

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
            output.notice(
                f"[exit] збережено підсумок ({len(history)} ходів) -> {MEMORY_FILE.name}; "
                f"транскрипт -> history/{session_path.name}"
            )
