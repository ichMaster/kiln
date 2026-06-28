"""
kiln — mood & biorhythm (v0.9): pure formatters for Agnika's felt inner state.

The biorhythm is the three classic sine cycles (physical 23 / emotional 28 / intellectual 33 days)
from her birthday to "now", each −1..+1. Pure and deterministic — `now` and `birth` are injected,
so the output is unit-testable (no `datetime.now()` inside). In the engine the biorhythm is computed
ONCE at session start (it's the day's static baseline); the needs (KILN-036) stay live per turn.
"""

from __future__ import annotations

import datetime as _dt
import math

# Classic biorhythm cycle lengths, in days.
_PERIODS = {"physical": 23, "emotional": 28, "intellectual": 33}

# Ukrainian display labels for the needs (persona layer); insertion order = display order.
# `rest` reads as fatigue here (high = tired).
NEED_LABELS = {
    "connection": "близькість",
    "novelty": "новизна",
    "rest": "втома",
    "intensity": "напруга",
}


def biorhythm(now: _dt.datetime, birth: _dt.datetime) -> dict[str, float]:
    """The three biorhythm cycles for `now` relative to `birth` — each `sin(2π·days/period)` in
    −1..+1. Counts whole days between the two **dates** (time-of-day ignored), so it's tz-safe and
    constant across a day."""
    n = (now.date() - birth.date()).days
    return {name: math.sin(2 * math.pi * n / period) for name, period in _PERIODS.items()}


def bio_band(v: float) -> str:
    """A Ukrainian band for a biorhythm value (−1..+1): критичний день near a zero-crossing,
    підйом high-positive, спад low-negative, else нейтрально."""
    if abs(v) < 0.15:
        return "критичний день"
    if v >= 0.5:
        return "підйом"
    if v <= -0.5:
        return "спад"
    return "нейтрально"


def _bio_cue(emotional: float) -> str:
    """A one-line tone cue for the day, keyed on the emotional cycle (the most mood-relevant)."""
    if abs(emotional) < 0.15:
        return "Емоційно хисткий день — критична точка, можливі різкі зміни."
    if emotional >= 0.5:
        return "Емоційно піднесений день — тон тепліший, іскри більше."
    if emotional <= -0.5:
        return "Емоційно пригашений день — менше іскри, ближче до тиші."
    return "Рівний день — без різких сплесків."


def biorhythm_block(bio: dict[str, float]) -> str:
    """A compact Ukrainian rendering of the day's biorhythm (the three cycles + bands) with a
    one-line tone cue — the sub-block embedded under `## Настрій` (KILN-036)."""
    phys, emo, intel = bio["physical"], bio["emotional"], bio["intellectual"]
    head = (
        f"Біоритм дня: фізичний {phys:+.2f} ({bio_band(phys)}) · "
        f"емоційний {emo:+.2f} ({bio_band(emo)}) · "
        f"інтелектуальний {intel:+.2f} ({bio_band(intel)})."
    )
    return f"{head}\n{_bio_cue(emo)}"


def need_band(value: float) -> str:
    """A Ukrainian band for how big a need is right now (`0..1`): низька / помірна / висока /
    дуже висока."""
    if value < 0.35:
        return "низька"
    if value < 0.65:
        return "помірна"
    if value < 0.85:
        return "висока"
    return "дуже висока"


def mood_block(needs: dict[str, float], bio: dict[str, float] | None = None) -> str:
    """The `## Настрій` section: every need by its Ukrainian label + level + band, then the
    biorhythm sub-block (omitted when `bio` is None, e.g. `BIORHYTHM=0`). Pure — no emotion label is
    computed; she reads the levels + biorhythm and decides her own tone."""
    lines = ["## Настрій"]
    for key, label in NEED_LABELS.items():
        level = needs.get(key, 0.0)
        lines.append(f"{label} {level:.2f} — {need_band(level)}")
    if bio is not None:
        lines.append(biorhythm_block(bio))
    return "\n".join(lines)
