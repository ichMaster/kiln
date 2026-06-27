"""
kiln — the single persistence store (`.kiln/store.json`).

One JSON file holds the whole cross-session record, mirroring Lumi's `.lumi/store.json`:
  - `sessions`:  ``[{id, started_at, ended_at, mode, turns}]`` — one per closed session;
  - `messages`:  ``{session_id: [{role, text}, …]}`` — the raw turns (the RAG corpus);
  - `summaries`: ``[{session_id, stamp, text}]`` — one summary per session;
  - `facts`:     ``[{id, text, first_seen, last_seen, source_session}]`` — durable user facts (v0.6).

Writes are **atomic** (temp file + ``os.replace``) and keep a ``.bak`` of the previous good
file, so a crash mid-write never corrupts the store. A corrupt `store.json` is recovered from
``.bak`` (or started fresh) rather than crashing the loop. Stdlib-only; ``ensure_ascii=False``
so Ukrainian stays readable.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .config import STORE_FILE


def empty_store() -> dict:
    """A fresh, empty store with all four sections."""
    return {"sessions": [], "messages": {}, "summaries": [], "facts": []}


def _norm(text: str) -> str:
    """Normalized key for fact dedupe: lower-cased, whitespace-collapsed."""
    return " ".join((text or "").lower().split())


def add_facts(store: dict, texts, session_id: str, stamp: str) -> int:
    """
    Append durable user facts to the store's `facts`, deduped by normalized text. A text that
    matches an existing fact bumps its `last_seen` (its origin `source_session` is kept); a new
    text is appended as `{id, text, first_seen, last_seen, source_session}` with a fresh id and
    `first_seen == last_seen == stamp`. Returns the count of NEW facts added. Order preserved.
    """
    facts = store.setdefault("facts", [])
    index = {_norm(f.get("text", "")): f for f in facts}
    added = 0
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        key = _norm(text)
        existing = index.get(key)
        if existing is not None:
            existing["last_seen"] = stamp  # seen again; origin (source_session) unchanged
            continue
        fact = {
            "id": f"f{len(facts) + 1}",
            "text": text,
            "first_seen": stamp,
            "last_seen": stamp,
            "source_session": session_id,
        }
        facts.append(fact)
        index[key] = fact
        added += 1
    return added


def _read(path: Path) -> dict | None:
    """Parse a store file; None if missing or not a valid JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _bak(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def load_store(path: Path = STORE_FILE) -> dict:
    """
    Load the store, healing missing/mistyped sections. Recovery order: the live file, then
    its ``.bak``, then a fresh empty store — so a corrupt `store.json` falls back to the last
    good backup instead of crashing.
    """
    data = _read(path)
    if data is None:
        data = _read(_bak(path))
    if data is None:
        return empty_store()
    store = empty_store()
    if isinstance(data.get("sessions"), list):
        store["sessions"] = data["sessions"]
    if isinstance(data.get("messages"), dict):
        store["messages"] = data["messages"]
    if isinstance(data.get("summaries"), list):
        store["summaries"] = data["summaries"]
    if isinstance(data.get("facts"), list):  # v0.5 stores have no `facts` — healed to []
        store["facts"] = data["facts"]
    return store


def save_store(store: dict, path: Path = STORE_FILE) -> None:
    """
    Atomically write the store: write a temp file, back up the current good file to ``.bak``,
    then ``os.replace`` the temp into place (atomic on POSIX). The store dir is created if
    needed; `path` is never left in a half-written state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        try:  # keep the previous good file as .bak (best-effort)
            shutil.copy2(path, _bak(path))
        except OSError:
            pass
    os.replace(tmp, path)
