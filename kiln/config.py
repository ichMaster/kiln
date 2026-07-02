"""
kiln — configuration: paths, .env, and calibration knobs.

Kept separate so all modules (engine, memory, commands) can import the
constants without circular dependencies (engine runs as __main__).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .security import SecurityProfile

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
NEEDS_FILE = STATE_DIR / "needs_model.yaml"  # the need MODEL: drift/satiation/triggers + scalars
CONFIG_FILE = STATE_DIR / "config.yaml"  # committed agent config (models, ticks, awareness…)
HISTORY_DIR = PROJECT_ROOT / "history"  # raw session transcripts (JSON, for RAG)
ENV_FILE = PROJECT_ROOT / ".env"  # secrets + personal fields (gitignored)
SERVER_CONFIG_FILE = PROJECT_ROOT / "server.yaml"  # committed server config (host/port/agent)

KILN_DIR = PROJECT_ROOT / ".kiln"  # unified store dir (Lumi-style; shared with later phases)
STORE_FILE = KILN_DIR / "store.json"  # the single persistence store (sessions/messages/summaries)
NEEDS_LEVELS_FILE = KILN_DIR / "needs.json"  # live need LEVELS (auto-written each run)
USAGE_LEDGER = KILN_DIR / "usage-ledger.jsonl"  # v0.7: one append-only line per closed session
USAGE_REPORT_FILE = KILN_DIR / "usage-report.md"  # v0.7: the generated Markdown cost report

# v1.1: per-agent persistence. The DEFAULT agent (agnika / unset) keeps today's flat global paths
# (no migration); any other agent_id nests its mutable data under state/{id}/ and .kiln/{id}/.
DEFAULT_AGENT = "agnika"


@dataclass(frozen=True)
class AgentPaths:
    """The per-agent persistence roots threaded through `engine.run()` (KILN-051). For the default
    agent these are the module-level globals, so Agnika + all v0 data/tests are byte-for-byte. (The
    mood / needs-model CONFIG is carried per-agent by AgentConfig (v1.2), threaded into run.)"""

    state_dir: Path  # committed config: needs_model.yaml, canon.md, prompts.md, mood.json
    needs_file: Path  # live need LEVELS (runtime, under .kiln/ with the rest of the generated data)
    store_file: Path
    usage_ledger: Path
    usage_report: Path
    canon_file: Path
    prompts_file: Path

    @classmethod
    def for_agent(cls, agent_id: str | None = None) -> AgentPaths:
        if not agent_id or agent_id == DEFAULT_AGENT:
            return cls(
                state_dir=STATE_DIR,
                needs_file=NEEDS_LEVELS_FILE,
                store_file=STORE_FILE,
                usage_ledger=USAGE_LEDGER,
                usage_report=USAGE_REPORT_FILE,
                canon_file=CANON_FILE,
                prompts_file=PROMPTS_FILE,
            )
        sdir = STATE_DIR / agent_id
        kdir = KILN_DIR / agent_id
        return cls(
            state_dir=sdir,
            needs_file=kdir / "needs.json",
            store_file=kdir / "store.json",
            usage_ledger=kdir / "usage-ledger.jsonl",
            usage_report=kdir / "usage-report.md",
            canon_file=sdir / "canon.md",
            prompts_file=sdir / "prompts.md",
        )


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


load_dotenv()  # read .env (secrets + personal) BEFORE the settings are defined below


# --- Non-secret config (committed YAML) -------------------------------------
# Agent tunables live in state/config.yaml, server settings in server.yaml — both committed. A real
# environment variable (UPPER_SNAKE of the key) overrides a file value, which overrides the built-in
# default below; secrets/personal fields stay in .env. DEFAULT_* is the fallback for a fresh clone /
# broken edit / no PyYAML.
DEFAULT_CONFIG = {
    "chat_model": "claude-haiku-4-5-20251001",
    "deep_model": "claude-opus-4-8",
    "thought_model": None,  # None -> use chat_model
    "facts_model": "claude-sonnet-5",  # v1.4: facts extract/digest via the SDK (API-key billed)
    "tick_seconds": 0.5,
    "think_threshold": 0.45,
    "thinking_tokens": 8000,
    "thoughts_enabled": True,
    "thought_visible_every": 5,
    "thoughts_in_prompt": 8,
    "facts_enabled": True,
    "facts_digest_lines": 8,
    "max_facts": 0,
    "memory_summaries": 0,
    "summary_sentences": 5,
    "world_awareness": True,
    "mood_awareness": True,
    "biorhythm": True,
    "recent_messages": 10,
    "usage_report": True,
    "agent_name": "Агніка",
    "agent_birth": "",
    "rotate_every_hours": 0,  # auto-rotate the session every N hours (0 = off, manual only)
}
DEFAULT_SERVER = {"host": "127.0.0.1", "port": 8000, "agent": "agnika", "agents": ["agnika"]}


def _load_yaml(path: Path, default: dict) -> dict:
    """`default` merged with the YAML file (file wins per key); the bare default on any failure."""
    try:
        import yaml
    except ImportError:
        return dict(default)
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return dict(default)
    return {**default, **data} if isinstance(data, dict) else dict(default)


def load_config(path: Path = CONFIG_FILE) -> dict:
    """Agent config from state/config.yaml (DEFAULT_CONFIG fallback)."""
    return _load_yaml(path, DEFAULT_CONFIG)


def load_server_config(path: Path = SERVER_CONFIG_FILE) -> dict:
    """Server config from server.yaml (DEFAULT_SERVER fallback)."""
    return _load_yaml(path, DEFAULT_SERVER)


_CONFIG = load_config()
_SERVER = load_server_config()


def _opt_str(cfg: dict, key: str, env_key: str, default: str) -> str:
    v = os.environ.get(env_key)
    if v is None:
        v = cfg.get(key, default)
    return default if v is None else str(v)


def _opt_int(cfg: dict, key: str, env_key: str, default: int) -> int:
    v = os.environ.get(env_key)
    return int(v if v is not None else cfg.get(key, default))


def _opt_float(cfg: dict, key: str, env_key: str, default: float) -> float:
    v = os.environ.get(env_key)
    return float(v if v is not None else cfg.get(key, default))


def _opt_bool(cfg: dict, key: str, env_key: str, default: bool) -> bool:
    v = os.environ.get(env_key)
    if v is not None:
        return v == "1"
    return bool(cfg.get(key, default))


# --- Ticks ------------------------------------------------------------------
TICK_SECONDS = _opt_float(_CONFIG, "tick_seconds", "TICK_SECONDS", 0.5)

# --- Needs (the motivational substrate) -------------------------------------
# The need MODEL — per-need drift, satiation (closing) events, trigger thresholds + the trigger
# wiring + cooldowns — lives in state/needs_model.yaml; edit THAT to tune Agnika. It's loaded here;
# DEFAULT_NEEDS is the fallback for a fresh clone / a broken edit / no PyYAML. (The runtime need
# LEVELS are separate — .kiln/needs.json, save_state/load_state.) `need_triggers` action: chat ->
# Haiku, deep -> Opus, idle -> rest gate, tool -> sub-agent, thought -> v0.10 monologue, ask ->
# v0.11 curiosity. Only reach_out_need (connection) self-triggers a message; reflection/curiosity
# have an entry (threshold + panel display) but don't self-trigger.
DEFAULT_NEEDS = {
    "need_triggers": {
        "connection": {"threshold": 0.80, "action": "chat"},
        "rest": {"threshold": 0.90, "action": "idle"},
        "novelty": {"threshold": 0.85, "action": "tool", "agent": "session-wiki"},
        "intensity": {"threshold": 0.75, "action": "tool", "agent": "deep"},
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
    """The needs config from state/needs_model.yaml; DEFAULT_NEEDS if the file is missing/invalid or
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
THINK_THRESHOLD = _opt_float(_CONFIG, "think_threshold", "THINK_THRESHOLD", 0.45)

