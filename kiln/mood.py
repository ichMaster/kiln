"""
kiln — mood & biorhythm (v0.9): pure formatters for Agnika's felt inner state.

The need / biorhythm **bands** (thresholds + Ukrainian names) and the behavioural **cues** live in
`state/mood.json`, loaded once at import — so the persona tuning is editable without touching code
(DEFAULT_MOOD is a minimal fallback for a fresh clone / a broken edit). The biorhythm is the three
classic sine cycles (physical 23 / emotional 28 / intellectual 33 days) from her birthday to "now",
each −1..+1. Pure and deterministic — `now` and `birth` are injected, so unit-testable.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path

from .config import MOOD_FILE

# Minimal fallback (labels + bands; empty cues) so the feature still works if state/mood.json is
# missing or broken. The full, editable config — including all behavioural cues — lives in the file.
DEFAULT_MOOD = {
    "need_bands": [
        {"name": "низька", "below": 0.35},
        {"name": "помірна", "below": 0.65},
        {"name": "висока", "below": 0.85},
        {"name": "дуже висока", "below": 1.01},
    ],
    "needs": {
        "connection": {"label": "самотність", "cues": {}},
        "novelty": {"label": "нудьга", "cues": {}},
        "rest": {"label": "втома", "cues": {}},
        "intensity": {"label": "напруга", "cues": {}},
        "reflection": {"label": "незібраність", "cues": {}},
        # v0.11 curiosity: a normal mood need (cues live in state/mood.json; empty here = fallback).
        "curiosity": {"label": "цікавість", "cues": {}},
    },
    "biorhythm": {
        "periods": {"physical": 23, "emotional": 28, "intellectual": 33},
        "bands": {
            "critical_abs": 0.15,
            "high": 0.5,
            "low": -0.5,
            "names": {
                "critical": "критичний день",
                "high": "підйом",
                "low": "спад",
                "neutral": "нейтрально",
            },
        },
        "cycles": {
            "physical": {"label": "фізичний", "cues": {}},
            "emotional": {"label": "емоційний", "cues": {}},
            "intellectual": {"label": "інтелектуальний", "cues": {}},
        },
    },
}


def load_mood(path: Path = MOOD_FILE) -> dict:
    """The mood config (bands / labels / cues) from `state/mood.json`; DEFAULT_MOOD if the file is
    missing or invalid (a fresh clone / broken edit still starts)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_MOOD
    return data if isinstance(data, dict) else DEFAULT_MOOD


def _build(cfg: dict):
    """Flatten a mood config into the runtime structures; raises on a structurally bad config."""
    needs = cfg["needs"]
    cycles = cfg["biorhythm"]["cycles"]
    return (
        {k: v["label"] for k, v in needs.items()},  # NEED_LABELS
        {k: v["cues"] for k, v in needs.items()},  # NEED_CUES
        cfg["need_bands"],  # NEED_BANDS (ascending {name, below})
        cfg["biorhythm"]["periods"],  # _PERIODS
        cfg["biorhythm"]["bands"],  # _BIO_BANDS
        {k: v["label"] for k, v in cycles.items()},  # _BIO_LABELS
        {k: v["cues"] for k, v in cycles.items()},  # BIO_CUES
    )


def _resolve():
    """Load + flatten the mood config; a structurally bad config falls back to DEFAULT_MOOD. Returns
    `(cfg, built)` so module state derives from the SAME source that was actually used."""
    cfg = load_mood()
    try:
        return cfg, _build(cfg)
    except (KeyError, TypeError):
        return DEFAULT_MOOD, _build(DEFAULT_MOOD)


_cfg, _built = _resolve()
NEED_LABELS, NEED_CUES, NEED_BANDS, _PERIODS, _BIO_BANDS, _BIO_LABELS, BIO_CUES = _built


