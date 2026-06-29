"""
kiln — configuration: paths, .env, and calibration knobs.

Kept separate so all modules (engine, memory, commands) can import the
constants without circular dependencies (engine runs as __main__).
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths ------------------------------------------------------------------
# This module lives inside the kiln/ package, so the project root is the
# PARENT of the package (not the package itself). All mutable data (state/,
# history/, .env) lives at the root. The root can be overridden with the
# KILN_HOME variable (e.g. to keep state outside the repo).
_PKG_DIR = Path(__file__).resolve().parent  # .../kiln/kiln (package)
PROJECT_ROOT = Path(os.environ.get("KILN_HOME", _PKG_DIR.parent))  # repo root by default

STATE_DIR = PROJECT_ROOT / "state"
MEMORY_FILE = STATE_DIR / "memory.md"  # long-term memory: summaries of past sessions
CANON_FILE = STATE_DIR / "canon.md"  # canon: persona/voice (system prompt)
PROMPTS_FILE = STATE_DIR / "prompts.md"  # self-trigger prompts per need
MOOD_FILE = STATE_DIR / "mood.json"  # v0.9: need/biorhythm bands, labels, behavioural cues
NEEDS_FILE = STATE_DIR / "needs.yaml"  # the need MODEL: drift/satiation/triggers + scalars (config)
HISTORY_DIR = PROJECT_ROOT / "history"  # raw session transcripts (JSON, for RAG)
ENV_FILE = PROJECT_ROOT / ".env"  # local configuration (models + calibration)

KILN_DIR = PROJECT_ROOT / ".kiln"  # unified store dir (Lumi-style; shared with later phases)
STORE_FILE = KILN_DIR / "store.json"  # the single persistence store (sessions/messages/summaries)
USAGE_LEDGER = KILN_DIR / "usage-ledger.jsonl"  # v0.7: one append-only line per closed session
USAGE_REPORT_FILE = KILN_DIR / "usage-report.md"  # v0.7: the generated Markdown cost report


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Minimal .env loader (KEY=VALUE) with no third-party dependencies.
    Supports whole-line and inline (` # ...`) comments. Real environment
    variables take priority (setdefault does not overwrite already-set ones)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        val = val.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


load_dotenv()  # read .env BEFORE the settings are defined below

# --- Ticks ------------------------------------------------------------------
TICK_SECONDS = float(os.environ.get("TICK_SECONDS", "0.5"))

# --- Needs (the motivational substrate) -------------------------------------
# The need MODEL — per-need drift, satiation (closing) events, trigger thresholds + the trigger
# wiring + cooldowns — lives in state/needs.yaml; edit THAT to tune Agnika. It's loaded here;
# DEFAULT_NEEDS is the fallback for a fresh clone / a broken edit / no PyYAML. (The runtime need
# LEVELS are separate — state/needs.json, save_state/load_state.) `need_triggers` action: chat ->
# Haiku, deep -> Opus, idle -> rest gate, tool -> sub-agent, thought -> v0.10 monologue, ask ->
# v0.11 curiosity. Only reach_out_need (connection) self-triggers a message; reflection/curiosity
# have an entry (threshold + panel display) but don't self-trigger.
DEFAULT_NEEDS = {
    "need_triggers": {
        "connection": {"threshold": 0.80, "action": "chat"},
        "rest": {"threshold": 0.90, "action": "idle"},
        "novelty": {"threshold": 0.85, "action": "tool", "agent": "session-wiki"},
        "intensity": {"threshold": 0.75, "action": "deep"},
        "reflection": {"threshold": 0.60, "action": "thought"},
        "curiosity": {"threshold": 0.65, "action": "ask"},
    },
    "drift": {
        "connection": 0.0010,
        "rest": -0.005,
        "novelty": 0.0001,
        "intensity": 0.0005,
        "reflection": 0.001,
        "curiosity": 0.002,
    },
    "satiation": {
        "chat": {"connection": -0.3, "rest": 0.2, "novelty": 0, "intensity": -0.001},
        "deep": {"connection": -0.6, "rest": 0.4, "novelty": -0.0015, "intensity": -0.80},
        "session-wiki": {"connection": -0.6, "rest": 0.4, "novelty": -0.90, "intensity": -0.15},
        "idle": {"connection": 0, "rest": -0.01, "novelty": 0.0001, "intensity": 0.0005},
        "thought": {"reflection": -0.7},
        "asked": {"curiosity": -0.4},
    },
    "reach_out_need": "connection",
    "reach_out_models": ["intensity", "novelty"],
    "reflect_need": "reflection",
    "self_cooldown": 5,
    "thought_cooldown": 5,
    "rest_wake": 0.85,
}


def load_needs(path: Path = NEEDS_FILE) -> dict:
    """The needs config from state/needs.yaml; DEFAULT_NEEDS if the file is missing/invalid or
    PyYAML isn't installed (a fresh clone / broken edit still starts)."""
    try:
        import yaml
    except ImportError:
        return DEFAULT_NEEDS
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return DEFAULT_NEEDS
    return data if isinstance(data, dict) else DEFAULT_NEEDS


