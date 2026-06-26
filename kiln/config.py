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

# Self-triggers: EACH need has its own threshold and action (branch) on crossing.
#   action: "chat" -> cheap Haiku; "deep" -> Claude (Opus); "idle" -> stay quiet;
#           "tool" -> run a named Claude Code sub-agent (key "agent") via
#                     `claude -p --agent <agent>` (.claude/agents/<agent>.md).
# connection is eased by contact -> cheap chat is the FREQUENT driver;
# intensity needs discharge -> deep; novelty reaches out through the session-wiki
# sub-agent (a fresh external fact); rest is recovered by silence -> idle (rarely crosses).
NEED_TRIGGERS = {
    "connection": {"threshold": 0.80, "action": "chat"},
    "rest": {"threshold": 0.90, "action": "idle"},
    "novelty": {"threshold": 0.85, "action": "tool", "agent": "session-wiki"},
    "intensity": {"threshold": 0.75, "action": "deep"},
}
SELF_COOLDOWN = int(
    os.environ.get("SELF_COOLDOWN", "5")
)  # silent ticks after a self-trigger (per need)

# Per-tick drift for EACH need separately (added every tick). Stated in TICKS (the
# needs-panel counter) so it's independent of TICK_SECONDS. connection drives the cheap
# chat; novelty drives the expensive deep (kept slow so Opus stays rare); intensity is
# discharged by every deep turn, so it hovers below its threshold rather than leading;
# rest barely time-drifts (fatigue is activity-driven). Pure-idle cadence: chat and deep
# each fire ~every 400 ticks (interactions make the exact gap differ from naive math).
DRIFT = {
    "connection": 0.0020,  # chat driver — 0->0.80 in ~400 ticks
    "rest": 0.0004,  # minimal time drift — fatigue mostly comes from activity (deep/chat)
    "novelty": 0.0012,  # deep driver (leads) — bar swings the full 0..0.85
    "intensity": 0.0008,  # discharged by every deep turn — hovers ~0.7, rarely the lead
}

# Closing needs by events. Negative = lowering the level. The reset is LARGE relative
# to drift, so one event clearly satisfies the need (a calm, minute-scale cadence)
# instead of leaving it hovering just under threshold and re-firing every few seconds.
#
# Key idea: WHICH branch answered DETERMINES which needs were closed.
#   - chat (Haiku) gives contact: closes connection hard, barely touches the rest;
#   - reasoning/tools (Claude CLI) is the "filling meal": closes novelty + intensity
#     hard (and TIRES — rest rises, not falls).
SATIATION = {
    # answered via chat (Haiku) — contact
    "chat": {"connection": -0.50, "rest": +0.01, "novelty": -0.05, "intensity": -0.08},
    # answered via reasoning/tools (Claude/Opus) — the "filling meal" (and tiring)
    "deep": {"connection": -0.40, "rest": +0.04, "novelty": -0.45, "intensity": -0.40},
    # silence (a tick with no reply): rest recovers; being unanswered builds mild restlessness
    "idle": {"connection": 0, "rest": -0.005, "novelty": 0, "intensity": +0.0003},
}

# --- Classification / routing -----------------------------------------------
# Turn "weight" threshold: above -> the turn counts as reasoning, goes to Claude CLI.
THINK_THRESHOLD = float(os.environ.get("THINK_THRESHOLD", "0.45"))

CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-haiku-4-5-20251001")  # simple API for chat
DEEP_MODEL = os.environ.get("DEEP_MODEL", "claude-opus-4-8")  # Claude CLI for reasoning/tools

# Tools/skills allowed on the reasoning branch (example).
DEEP_TOOLS = ["Read", "Write", "Bash"]
DEEP_SKILLS: list[str] = []  # e.g. ["search", "summarize"]

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
