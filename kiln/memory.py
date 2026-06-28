"""
kiln — canon/prompts loaders, the session summarizer, and the system-prompt builder.

`summarize()` condenses a session via the Anthropic Messages API (Haiku — cheap/fast, like the
chat branch, not `claude -p`/Opus); the engine's close path writes the summary + raw turns into
the single `.kiln/store.json` (store.py). On start the stored summaries load into the system
prompt of every branch (`build_system`).
"""

from __future__ import annotations

import datetime as _dt
import json
import random
import re
import subprocess
from pathlib import Path

from .config import (
    AGENT_BIRTH,
    CANON_FILE,
    CHAT_MODEL,
    DEEP_MODEL,
    DEFAULT_CANON,
    FACTS_DIGEST_LINES,
    FACTS_ENABLED,
    HISTORY_DIR,
    MAX_FACTS,
    MEMORY_FILE,
    MEMORY_SUMMARIES,
    PROMPTS_FILE,
    REST_MESSAGE,
    SUMMARY_SENTENCES,
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
    `MEMORY_SUMMARIES` (0 = all, N = the last N) bounds how many enter the prompt.
    """
    summaries = load_store().get("summaries", [])
    if MEMORY_SUMMARIES > 0:
        summaries = summaries[-MEMORY_SUMMARIES:]  # keep only the most recent N
    blocks = [
        f"## Conversation {s.get('stamp', '')}\n{s.get('text', '')}".strip()
        for s in summaries
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


# v0.9: Agnika's birthday seeds the daily biorhythm (kiln/mood.py).
DEFAULT_BIRTH = _dt.datetime(2001, 8, 12, 17, 10)  # the canon birthday (fallback)
_BIRTH_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[,\s]+(\d{1,2}):(\d{2}))?")


def _parse_birth(text: str) -> _dt.datetime | None:
    """First `DD.MM.YYYY[ , HH:MM]` in `text` -> a datetime, or None if none / invalid."""
    m = _BIRTH_RE.search(text or "")
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = int(m.group(4)) if m.group(4) else 0
    minute = int(m.group(5)) if m.group(5) else 0
    try:
        return _dt.datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def load_birth(canon: str) -> _dt.datetime:
    """Agnika's birth datetime for the biorhythm: `AGENT_BIRTH` (.env) if set & valid, else parsed
    from the canon's natal line (`Народження: 12.08.2001, 17:10`), else `DEFAULT_BIRTH`. Never raises
    — a fresh clone / garbled canon still starts."""
    return _parse_birth(AGENT_BIRTH) or _parse_birth(canon) or DEFAULT_BIRTH


def summarize(history: list[dict], live: bool) -> str:
    """Conversation summary via the Anthropic Messages API on CHAT_MODEL (Haiku) — cheap and
    fast (~1-2s), billed through the API key like the chat branch, NOT via `claude -p`/Opus.
    In dry-run — a stub."""
    if not history:
        return ""
    transcript = to_transcript(history)
    prompt = (
        f"Стисло підсумуй цю розмову українською (до {SUMMARY_SENTENCES} речень): про що "
        "говорили, які висновки, що варто пам'ятати наступного разу.\n\n" + transcript
    )
    if not live:
        return f"(dry-run summary: {len(history)} turns)"
    # Invariant: the API-key (SDK) path is for the CHEAP model only — Opus is never billed via
    # the API key (it runs only through `claude -p`). Skip rather than misbill on misconfig.
    if "opus" in CHAT_MODEL.lower():
        print(f"[exit] summary skipped: CHAT_MODEL '{CHAT_MODEL}' is Opus (set a cheap model)")
        return ""
    # Local import so dry-run/tests need no anthropic package (the live chat branch needs it).
    from anthropic import Anthropic

    try:
        msg = Anthropic().messages.create(
            model=CHAT_MODEL,  # Haiku 4.5 — cheap and fast
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # network / limits / API error — don't crash exit; the turns are saved
        print(f"[exit] summary failed: {e}")
        return ""
    return next((b.text for b in msg.content if b.type == "text"), "").strip()


def _parse_facts(stdout: str) -> list[str]:
    """Pull the fact list from `claude -p` JSON output: its `result` is a JSON array of strings
    (tolerating a code fence / surrounding prose); falls back to a bullet/line list."""
    try:
        result = json.loads(stdout).get("result", "")
    except json.JSONDecodeError:
        result = stdout
    result = (result or "").strip()
    if not result:
        return []
    start, end = result.find("["), result.rfind("]")
    if start != -1 and end > start:
        try:
            arr = json.loads(result[start : end + 1])
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [ln.strip().lstrip("-•*").strip() for ln in result.splitlines() if ln.strip()]


def extract_facts(history: list[dict], existing_facts: list[str], live: bool) -> list[str]:
    """Extract durable **facts about the user** from the cleaned session via `claude -p` on
    DEEP_MODEL (Opus + extended thinking; API key stripped → subscription). The model is shown
    the facts already known and asked for ONLY new ones. Returns a list of new fact strings.
    Dry-run — a stub (`[]`). On CLI error — `[]` (the exit path must never crash). Off when
    FACTS_ENABLED is false."""
    if not FACTS_ENABLED or not history:
        return []
    transcript = to_transcript(history)
    known = "\n".join(f"- {t}" for t in existing_facts if t.strip()) or "(немає)"
    prompt = (
        "Ось розмова з користувачем. Випиши СТІЙКІ факти про користувача (хто він, уподобання, "
        "життя, плани, стосунки) — це довготривала пам'ять, а не переказ розмови. Поверни ЛИШЕ "
        "нові факти, яких ще немає у списку відомих, як JSON-масив рядків українською (порожній "
        f"масив [], якщо нічого нового).\n\nВідомі факти:\n{known}\n\nРозмова:\n{transcript}"
    )
    if not live:
        return []  # dry-run stub — no model call
    # Opus via claude -p; claude_env() turns on extended thinking and strips the API key (so it
    # bills via the CLI login — Opus is never called via the API key). Distinct from the Haiku
    # session summary: facts are the durable layer and warrant Opus.
    cmd = ["claude", "-p", "--model", DEEP_MODEL, "--output-format", "json", prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=claude_env())
    except Exception as e:  # timeout / process failed to start
        print(f"[exit] fact extraction failed: {e}")
        return []
    if result.returncode != 0:
        # Don't crash the exit over facts — the transcript + summary are already saved.
        print(
            f"[exit] fact extraction failed (CLI {result.returncode}: {_cli_error_detail(result)})"
        )
        return []
    return _parse_facts(result.stdout)


def digest_facts(live: bool) -> str:
    """Condense ALL stored user facts to a compact view of who the user is — at most
    FACTS_DIGEST_LINES lines (Lumi's `facts_digests`) — via `claude -p` on DEEP_MODEL (Opus +
    extended thinking; API key stripped → subscription). Read-only (no store writes). No facts
    → "". Dry-run — a stub. On CLI error — "" (start must never crash). The line cap is enforced
    defensively after the call. Off (→ "") when FACTS_ENABLED is false."""
    if not FACTS_ENABLED:
        return ""
    facts = [
        f.get("text", "") for f in load_store().get("facts", []) if (f.get("text") or "").strip()
    ]
    if not facts:
        return ""
    if MAX_FACTS > 0:
        facts = facts[-MAX_FACTS:]  # digest only the most recent N (bounds the per-start input)
    listing = "\n".join(f"- {t}" for t in facts)
    prompt = (
        f"Ось факти про користувача. Стисни їх до щонайбільше {FACTS_DIGEST_LINES} рядків — "
        "актуальний, дедуплікований портрет користувача українською (по одному факту в рядку, "
        f"без вступів і нумерації).\n\n{listing}"
    )
    if not live:
        return f"(dry-run facts digest: {len(facts)} facts)"
    cmd = ["claude", "-p", "--model", DEEP_MODEL, "--output-format", "json", prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=claude_env())
    except Exception as e:  # timeout / process failed to start
        print(f"[start] facts digest failed: {e}")
        return ""
    if result.returncode != 0:
        print(f"[start] facts digest failed (CLI {result.returncode}: {_cli_error_detail(result)})")
        return ""
    try:
        text = (json.loads(result.stdout).get("result") or "").strip()
    except json.JSONDecodeError:
        text = result.stdout.strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]  # enforce the cap (model may overshoot)
    return "\n".join(lines[:FACTS_DIGEST_LINES])


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


def build_system(canon: str, memory: str, facts: str = "", world: str = "", mood: str = "") -> str:
    """System prompt = canon (persona) + long-term memory summaries + the user-facts digest +
    the v0.8 world block + the v0.9 mood block.

    Each layer is optional and appended only when non-empty: the v0.5 memory block, the v0.6
    `## Facts about the user` section, the v0.8 `world` block (`## Зараз` + `## Повідомлення з
    минулої сесії`), then the v0.9 `mood` block (`## Настрій` — needs + biorhythm); each carries its
    own headers. With empty `memory`/`facts`/`world`/`mood` the result is exactly `canon`; with empty
    `mood` it is byte-for-byte the v0.8 output."""
    out = canon
    if memory.strip():
        out += "\n\nДовга пам'ять про попередні розмови (для контексту):\n" + memory.strip()
    if facts.strip():
        out += "\n\n## Facts about the user\n" + facts.strip()
    if world.strip():
        out += "\n\n" + world.strip()
    if mood.strip():
        out += "\n\n" + mood.strip()
    return out


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