CHAT_MODEL = _opt_str(_CONFIG, "chat_model", "CHAT_MODEL", "claude-haiku-4-5-20251001")
DEEP_MODEL = _opt_str(_CONFIG, "deep_model", "DEEP_MODEL", "claude-opus-4-8")
# v1.4: the facts layer (extract/digest) runs on the SDK on this model (API-key billed like chat +
# the session summary), NOT on `claude -p`. Must not be Opus (the API key never bills Opus).
FACTS_MODEL = _opt_str(_CONFIG, "facts_model", "FACTS_MODEL", "claude-sonnet-5")

# v0.10 inner monologue: the thought runs on the chat brain (Haiku). THOUGHTS_ENABLED is the master
# switch; THOUGHT_MODEL is informational (= CHAT_MODEL — thoughts go through brain.chat).
# THOUGHT_VISIBLE_EVERY (M): a fresh thought surfaces in the chat with probability ~1/M (else stays
# internal). 1 -> always shown; large -> rarely.
THOUGHTS_ENABLED = _opt_bool(_CONFIG, "thoughts_enabled", "THOUGHTS_ENABLED", True)
THOUGHT_MODEL = _opt_str(_CONFIG, "thought_model", "THOUGHT_MODEL", "") or CHAT_MODEL
THOUGHT_VISIBLE_EVERY = _opt_int(_CONFIG, "thought_visible_every", "THOUGHT_VISIBLE_EVERY", 5)
# How many recent (cross-session) thoughts go into the `## Думки` prompt section. 0 = off.
THOUGHTS_IN_PROMPT = _opt_int(_CONFIG, "thoughts_in_prompt", "THOUGHTS_IN_PROMPT", 8)