def _build_needs(cfg: dict) -> dict:
    """Flatten + validate the needs config into the runtime constants; raises on a bad shape so the
    caller can fall back to DEFAULT_NEEDS."""
    return {
        "NEED_TRIGGERS": cfg["need_triggers"],
        "DRIFT": cfg["drift"],
        "SATIATION": cfg["satiation"],
        "REACH_OUT_NEED": cfg["reach_out_need"],
        "REACH_OUT_MODELS": tuple(cfg["reach_out_models"]),
        "REFLECT_NEED": cfg["reflect_need"],
        "SELF_COOLDOWN": int(cfg["self_cooldown"]),
        "THOUGHT_COOLDOWN": int(cfg["thought_cooldown"]),
        "REST_WAKE": float(cfg["rest_wake"]),
    }


try:
    _NEEDS = _build_needs(load_needs())
except (KeyError, TypeError, ValueError):
    _NEEDS = _build_needs(DEFAULT_NEEDS)  # a structurally malformed file falls back to the defaults

NEED_TRIGGERS = _NEEDS["NEED_TRIGGERS"]  # threshold + branch/mode per need (also the TUI panel)
DRIFT = _NEEDS["DRIFT"]  # per-tick growth per need
SATIATION = _NEEDS["SATIATION"]  # per-event need deltas (which branch closed what)
REACH_OUT_NEED = _NEEDS["REACH_OUT_NEED"]  # the ONLY need that self-triggers a proactive message
REACH_OUT_MODELS = _NEEDS["REACH_OUT_MODELS"]  # first over its threshold shapes the reach-out brain
REFLECT_NEED = _NEEDS["REFLECT_NEED"]  # v0.10: the need whose crossing fires an internal thought
SELF_COOLDOWN = _NEEDS["SELF_COOLDOWN"]  # silent ticks after a self-trigger (per need)
THOUGHT_COOLDOWN = _NEEDS["THOUGHT_COOLDOWN"]  # v0.10: silent ticks after an internal thought
REST_WAKE = _NEEDS["REST_WAKE"]  # rest falls below this -> she answers again (rest-gate hysteresis)

# Persona text tied to the rest gate / reach-out (stays in code — Ukrainian, intentional). On
# entering rest she says REST_MESSAGE once; SELF_SILENCE_NOTE is appended to a reach-out prompt
# when she already reached out and got no reply, so she doesn't robotically repeat herself.
REST_MESSAGE = "мені треба відпочити"
SELF_SILENCE_NOTE = (
    "(Ти вже озивалася першою, а відповіді ще нема. Не повторюйся: визнай тишу, "
    "зміни тон або просто побудь поруч одним коротким рядком.)"
)

# --- Classification / routing -----------------------------------------------
# Turn "weight" threshold: above -> the turn counts as reasoning, goes to Claude CLI.
THINK_THRESHOLD = float(os.environ.get("THINK_THRESHOLD", "0.45"))

CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-haiku-4-5-20251001")  # simple API for chat
DEEP_MODEL = os.environ.get("DEEP_MODEL", "claude-opus-4-8")  # Claude CLI for reasoning/tools

# v0.10 inner monologue: the thought runs on the chat brain (Haiku). THOUGHTS_ENABLED is the master
# switch; THOUGHT_MODEL is informational (= CHAT_MODEL — thoughts go through brain.chat).
# THOUGHT_VISIBLE_EVERY (M): a fresh thought surfaces in the chat with probability ~1/M (else stays
# internal). 1 -> always shown; large -> rarely.
THOUGHTS_ENABLED = os.environ.get("THOUGHTS_ENABLED", "1") == "1"
THOUGHT_MODEL = os.environ.get("THOUGHT_MODEL", CHAT_MODEL)
THOUGHT_VISIBLE_EVERY = int(os.environ.get("THOUGHT_VISIBLE_EVERY", "5"))
# How many recent (cross-session) thoughts go into the `## Думки` prompt section. 0 = off.
THOUGHTS_IN_PROMPT = int(os.environ.get("THOUGHTS_IN_PROMPT", "8"))

# EVERY `claude -p` call (deep, tool, summarize) runs with extended thinking ON — this is the
# budget passed as MAX_THINKING_TOKENS by claude_env(). Tune via .env; lower it for snappier
# interactive replies (thinking adds latency — it's a cap, the model uses up to this much).
THINKING_TOKENS = int(os.environ.get("THINKING_TOKENS", "8000"))

# v0.6 long memory: at start, all stored user `facts` are condensed (Opus) to at most this many
# lines for the `## Facts about the user` system-prompt section. Tune via .env.
FACTS_DIGEST_LINES = int(os.environ.get("FACTS_DIGEST_LINES", "8"))

