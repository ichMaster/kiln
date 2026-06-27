"""
kiln — the single persistence store (`.kiln/store.json`).

One JSON file holds the whole cross-session record, mirroring Lumi's `.lumi/store.json`:
  - `sessions`:  ``[{id, started_at, ended_at, mode, turns}]`` — one per closed session;
  - `messages`:  ``{session_id: [{role, text}, …]}`` — the raw turns (the RAG corpus);
  - `summaries`: ``[{session_id, stamp, text}]`` — one summary per session.

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
    """A fresh, empty store with all three sections."""
    return {"sessions": [], "messages": {}, "summaries": []}


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
