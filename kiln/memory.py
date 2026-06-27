"""
kiln — canon/prompts loaders, the session summarizer, and the system-prompt builder.

`summarize()` condenses a session via `claude -p` (Opus + thinking); the engine's close path
writes the summary + raw turns into the single `.kiln/store.json` (store.py). On start the
stored summaries load into the system prompt of every branch (`build_system`).
"""

from __future__ import annotations

import json
import random
import re
import subprocess
from pathlib import Path

from .config import (
    CANON_FILE,
    DEEP_MODEL,
    DEFAULT_CANON,
    HISTORY_DIR,
    MEMORY_FILE,
    PROMPTS_FILE,
    REST_MESSAGE,
    claude_env,
)
from .history import to_transcript
from .store import load_store, save_store
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
    """
    All stored session summaries as one text blob (oldest→newest) for the system prompt.

    Source is `.kiln/store.json` (v0.5); each summary becomes a `## Conversation <stamp>` block
    — the same layout the old `memory.md` used, so `build_system` is unchanged. Empty store → "".
    """
    blocks = [
        f"## Conversation {s.get('stamp', '')}\n{s.get('text', '')}".strip()
        for s in load_store().get("summaries", [])
        if (s.get("text") or "").strip()
    ]
    return "\n\n".join(blocks)


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


def prune_history(turns: list[dict]) -> list[dict]:
    """
    Drop what isn't real conversation before a session is stored (KILN-019): empty/whitespace
    turns, slash-command echoes (text starting with '/'), and the resting notice (REST_MESSAGE).
    Order is preserved. An all-noise session prunes to `[]` and is then skipped by the caller.
    """
    kept = []
    for t in turns:
        text = (t.get("text") or "").strip()
        if not text or text.startswith("/") or text == REST_MESSAGE:
            continue
        kept.append(t)
    return kept


def build_system(canon: str, memory: str) -> str:
    """System prompt = canon (persona) + long-term memory (if any)."""
    if not memory.strip():
        return canon
    return canon + "\n\nДовга пам'ять про попередні розмови (для контексту):\n" + memory.strip()


def _parse_memory_md(text: str):
    """Yield (stamp, summary_text) from legacy memory.md `## Conversation <stamp>` blocks."""
    for block in re.split(r"^## Conversation ", text, flags=re.MULTILINE):
        block = block.strip()
        if not block:
            continue
        stamp, _, body = block.partition("\n")
        body = body.strip()
        if body:
            yield stamp.strip(), body


def migrate_legacy(memory_file: Path = MEMORY_FILE, history_dir: Path = HISTORY_DIR) -> int:
    """
    One-shot, idempotent import of the legacy `state/memory.md` summaries and
    `history/session-*.json` transcripts into `.kiln/store.json` (KILN-021). Guarded by the
    store's own non-emptiness — a store that already has data is left untouched, so re-runs
    are no-ops. Returns the count imported (0 if nothing to do / already migrated). The legacy
    files are left in place (read-only); the engine no longer writes them.
    """
    store = load_store()
    if store["sessions"] or store["summaries"] or store["messages"]:
        return 0  # already migrated / in use
    imported = 0
    if memory_file.exists():
        for stamp, body in _parse_memory_md(memory_file.read_text(encoding="utf-8")):
            store["summaries"].append({"session_id": stamp, "stamp": stamp, "text": body})
            imported += 1
    for path in sorted(history_dir.glob("session-*.json")) if history_dir.exists() else []:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Use the unique FILENAME (history/session-<stamp>[-N].json) as the id — the inner
        # "session" stamp collides for same-second closes (the -2/-3 files), which would
        # overwrite messages and lose turns. The filename is unique per file.
        sid = path.stem.removeprefix("session-") or path.stem
        store["sessions"].append(
            {
                "id": sid,
                "started_at": rec.get("started_at", ""),
                "ended_at": rec.get("ended_at", ""),
                "mode": rec.get("mode", ""),
                "turns": rec.get("turns", len(rec.get("history", []))),
            }
        )
        store["messages"][sid] = rec.get("history", [])
        imported += 1
    if imported:
        save_store(store)
    return imported