# How many of the most recent session summaries load into the system prompt (load_memory):
# 0 = all (no cap); N = only the last N. Bounds prompt growth as the store accumulates sessions.
MEMORY_SUMMARIES = int(os.environ.get("MEMORY_SUMMARIES", "0"))

# Target length of each session summary, in sentences (the summarize prompt).
SUMMARY_SENTENCES = int(os.environ.get("SUMMARY_SENTENCES", "5"))

# How many of the most recent stored facts feed the start-time digest (digest_facts): 0 = all,
# N = only the last N. Bounds the per-start Opus input as facts accumulate (the digest OUTPUT is
# always capped at FACTS_DIGEST_LINES regardless). Stored facts themselves are never trimmed.
MAX_FACTS = int(os.environ.get("MAX_FACTS", "0"))

# Master switch for the user-facts layer (v0.6): 0 -> no extraction on close and no facts section
# in the prompt (stored facts are kept, just dormant). 1 -> on.
FACTS_ENABLED = os.environ.get("FACTS_ENABLED", "1") == "1"

# v0.7 usage reporting: 0 -> a session close writes no ledger line and no report (Lumi's
# `usage_report` flag). 1 -> on.
USAGE_REPORT = os.environ.get("USAGE_REPORT", "1") == "1"

# v0.8 world awareness: the user's location for the `## Зараз` section (empty = omitted).
USER_LOCATION = os.environ.get("USER_LOCATION", "")
# Optional IANA timezone for the world clock (e.g. "Europe/Kyiv"); empty = the machine's local time.
TIMEZONE = os.environ.get("TIMEZONE", "")
# How many recent turns go into the `## Повідомлення з минулої сесії` block (timestamped); 0 = off.
RECENT_MESSAGES = int(os.environ.get("RECENT_MESSAGES", "10"))
# Master switch for the world block (## Зараз + ## Повідомлення з минулої сесії); 0 = off.
WORLD_AWARENESS = os.environ.get("WORLD_AWARENESS", "1") == "1"

# Speaker names used to label turns in transcripts / the timeline / `/prompt` (persona layer).
USER_NAME = os.environ.get("USER_NAME", "Користувач")
AGENT_NAME = os.environ.get("AGENT_NAME", "Агніка")

# v0.9: Agnika's birthday seeds the daily biorhythm. Empty -> parsed from the canon's natal line
# (else the DEFAULT_BIRTH fallback in memory.py); set to override (`DD.MM.YYYY[ HH:MM]`).
AGENT_BIRTH = os.environ.get("AGENT_BIRTH", "")
# v0.9 mood: the per-turn `## Настрій` block (needs + biorhythm). MOOD_AWARENESS = master switch;
# BIORHYTHM toggles just the biorhythm sub-block within it.
MOOD_AWARENESS = os.environ.get("MOOD_AWARENESS", "1") == "1"
BIORHYTHM = os.environ.get("BIORHYTHM", "1") == "1"

# v0.11 curiosity is a NORMAL need: it drifts (DRIFT["curiosity"]), shows in `## Настрій` with a
# behavioural cue per band (state/mood.json), and is discharged by the SATIATION["asked"] event —
# fired by the question monitor (engine.is_curiosity_reply) when a reply actually asks. It does NOT
# self-trigger (not in NEED_TRIGGERS); the band cues, not a threshold, shape when she asks.

# Tools/skills allowed on the reasoning branch (example).
DEEP_TOOLS = ["Read", "Write", "Bash"]
DEEP_SKILLS: list[str] = []  # e.g. ["search", "summarize"]


def claude_env(**extra: str) -> dict:
    """Environment for a `claude -p` subprocess. Two invariants for EVERY claude -p call:
    ANTHROPIC_API_KEY is REMOVED (the CLI bills via its OWN login — Opus/Sonnet never run on the
    API key), and extended thinking is ON (MAX_THINKING_TOKENS=THINKING_TOKENS). `extra` overrides
    (e.g. a bigger MAX_THINKING_TOKENS for one call)."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["MAX_THINKING_TOKENS"] = str(THINKING_TOKENS)
    env.update(extra)
    return env


# "tool" self-triggers run a named Claude Code sub-agent (see NEED_TRIGGERS and
# brain.LiveBrain.tool): `claude -p --agent <agent>` loads .claude/agents/<agent>.md,
# whose frontmatter supplies the model and allowed tools — kiln only needs the directory.
AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"

# Canon (persona/voice) is taken from state/canon.md; this is just a fallback.
DEFAULT_CANON = (
    "Ти — співрозмовник цього чат-двіжка. Відповідай стисло, природно, "
    "українською. Тримай сталий голос незалежно від гілки."
)

# Marker words that hint at a need for reasoning or actions.
THINK_HINTS = ("чому", "поясни", "проаналізуй", "порівняй", "розбери", "обґрунтуй")
TOOL_HINTS = ("файл", "запусти", "збережи", "прочитай", "пошукай", "знайди", "пошук")
