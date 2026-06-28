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

# Need thresholds + the branch each maps to.
#   action: "chat" -> Haiku; "deep" -> Opus; "idle" -> rest gate; "tool" -> named sub-agent.
# Only REACH_OUT_NEED (connection) actually SELF-TRIGGERS a proactive message (loneliness ->
# she writes first). WHICH brain answers it is shaped by her OTHER needs at that moment
# (engine.reach_out_branch): intensity over its threshold -> deep (opus); else novelty over its
# -> session-wiki; else connection's baseline (chat). So opus/session-wiki never self-INITIATE;
# intensity also routes USER turns to opus via turn_weight. rest -> the sleep gate, not a message.
NEED_TRIGGERS = {
    "connection": {"threshold": 0.80, "action": "chat"},
    "rest": {"threshold": 0.90, "action": "idle"},
    "novelty": {"threshold": 0.85, "action": "tool", "agent": "session-wiki"},
    "intensity": {"threshold": 0.75, "action": "deep"},
}
# The proactive self-message fires only on this need; intensity/novelty pick the brain that
# answers it (see the comment above and engine.reach_out_branch). REACH_OUT_MODELS = priority.
REACH_OUT_NEED = "connection"
REACH_OUT_MODELS = ("intensity", "novelty")  # first over its threshold shapes the reach-out
SELF_COOLDOWN = int(
    os.environ.get("SELF_COOLDOWN", "5")
)  # silent ticks after a self-trigger (per need)

# Rest gate: when fatigue (rest) reaches NEED_TRIGGERS["rest"]["threshold"] (0.90) Agnika stops
# answering (user turns AND self-triggers) and recovers on idle until rest drops to REST_WAKE,
# then she's available again. REST_WAKE sits just BELOW the sleep threshold — a thin band keeps
# her from oscillating exactly at the boundary, but she wakes as soon as she's meaningfully below
# it (not after a long nap; lower it for longer naps). She says REST_MESSAGE ONCE on entering
# rest (not to every message); slash commands keep working.
REST_WAKE = float(os.environ.get("REST_WAKE", "0.85"))
REST_MESSAGE = "мені треба відпочити"  # persona line (Ukrainian, intentional)

# Appended to the self-trigger prompt when she ALREADY reached out and got no reply, so she
# doesn't robotically repeat herself (persona-layer Ukrainian, intentional).
SELF_SILENCE_NOTE = (
    "(Ти вже озивалася першою, а відповіді ще нема. Не повторюйся: визнай тишу, "
    "зміни тон або просто побудь поруч одним коротким рядком.)"
)

# Per-tick drift for EACH need separately (added every tick). Stated in TICKS (the
# needs-panel counter) so it's independent of TICK_SECONDS. connection drives the cheap
# chat; novelty drives the expensive deep (kept slow so Opus stays rare); intensity is
# discharged by every deep turn, so it hovers below its threshold rather than leading;
# rest barely time-drifts (fatigue is activity-driven). Pure-idle cadence: chat and deep
# each fire ~every 400 ticks (interactions make the exact gap differ from naive math).
DRIFT = {
    "connection": 0.0010,  # chat driver — 0->0.80 in ~400 ticks (6 mins)
    "rest": -0.005,  # minimal time drift — fatigue mostly comes from activity (deep/chat)
    "novelty": 0.0001,  # deep driver (leads) — bar swings the full 0..0.85
    "intensity": 0.001,  # discharged by every deep turn — hovers ~0.7, rarely the lead
}

# Closing needs by events. Negative = lowering the level. The reset is LARGE relative
# to drift, so one event clearly satisfies the need (a calm, minute-scale cadence)
# instead of leaving it hovering just under threshold and re-firing every few seconds.
#
# Key idea: WHICH branch answered DETERMINES which needs were closed.
#   - chat (Haiku) gives contact: closes connection hard, barely touches the rest;
#   - reasoning/tools (Claude CLI) is the "filling meal": closes novelty + intensity
#     hard (and TIRES — rest rises, not falls);
#   - a "tool" reach-out is keyed by AGENT NAME (else falls back to deep), so each
#     sub-agent closes what it addresses — session-wiki brings a fact -> drops novelty.
SATIATION = {
    # answered via chat (Haiku) — contact
    "chat": {"connection": -0.3, "rest": +0.2, "novelty": 0, "intensity": -0.001},
    # answered via reasoning/tools (Claude/Opus) — the "filling meal" (and tiring)
    "deep": {"connection": -0.6, "rest": +0.4, "novelty": -0.0015, "intensity": -0.80},
    # the session-wiki tool reach-out (keyed by agent name): a fresh external fact, so it
    # drops NOVELTY hard — light contact, barely tires (far less than an opus deep turn)
    "session-wiki": {"connection": -0.6, "rest": +0.4, "novelty": -0.90, "intensity": -0.15},
    # silence (a tick with no reply): rest recovers; from 0.9 to 0 in 180 ticks (3 mins)
    "idle": {"connection": 0, "rest": -0.01, "novelty": 0.0005, "intensity": +0.001},
}

# --- Classification / routing -----------------------------------------------
# Turn "weight" threshold: above -> the turn counts as reasoning, goes to Claude CLI.
THINK_THRESHOLD = float(os.environ.get("THINK_THRESHOLD", "0.45"))

CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-haiku-4-5-20251001")  # simple API for chat
DEEP_MODEL = os.environ.get("DEEP_MODEL", "claude-opus-4-8")  # Claude CLI for reasoning/tools

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