def _resolve_built(mood_cfg: dict | None):
    """The runtime structures for a mood config: v1.2 per-agent `mood_cfg` built on the fly, or
    `None` → the module globals (the agnika default — byte-for-byte). A bad per-agent config heals
    to DEFAULT_MOOD (same as import-time)."""
    if mood_cfg is None:
        return NEED_LABELS, NEED_CUES, NEED_BANDS, _PERIODS, _BIO_BANDS, _BIO_LABELS, BIO_CUES
    try:
        return _build(mood_cfg)
    except (KeyError, TypeError):
        return _build(DEFAULT_MOOD)


def biorhythm(
    now: _dt.datetime, birth: _dt.datetime, mood_cfg: dict | None = None
) -> dict[str, float]:
    """The three biorhythm cycles for `now` relative to `birth` — each `sin(2π·days/period)` in
    −1..+1. Counts whole days between the two **dates** (time-of-day ignored), so it's tz-safe and
    constant across a day. `mood_cfg` (v1.2): per-agent periods; None → the module globals."""
    periods = _resolve_built(mood_cfg)[3]
    n = (now.date() - birth.date()).days
    return {name: math.sin(2 * math.pi * n / period) for name, period in periods.items()}


def bio_band(v: float, bio_bands: dict | None = None) -> str:
    """A Ukrainian band for a biorhythm value (−1..+1): критичний день near a zero-crossing,
    підйом high-positive, спад low-negative, else нейтрально (thresholds from `state/mood.json`).
    `bio_bands` (v1.2): per-agent biorhythm bands; None → the module globals."""
    bb = bio_bands if bio_bands is not None else _BIO_BANDS
    names = bb["names"]
    if abs(v) < bb["critical_abs"]:
        return names["critical"]
    if v >= bb["high"]:
        return names["high"]
    if v <= bb["low"]:
        return names["low"]
    return names["neutral"]


def need_band(value: float, bands: list | None = None) -> str:
    """A Ukrainian band for how big a need is right now (`0..1`) — the first NEED_BANDS entry whose
    `below` exceeds the value (низька / помірна / висока / дуже висока by default).
    `bands` (v1.2): per-agent need bands; None → the module globals."""
    nb = bands if bands is not None else NEED_BANDS
    for b in nb:
        if value < b["below"]:
            return b["name"]
    return nb[-1]["name"]


def biorhythm_block(bio: dict[str, float], mood_cfg: dict | None = None) -> str:
    """The day's biorhythm sub-block under `## Настрій`: one line per cycle — value + band + a
    behavioural cue (`- фізичний +0.27 (нейтрально): рівна енергія`).
    `mood_cfg` (v1.2): per-agent bands/labels/cues; None → the module globals."""
    _, _, _, _, bio_bands, bio_labels, bio_cues = _resolve_built(mood_cfg)
    lines = ["Біоритм дня (як це на тебе впливає):"]
    for key, label in bio_labels.items():
        v = bio[key]
        band = bio_band(v, bio_bands)
        cue = bio_cues.get(key, {}).get(band, "")
        line = f"- {label} {v:+.2f} ({band})"
        lines.append(f"{line}: {cue}" if cue else line)
    return "\n".join(lines)


def mood_block(
    needs: dict[str, float], bio: dict[str, float] | None = None, mood_cfg: dict | None = None
) -> str:
    """The `## Настрій` section: every need by its Ukrainian label + level + band + a behavioural
    cue (how to act at that level), then the biorhythm sub-block (omitted when `bio` is None, e.g.
    `BIORHYTHM=0`). Pure — no emotion label is computed; she reads her state and shapes her tone.
    `mood_cfg` (v1.2): per-agent labels/bands/cues; None → the module globals (agnika default)."""
    need_labels, need_cues, need_bands, _, _, _, _ = _resolve_built(mood_cfg)
    lines = ["## Настрій"]
    for key, label in need_labels.items():
        level = needs.get(key, 0.0)
        band = need_band(level, need_bands)
        cue = need_cues.get(key, {}).get(band, "")
        line = f"{label} {level:.2f} — {band}"
        lines.append(f"{line}: {cue}" if cue else line)
    if bio is not None:
        lines.append(biorhythm_block(bio, mood_cfg))
    return "\n".join(lines)
