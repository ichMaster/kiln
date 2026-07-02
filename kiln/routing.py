"""
kiln — turn classification + the brain-call orchestration. `classify` routes a user turn to a class
(`chat | think | tools | tool`) from message markers + ambient high needs; `turn_weight` is its
state weight; `is_curiosity_reply` decides whether a reply "asks" (discharges curiosity). `respond`
one turn: it picks the branch (the forced class, or `classify`), calls the model **only through the
`Brain` seam**, appends both turns to `history`, and applies the branch's satiation — returning
`{class, route, reply, usage}`. Depends on `needs` (State, apply_satiation) + `config`/`brain`/
`history`; no engine import (the loop imports this).
"""

from __future__ import annotations

from .brain import Brain
from .config import (
    AGENT_NAME,
    CHAT_MODEL,
    DEEP_MODEL,
    NEED_TRIGGERS,
    REACH_OUT_MODELS,
    SATIATION,
    THINK_HINTS,
    THINK_THRESHOLD,
    TOOL_HINTS,
    AgentConfig,
)
from .history import ROLE_BOT, ROLE_USER, strip_leading_name, turn
from .needs import State, apply_satiation


def turn_weight(state: State) -> float:
    """Turn weight ~0..1 from state. Higher -> closer to deep reasoning."""
    return max(0.0, min(1.0, 0.55 * state.intensity + 0.45 * state.connection))


def classify(
    prompt: str, state: State, config: AgentConfig | None = None
) -> tuple[str, str | None]:
    """
    Route a USER turn to (class, agent), class ∈ 'chat'|'think'|'tools'|'tool'.

    Priority:
      1. explicit tool markers -> 'tools' (deep + --allowedTools);
      2. explicit reasoning markers -> 'think';
      3. ambient HIGH NEEDS pick the model the same way a self-trigger does
         (reach_out_branch / REACH_OUT_MODELS): intensity over its threshold -> 'deep'
         (opus); else novelty over its -> 'tool' (session-wiki). So when she's intense or
         curious, even a plain user turn gets the deeper brain, not cheap chat;
      4. a high state weight -> 'think';
      5. otherwise -> 'chat'.

    `config` (v1.2): the per-agent reach-out models / triggers / think-threshold; None → the module
    globals. The TOOL_HINTS / THINK_HINTS markers stay global (persona-layer Ukrainian words).
    """
    models = config.reach_out_models if config is not None else REACH_OUT_MODELS
    triggers = config.need_triggers if config is not None else NEED_TRIGGERS
    threshold = config.think_threshold if config is not None else THINK_THRESHOLD
    low = prompt.lower()
    if any(h in low for h in TOOL_HINTS):
        return "tools", None
    if any(h in low for h in THINK_HINTS):
        return "think", None
    for name in models:  # high need -> deeper brain (same map as reach_out_branch)
        cfg = triggers.get(name, {})
        if state.needs.get(name, 0.0) >= cfg.get("threshold", 2.0):
            return cfg.get("action", "chat"), cfg.get("agent")
    if turn_weight(state) >= threshold:
        return "think", None
    return "chat", None


# v0.11 curiosity: she "asks" when the reply carries a question — a `?` or a leading Ukrainian
# interrogative as its first word. Pure heuristic (a model judge may refine it later); it gates the
# curiosity discharge (she acted on the nudge -> sated).
_QUESTION_WORDS = frozenset(
    {
        "чому",
        "що",
        "як",
        "коли",
        "де",
        "хто",
        "навіщо",
        "чи",
        "чим",
        "кого",
        "кому",
        "який",
        "яка",
        "яке",
        "які",
        "скільки",
        "куди",
        "звідки",
    }
)


def is_curiosity_reply(text: str) -> bool:
    """True when a reply actually ASKS — it contains a question mark, or its first word is a
    Ukrainian interrogative. Pure; used to discharge curiosity (KILN-047)."""
    if "?" in text:
        return True
    words = text.lstrip().lower().split(maxsplit=1)
    first = words[0].strip(".,!?;:—-«»\"'") if words else ""
    return first in _QUESTION_WORDS


def respond(
    prompt: str,
    state: State,
    history: list[dict],
    system: str,
    brain: Brain,
    force: str | None = None,
    agent: str | None = None,
    config: AgentConfig | None = None,
) -> dict:
    # force ("chat"|"deep"|"tool") picks the branch directly (for self-triggers and /ask),
    # otherwise classify() routes the user turn (and may name the "tool" sub-agent). We call
    # the model ONLY through brain (seam): the core knows nothing about the SDK or the CLI.
    # `config` (v1.2): the per-agent models + satiation; None → the module globals.
    chat_model = config.chat_model if config is not None else CHAT_MODEL
    deep_model = config.deep_model if config is not None else DEEP_MODEL
    sat = config.satiation if config is not None else SATIATION
    name = (
        config.agent_name if config is not None else AGENT_NAME
    )  # strip THIS agent's echoed label
    if force:
        cls = force
    else:
        cls, agent = classify(prompt, state, config)

    # The user's current turn goes into the shared history before the call (timestamped, v0.8).
    history.append(turn(ROLE_USER, prompt))

    if cls == "chat":
        reply, usage = brain.chat(history, system)
        route = f"CHAT/{chat_model.split('-')[1]}"  # e.g. CHAT/haiku
        event = "chat"
    elif cls == "tool":
        # A "tool" self-trigger runs a named Claude Code sub-agent (e.g. novelty ->
        # session-wiki, an external Wikipedia fact). Satiation is PER-AGENT: if SATIATION has
        # an entry keyed by the agent name it's used (so session-wiki drops novelty on its
        # own terms), else it falls back to a deep "filling meal". NB: distinct from the
        # "tools" class below (deep + --allowedTools); here the whole turn is a sub-agent.
        reply, usage = brain.tool(agent or "", history, system)
        route = f"TOOL/{agent}"  # e.g. TOOL/session-wiki (the agent IS the trace label)
        event = agent if agent in sat else "deep"
    elif cls in ("think", "deep"):
        reply, usage = brain.deep(prompt, history, system, with_tools=False)
        route = f"THINK/{deep_model.split('-')[1]}"  # e.g. THINK/opus
        event = "deep"
    else:  # tools (v1.4): the armed branch is retired — fire the `hands` sub-agent (workspace-
        # jailed, gated by the agent's profile) instead of arming the main prompt with tools.
        reply, usage = brain.tool("hands", history, system)
        route = "TOOLS/hands"
        event = "deep"  # hands is a "filling meal" like deep — closes novelty/rest/intensity

    # Strip a leading name the model echoed (it mirrors the timeline's "Name:" labels) — clean
    # for both display and storage, so it never shows and never compounds in the next timeline.
    reply = strip_leading_name(reply, name)
    # The reply goes into the history too (timestamped, v0.8).
    history.append(turn(ROLE_BOT, reply))

    # The branch determines which needs were closed.
    apply_satiation(state, event, config)
    return {"class": cls, "route": route, "reply": reply, "usage": usage}
