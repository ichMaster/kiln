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


try:
    _built = _build(load_mood())
except (KeyError, TypeError):
    _built = _build(DEFAULT_MOOD)  # a structurally malformed file falls back to the defaults
NEED_LABELS, NEED_CUES, NEED_BANDS, _PERIODS, _BIO_BANDS, _BIO_LABELS, BIO_CUES = _built


def biorhythm(now: _dt.datetime, birth: _dt.datetime) -> dict[str, float]:
    """The three biorhythm cycles for `now` relative to `birth` — each `sin(2π·days/period)` in
    −1..+1. Counts whole days between the two **dates** (time-of-day ignored), so it's tz-safe and
    constant across a day."""
    n = (now.date() - birth.date()).days
    return {name: math.sin(2 * math.pi * n / period) for name, period in _PERIODS.items()}


def bio_band(v: float) -> str:
    """A Ukrainian band for a biorhythm value (−1..+1): критичний день near a zero-crossing,
    підйом high-positive, спад low-negative, else нейтрально (thresholds from `state/mood.json`)."""
    names = _BIO_BANDS["names"]
    if abs(v) < _BIO_BANDS["critical_abs"]:
        return names["critical"]
    if v >= _BIO_BANDS["high"]:
        return names["high"]
    if v <= _BIO_BANDS["low"]:
        return names["low"]
    return names["neutral"]


def need_band(value: float) -> str:
    """A Ukrainian band for how big a need is right now (`0..1`) — the first NEED_BANDS entry whose
    `below` exceeds the value (низька / помірна / висока / дуже висока by default)."""
    for b in NEED_BANDS:
        if value < b["below"]:
            return b["name"]
    return NEED_BANDS[-1]["name"]


def biorhythm_block(bio: dict[str, float]) -> str:
    """The day's biorhythm sub-block under `## Настрій`: one line per cycle — value + band + a
    behavioural cue (`- фізичний +0.27 (нейтрально): рівна енергія`)."""
    lines = ["Біоритм дня (як це на тебе впливає):"]
    for key, label in _BIO_LABELS.items():
        v = bio[key]
        band = bio_band(v)
        cue = BIO_CUES.get(key, {}).get(band, "")
        line = f"- {label} {v:+.2f} ({band})"
        lines.append(f"{line}: {cue}" if cue else line)
    return "\n".join(lines)


def mood_block(needs: dict[str, float], bio: dict[str, float] | None = None) -> str:
    """The `## Настрій` section: every need by its Ukrainian label + level + band + a behavioural
    cue (how to act at that level), then the biorhythm sub-block (omitted when `bio` is None, e.g.
    `BIORHYTHM=0`). Pure — no emotion label is computed; she reads her state and shapes her own
    tone."""
    lines = ["## Настрій"]
    for key, label in NEED_LABELS.items():
        level = needs.get(key, 0.0)
        band = need_band(level)
        cue = NEED_CUES.get(key, {}).get(band, "")
        line = f"{label} {level:.2f} — {band}"
        lines.append(f"{line}: {cue}" if cue else line)
    if bio is not None:
        lines.append(biorhythm_block(bio))
    return "\n".join(lines)