# Every `claude -p` sub-agent runs with extended thinking ON — this is the budget the security
# builder passes as MAX_THINKING_TOKENS (kiln/security.py). Tune via .env; lower it for snappier
# interactive replies (thinking adds latency — it's a cap, the model uses up to this much).
THINKING_TOKENS = _opt_int(_CONFIG, "thinking_tokens", "THINKING_TOKENS", 8000)

# v0.6 long memory: at start, all stored user `facts` are condensed (Opus) to at most this many
# lines for the `## Facts about the user` system-prompt section. Tune via .env.
FACTS_DIGEST_LINES = _opt_int(_CONFIG, "facts_digest_lines", "FACTS_DIGEST_LINES", 8)

# How many of the most recent session summaries load into the system prompt (load_memory):
# 0 = all (no cap); N = only the last N. Bounds prompt growth as the store accumulates sessions.
MEMORY_SUMMARIES = _opt_int(_CONFIG, "memory_summaries", "MEMORY_SUMMARIES", 0)

# Target length of each session summary, in sentences (the summarize prompt).
SUMMARY_SENTENCES = _opt_int(_CONFIG, "summary_sentences", "SUMMARY_SENTENCES", 5)

# How many of the most recent stored facts feed the start-time digest (digest_facts): 0 = all,
# N = only the last N. Bounds the per-start Opus input as facts accumulate (the digest OUTPUT is
# always capped at FACTS_DIGEST_LINES regardless). Stored facts themselves are never trimmed.
MAX_FACTS = _opt_int(_CONFIG, "max_facts", "MAX_FACTS", 0)

# Master switch for the user-facts layer (v0.6): 0 -> no extraction on close and no facts section
# in the prompt (stored facts are kept, just dormant). 1 -> on.
FACTS_ENABLED = _opt_bool(_CONFIG, "facts_enabled", "FACTS_ENABLED", True)

# v0.7 usage reporting: 0 -> a session close writes no ledger line and no report (Lumi's
# `usage_report` flag). 1 -> on.
USAGE_REPORT = _opt_bool(_CONFIG, "usage_report", "USAGE_REPORT", True)

