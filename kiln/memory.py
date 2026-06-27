"""
kiln — cross-session long-term memory, prompts/canon, and session transcripts.

On exit the conversation is summarized via Claude and appended to memory.md, while
the raw session history is written to history/*.json (for RAG). On start the summaries
are loaded into the system prompt of both branches.
"""

from __future__ import annotations

import datetime as _dt
import json
import random
import re
import subprocess

from .config import (
    CANON_FILE,
    DEEP_MODEL,
    DEFAULT_CANON,
    HISTORY_DIR,
    MEMORY_FILE,
    PROMPTS_FILE,
    claude_env,
)
from .history import to_transcript
from .usage import _cli_error_detail


def load_prompts() -> dict[str, list[str]]:
    """
    Reads state/prompts.md: [need] sections with a list of self-trigger prompts.
    Format:
        [connection]
        Prompt text 1
        Prompt text 2
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
    """Random prompt for a need; falls back when the need has no list."""
    options = prompts.get(need)
    if options:
        return random.choice(options)
    return f"(внутрішній імпульс: '{need}') Озвися першим, коротко."


def load_memory() -> str:
    """All previous conversation summaries as one text (or '')."""
    return MEMORY_FILE.read_text(encoding="utf-8") if MEMORY_FILE.exists() else ""


def load_canon() -> str:
    """Canon (persona/voice of both branches) from state/canon.md; falls back to DEFAULT_CANON."""
    if CANON_FILE.exists():
        text = CANON_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    return DEFAULT_CANON


def summarize(history: list[dict], live: bool) -> str:
    """Conversation summary via `claude -p` on DEEP_MODEL (Opus) with extended thinking ON.
    In dry-run — a stub. Opus runs through the CLI with the API key stripped (subscription)."""
    if not history:
        return ""
    transcript = to_transcript(history)
    prompt = (
        "Стисло підсумуй цю розмову українською (3-5 речень): про що говорили, "
        "які висновки, що варто пам'ятати наступного разу.\n\n" + transcript
    )
    if not live:
        return f"(dry-run summary: {len(history)} turns)"
    # Opus via claude -p; claude_env() turns on extended thinking and strips the API key (so it
    # bills via the CLI login — Opus is never called via the API key).
    cmd = ["claude", "-p", "--model", DEEP_MODEL, "--output-format", "json", prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=claude_env())
    except Exception as e:  # timeout / process failed to start
        print(f"[exit] summary failed: {e}")
        return ""
    if result.returncode != 0:
        # Don't crash exit over a failed summary — the transcript is already saved.
        detail = _cli_error_detail(result)
        print(f"[exit] summary failed (CLI {result.returncode}: {detail})")
        return ""
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()
    # (summary usage is not shown in the feed — only the result)
    return (data.get("result") or "").strip()


def save_summary(text: str) -> None:
    """Appends a summary to memory.md with a date separator."""
    if not text:
        return
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n## Conversation {stamp}\n{text}\n"
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(block)


def save_session(history: list[dict], live: bool, started: str):
    """
    Saves the RAW session history to history/session-<stamp>.json (for future RAG).
    One file = one session; ensure_ascii=False so Ukrainian stays readable.
    Returns the file path or None (empty history).
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
        "history": history,  # [{role, text}, ...] in chronological order
    }
    # Don't overwrite an existing file if two sessions closed in the same second.
    path = HISTORY_DIR / f"session-{stamp}.json"
    n = 2
    while path.exists():
        path = HISTORY_DIR / f"session-{stamp}-{n}.json"
        n += 1
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_system(canon: str, memory: str) -> str:
    """System prompt = canon (persona) + long-term memory (if any)."""
    if not memory.strip():
        return canon
    return canon + "\n\nДовга пам'ять про попередні розмови (для контексту):\n" + memory.strip()
