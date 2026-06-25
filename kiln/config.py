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
#   action: "chat" -> cheap Haiku; "deep" -> Claude (Opus).
# connection/rest are closed by contact/pause -> cheap chat is enough;
# novelty/intensity need substance/discharge -> deep.
NEED_TRIGGERS = {
    "connection": {"threshold": 0.80, "action": "chat"},
    "rest": {"threshold": 0.90, "action": "idle"},
    "novelty": {"threshold": 0.85, "action": "deep"},
    "intensity": {"threshold": 0.75, "action": "deep"},
}
SELF_COOLDOWN = int(
    os.environ.get("SELF_COOLDOWN", "5")
)  # silent ticks after a self-trigger (per need)

# Per-tick drift for EACH need separately (how much is added every tick).
# intensity accumulates fastest, rest the slowest.
DRIFT = {
    "connection": 0.020,
    "rest": 0.010,
    "novelty": 0.015,
    "intensity": 0.030,
}

# Closing needs by events. Negative values = lowering the level.
#
# Key idea: WHICH branch answered DETERMINES which needs were closed.
#   - chat (Haiku) gives contact, but barely satiates novelty/discharge;
#   - reasoning/tools (Claude CLI) is the "filling meal": closes novelty, rest, intensity.
SATIATION = {
    # event "answered via chat"
    "chat": {"connection": -0.50, "rest": +0.05, "novelty": -0.10, "intensity": -0.10},
    # event "answered via reasoning or tools" (Claude call)
    "deep": {"connection": -0.50, "rest": +0.15, "novelty": -0.40, "intensity": -0.35},
    # event "silence" (tick with no answer): rest + cooling of tension
    "idle": {"connection": -0, "rest": -0.05, "novelty": -0, "intensity": +0.05},
}

# --- Classification / routing -----------------------------------------------
# Turn "weight" threshold: above -> the turn counts as reasoning, goes to Claude CLI.
THINK_THRESHOLD = float(os.environ.get("THINK_THRESHOLD", "0.45"))

CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-haiku-4-5-20251001")  # simple API for chat
DEEP_MODEL = os.environ.get("DEEP_MODEL", "claude-opus-4-8")  # Claude CLI for reasoning/tools

# Tools/skills allowed on the reasoning branch (example).
DEEP_TOOLS = ["Read", "Write", "Bash"]
DEEP_SKILLS: list[str] = []  # e.g. ["search", "summarize"]

# Canon (persona/voice) is taken from state/canon.md; this is just a fallback.
DEFAULT_CANON = (
    "Ти — співрозмовник цього чат-двіжка. Відповідай стисло, природно, "
    "українською. Тримай сталий голос незалежно від гілки."
)

# Marker words that hint at a need for reasoning or actions.
THINK_HINTS = ("чому", "поясни", "проаналізуй", "порівняй", "розбери", "обґрунтуй")
TOOL_HINTS = ("файл", "запусти", "збережи", "прочитай", "пошукай", "знайди", "пошук")