# v0.8 world awareness: the user's location for the `## Зараз` section (empty = omitted).
USER_LOCATION = os.environ.get("USER_LOCATION", "")  # personal -> stays in .env
# Optional IANA timezone for the world clock (e.g. "Europe/Kyiv"); empty = the machine's local time.
TIMEZONE = os.environ.get("TIMEZONE", "")  # personal -> stays in .env
# How many recent turns go into the `## Повідомлення з минулої сесії` block (timestamped); 0 = off.
RECENT_MESSAGES = _opt_int(_CONFIG, "recent_messages", "RECENT_MESSAGES", 10)
# Master switch for the world block (## Зараз + ## Повідомлення з минулої сесії); 0 = off.
WORLD_AWARENESS = _opt_bool(_CONFIG, "world_awareness", "WORLD_AWARENESS", True)

# Speaker names used to label turns in transcripts / the timeline / `/prompt` (persona layer).
USER_NAME = os.environ.get("USER_NAME", "Користувач")  # personal -> stays in .env
AGENT_NAME = _opt_str(_CONFIG, "agent_name", "AGENT_NAME", "Агніка")

# v0.9: Agnika's birthday seeds the daily biorhythm. Empty -> parsed from the canon's natal line
# (else the DEFAULT_BIRTH fallback in memory.py); set to override (`DD.MM.YYYY[ HH:MM]`).
AGENT_BIRTH = _opt_str(_CONFIG, "agent_birth", "AGENT_BIRTH", "")
# v0.9 mood: the per-turn `## Настрій` block (needs + biorhythm). MOOD_AWARENESS = master switch;
# BIORHYTHM toggles just the biorhythm sub-block within it.
MOOD_AWARENESS = _opt_bool(_CONFIG, "mood_awareness", "MOOD_AWARENESS", True)
BIORHYTHM = _opt_bool(_CONFIG, "biorhythm", "BIORHYTHM", True)

# v1.1.x: auto-rotate the session every N hours of real time (close+summarize, start fresh;
# non-blocking). 0 = off (rotate only on /rotate or POST …/rotate). Only fires on a long-running
# server — a short dry-run never reaches the interval.
ROTATE_EVERY_HOURS = _opt_float(_CONFIG, "rotate_every_hours", "ROTATE_EVERY_HOURS", 0.0)

# v0.11 curiosity is a NORMAL need: it drifts (DRIFT["curiosity"]), shows in `## Настрій` with a
# behavioural cue per band (state/mood.json), and is discharged by the SATIATION["asked"] event —
# fired by the question monitor (engine.is_curiosity_reply) when a reply actually asks. It does NOT
# self-trigger (not in NEED_TRIGGERS); the band cues, not a threshold, shape when she asks.

# --- Tick-server (server.yaml; env overrides KILN_HOST / KILN_PORT / KILN_AGENT) --------------
SERVER_HOST = _opt_str(_SERVER, "host", "KILN_HOST", "127.0.0.1")
SERVER_PORT = _opt_int(_SERVER, "port", "KILN_PORT", 8000)
SERVER_AGENT = _opt_str(
    _SERVER, "agent", "KILN_AGENT", "agnika"
)  # connect.sh's default attach target


def _server_agents() -> list[str]:
    """The agent_ids the server boots (v1.2): `agents:` in server.yaml, or `KILN_AGENTS` (comma-
    separated) override, falling back to the single `agent`/SERVER_AGENT (v1.1 one agent)."""
    env = os.environ.get("KILN_AGENTS")
    if env:
        return [a.strip() for a in env.split(",") if a.strip()]
    listed = _SERVER.get("agents") or [_SERVER.get("agent", SERVER_AGENT)]
    return [str(a) for a in listed] or [SERVER_AGENT]


SERVER_AGENTS = _server_agents()  # the set of agents started at server boot (host.boot_configured)

