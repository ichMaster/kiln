"""
kiln — the need substrate + triggers (the FSM's variables/datapath). `State` (the live need levels,
0..1) and its persistence; `drift` (needs rise/fall each tick) + `apply_satiation` (an event closes
needs); the `TriggerBook` (hysteresis + per-need cooldown, not persisted) + the trigger selectors —
`select_self_trigger` (the connection reach-out), `select_thought_trigger` (the inner monologue),
`update_curiosity_monitor` (arms the question monitor) — plus `reach_out_branch` (which brain a
reach-out uses). A leaf on `config` (the need MODEL: drift/satiation/thresholds); no engine imports.
Each helper takes an optional per-agent `config` (None → the module-global calibration).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    DRIFT,
    NEED_TRIGGERS,
    NEEDS_LEVELS_FILE,
    REACH_OUT_MODELS,
    REACH_OUT_NEED,
    REFLECT_NEED,
    SATIATION,
    SELF_COOLDOWN,
    THOUGHT_COOLDOWN,
    AgentConfig,
)


@dataclass
class State:
    needs: dict[str, float] = field(default_factory=dict)
    self_messages: bool = True  # proactive reach-outs on? (/self toggle; per-session, not saved)

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


def load_state(path: Path = NEEDS_LEVELS_FILE) -> State:
    """Reads the live need LEVELS from .kiln/needs.json ({need: level}); empty state if the file is
    missing. Any configured need (a `DRIFT` key) absent from the file is healed in at 0.0 — so a new
    need (e.g. v0.10 `reflection`, v0.11 `curiosity`) appears in the TUI/commands and drifts, no
    re-seed. (The need MODEL — drift/satiation/triggers — is separate: state/needs_model.yaml.)"""
    if not path.exists():
        return State()
    data = json.loads(path.read_text(encoding="utf-8"))
    needs = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    for need in DRIFT:  # heal: a configured need missing from the file starts at 0.0
        needs.setdefault(need, 0.0)
    return State(needs=needs)


def save_state(state: State, path: Path = NEEDS_LEVELS_FILE) -> None:
    """Writes the live need LEVELS to .kiln/needs.json ({need: level}, rounded to 3 decimals)."""
    data = {k: round(v, 3) for k, v in state.needs.items()}
    path.parent.mkdir(parents=True, exist_ok=True)  # .kiln[/{id}] may not exist on a fresh run
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def drift(state: State, ticks: int = 1, config: AgentConfig | None = None) -> None:
    """Each need grows by DRIFT[k] × ticks (clamped to 1.0).
    ticks > 1 — "catch-up" drift for real time that elapsed during a
    blocking model call (see the loop in run).
    `config` (v1.2): the per-agent need model; None → the module global DRIFT (the agnika default,
    still monkeypatchable in tests). run() threads its config; direct callers omit it."""
    dmap = config.drift if config is not None else DRIFT
    for k in state.needs:
        state.needs[k] = min(1.0, state.needs[k] + dmap.get(k, 0.0) * ticks)


def apply_satiation(state: State, event: str, config: AgentConfig | None = None) -> None:
    """Closes needs per the event ('chat' | 'deep' | 'idle'), clamping at 0.
    `config` (v1.2): the per-agent satiation map; None → the module global SATIATION."""
    sat = config.satiation if config is not None else SATIATION
    for k, delta in sat.get(event, {}).items():
        if k in state.needs:
            state.needs[k] = max(0.0, state.needs[k] + delta)


@dataclass
class TriggerBook:
    """Runtime trigger state (not persisted): hysteresis + per-need cooldown."""

    armed: dict[str, bool] = field(default_factory=dict)  # ready to fire?
    cooldown: dict[str, int] = field(default_factory=dict)  # silent ticks remaining
    curiosity_monitor: bool = False  # v0.11: ON between a curiosity crossing and falling below it


def _crossing_trigger(
    state: State, tg: TriggerBook, name: str, cooldown: int, config: AgentConfig | None = None
) -> str | None:
    """Fire `name` on an UPWARD threshold crossing (`NEED_TRIGGERS`) with hysteresis — fires only on
    the up-crossing, re-arms once it falls back below — and a `cooldown` of silent ticks after.
    Returns the need name when it fires this tick, else None.
    `config` (v1.2): the per-agent triggers; None → the module global NEED_TRIGGERS."""
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    cfg = triggers.get(name)
    if cfg is None:
        return None
    if tg.cooldown.get(name, 0) > 0:
        tg.cooldown[name] -= 1
    if state.needs.get(name, 0.0) < cfg["threshold"]:
        tg.armed[name] = True  # re-arm below threshold
        return None
    if not tg.armed.get(name, True) or tg.cooldown.get(name, 0) != 0:
        return None  # already discharged this crossing, or still cooling down
    tg.armed[name] = False  # discharge hysteresis
    tg.cooldown[name] = cooldown  # start cooldown
    return name


def select_self_trigger(
    state: State, tg: TriggerBook, config: AgentConfig | None = None
) -> str | None:
    """The proactive reach-out fires ONLY on REACH_OUT_NEED (connection = loneliness): returns that
    need name when it crosses its threshold this tick, else None. WHICH brain answers is a separate
    choice (reach_out_branch). Hysteresis + SELF_COOLDOWN silent ticks after firing.
    `config` (v1.2): the per-agent reach-out need + cooldown; None → the module globals."""
    reach = config.reach_out_need if config is not None else REACH_OUT_NEED
    cooldown = config.self_cooldown if config is not None else SELF_COOLDOWN
    return _crossing_trigger(state, tg, reach, cooldown, config)


def _thought_visible(every: int) -> bool:
    """Whether a freshly formed thought surfaces in the chat — ~1/`every` via the stdlib RNG
    (seedable / monkeypatchable in tests). `every <= 0` → never shown."""
    return every > 0 and random.random() < (1.0 / every)


def select_thought_trigger(
    state: State, tg: TriggerBook, config: AgentConfig | None = None
) -> str | None:
    """The inner monologue fires on REFLECT_NEED (reflection = незібраність): returns it on an
    upward crossing this tick (else None), with the same hysteresis + THOUGHT_COOLDOWN as the
    reach-out. The thought itself (KILN-042) is generated separately — this only decides WHEN.
    `config` (v1.2): the per-agent reflect need + cooldown; None → the module globals."""
    reflect = config.reflect_need if config is not None else REFLECT_NEED
    cooldown = config.thought_cooldown if config is not None else THOUGHT_COOLDOWN
    return _crossing_trigger(state, tg, reflect, cooldown, config)


def update_curiosity_monitor(
    state: State, tg: TriggerBook, config: AgentConfig | None = None
) -> bool:
    """v0.11: curiosity's "trigger" — an upward crossing of its threshold **enables the monitor**
    (`tg.curiosity_monitor`); falling back below disables it. Unlike a reach-out/thought it sends
    nothing — it just gates whether a `?` reply discharges curiosity (the monitor, in `_turn`).
    Run every tick (after drift). Returns the monitor state. No `NEED_TRIGGERS` entry → off.
    `config` (v1.2): the per-agent triggers; None → the module global NEED_TRIGGERS."""
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    cfg = triggers.get("curiosity")
    if cfg is None:
        tg.curiosity_monitor = False
        return False
    if _crossing_trigger(
        state, tg, "curiosity", 0, config
    ):  # an upward crossing -> arm the monitor
        tg.curiosity_monitor = True
    elif state.needs.get("curiosity", 0.0) < cfg["threshold"]:  # fell below -> disarm
        tg.curiosity_monitor = False
    return tg.curiosity_monitor


def reach_out_branch(state: State, config: AgentConfig | None = None) -> tuple[str, str | None]:
    """
    WHICH brain answers a connection reach-out, shaped by her OTHER needs at fire time:
    the first REACH_OUT_MODELS need over its threshold wins (intensity -> deep/opus, novelty
    -> session-wiki), else the reach-out need's baseline (chat). So opus/session-wiki never
    self-INITIATE — they only shape a connection-driven message. Returns (action, agent).
    `config` (v1.2): the per-agent reach-out models/triggers/need; None → the module globals.
    """
    models = config.reach_out_models if config is not None else REACH_OUT_MODELS
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    reach = config.reach_out_need if config is not None else REACH_OUT_NEED
    for name in models:
        cfg = triggers.get(name, {})
        if state.needs.get(name, 0.0) >= cfg.get("threshold", 2.0):
            return cfg.get("action", "chat"), cfg.get("agent")
    base = triggers.get(reach, {})
    return base.get("action", "chat"), base.get("agent")