# v1.2: per-agent permission SCOPE — the home agent (agnika) is broad (system/home), companions are
# narrow. Set per agent + surfaced in GET /agents; **not enforced** until the tool registry (1.7).
AGENT_BROAD_SCOPE = "broad"
AGENT_NARROW_SCOPE = "narrow"


def agent_scope(agent_id: str | None) -> str:
    """The agent's permission scope (set in v1.2, ENFORCED with tools in 1.7): the home agent is
    broad; any companion is narrow."""
    return AGENT_BROAD_SCOPE if (not agent_id or agent_id == DEFAULT_AGENT) else AGENT_NARROW_SCOPE


# (v1.4) The old DEEP_TOOLS ["Read","Write","Bash"] on the raw deep branch is RETIRED — tool work
# now runs only through the `hands` sub-agent, gated by the per-agent security profile
# (state/{id}/security.yaml → kiln.security). The deep branch is tool-less.


# (v1.4) `claude_env` is RETIRED — the "everything minus the API key" env is gone. Every `claude -p`
# spawn now goes through `security.claude_cmd`, which builds a minimal env allowlist per call; the
# facts layer left `claude -p` for the SDK (`FACTS_MODEL`). See kiln/security.py.

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


# --- Per-agent calibration (v1.2, KILN-056) ---------------------------------
# v1.1 loads the calibration as the module-level constants above (one global set). v1.2 makes it
# PER-AGENT: `AgentConfig.for_agent(id)` bundles a single agent's full calibration — the need MODEL
# (drift/satiation/triggers + scalars from needs_model.yaml), the tunables (models/ticks/awareness…
# from config.yaml), and the mood bands/cues (mood.json) — each from that agent's `state/{id}/`.
# The DEFAULT agent (agnika / unset) reads the flat `state/` files, so it reproduces the globals
# above byte-for-byte. `config.yaml` is scoped PER-AGENT (`state/{id}/config.yaml`) — the settled
# decision — so a companion can run a cheaper model or a slower tick; a real env var (UPPER_SNAKE)
# still overrides any agent's value, the same operator escape hatch as the globals. `engine.run`
# takes a `config` (KILN-057): None = the agnika default (reads these module globals); a per-agent
# AgentConfig makes that agent drift/route/sound on its own model.
@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    # need MODEL (state/{id}/needs_model.yaml)
    need_triggers: dict
    drift: dict
    satiation: dict
    reach_out_need: str
    reach_out_models: tuple
    reflect_need: str
    self_cooldown: int
    thought_cooldown: int
    rest_wake: float
    # tunables (state/{id}/config.yaml; env var > file > default)
    chat_model: str
    deep_model: str
    thought_model: str
    facts_model: str
    tick_seconds: float
    think_threshold: float
    thinking_tokens: int
    thoughts_enabled: bool
    thought_visible_every: int
    thoughts_in_prompt: int
    facts_enabled: bool
    facts_digest_lines: int
    max_facts: int
    memory_summaries: int
    summary_sentences: int
    world_awareness: bool
    mood_awareness: bool
    biorhythm: bool
    recent_messages: int
    usage_report: bool
    agent_name: str
    agent_birth: str
    rotate_every_hours: float
    # mood bands/cues (state/{id}/mood.json), raw config
    mood: dict
    # v1.4 deep-branch security profile (state/{id}/security.yaml; fail-closed)
    security: SecurityProfile

    @classmethod
    def for_agent(cls, agent_id: str | None = None) -> AgentConfig:
        """Resolve one agent's full calibration from its `state/{id}/` files (the default agent uses
        the flat `state/`, reproducing the module globals). A missing/broken file falls back to the
        built-in `DEFAULT_*`; an env var (UPPER_SNAKE) still overrides any tunable per key."""
        import copy

        from .mood import load_mood  # lazy: mood imports config (avoid the cycle)
        from .security import load_security  # lazy: keep the import graph a clean DAG

        sdir = AgentPaths.for_agent(agent_id).state_dir
        cfg = load_config(sdir / "config.yaml")
        try:
            nm = _build_needs(load_needs(sdir / "needs_model.yaml"))
        except (KeyError, TypeError, ValueError):
            nm = _build_needs(DEFAULT_NEEDS)  # structurally malformed -> defaults
        mood = load_mood(sdir / "mood.json")  # load_mood already heals a missing/broken file
        security = load_security(sdir / "security.yaml")  # fail-closed → DEFAULT_SECURITY
        chat = _opt_str(cfg, "chat_model", "CHAT_MODEL", "claude-haiku-4-5-20251001")
        return cls(
            agent_id=agent_id or DEFAULT_AGENT,
            need_triggers=copy.deepcopy(nm["NEED_TRIGGERS"]),
            drift=copy.deepcopy(nm["DRIFT"]),
            satiation=copy.deepcopy(nm["SATIATION"]),
            reach_out_need=nm["REACH_OUT_NEED"],
            reach_out_models=nm["REACH_OUT_MODELS"],
            reflect_need=nm["REFLECT_NEED"],
            self_cooldown=nm["SELF_COOLDOWN"],
            thought_cooldown=nm["THOUGHT_COOLDOWN"],
            rest_wake=nm["REST_WAKE"],
            chat_model=chat,
            deep_model=_opt_str(cfg, "deep_model", "DEEP_MODEL", "claude-opus-4-8"),
            thought_model=_opt_str(cfg, "thought_model", "THOUGHT_MODEL", "") or chat,
            facts_model=_opt_str(cfg, "facts_model", "FACTS_MODEL", "claude-sonnet-5"),
            tick_seconds=_opt_float(cfg, "tick_seconds", "TICK_SECONDS", 0.5),
            think_threshold=_opt_float(cfg, "think_threshold", "THINK_THRESHOLD", 0.45),
            thinking_tokens=_opt_int(cfg, "thinking_tokens", "THINKING_TOKENS", 8000),
            thoughts_enabled=_opt_bool(cfg, "thoughts_enabled", "THOUGHTS_ENABLED", True),
            thought_visible_every=_opt_int(
                cfg, "thought_visible_every", "THOUGHT_VISIBLE_EVERY", 5
            ),
            thoughts_in_prompt=_opt_int(cfg, "thoughts_in_prompt", "THOUGHTS_IN_PROMPT", 8),
            facts_enabled=_opt_bool(cfg, "facts_enabled", "FACTS_ENABLED", True),
            facts_digest_lines=_opt_int(cfg, "facts_digest_lines", "FACTS_DIGEST_LINES", 8),
            max_facts=_opt_int(cfg, "max_facts", "MAX_FACTS", 0),
            memory_summaries=_opt_int(cfg, "memory_summaries", "MEMORY_SUMMARIES", 0),
            summary_sentences=_opt_int(cfg, "summary_sentences", "SUMMARY_SENTENCES", 5),
            world_awareness=_opt_bool(cfg, "world_awareness", "WORLD_AWARENESS", True),
            mood_awareness=_opt_bool(cfg, "mood_awareness", "MOOD_AWARENESS", True),
            biorhythm=_opt_bool(cfg, "biorhythm", "BIORHYTHM", True),
            recent_messages=_opt_int(cfg, "recent_messages", "RECENT_MESSAGES", 10),
            usage_report=_opt_bool(cfg, "usage_report", "USAGE_REPORT", True),
            agent_name=_opt_str(cfg, "agent_name", "AGENT_NAME", "Агніка"),
            agent_birth=_opt_str(cfg, "agent_birth", "AGENT_BIRTH", ""),
            rotate_every_hours=_opt_float(cfg, "rotate_every_hours", "ROTATE_EVERY_HOURS", 0.0),
            mood=copy.deepcopy(mood),
            security=security,
        )
