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
# Each names the felt pressure that BUILDS and gets discharged — high = more, low = relieved (no
# inversion). самотність / нудьга are lacks (of contact / stimulation); втома / напруга are the
# opposite — accumulations (fatigue / charge). Either way high -> she wants the matching release.
NEED_LABELS = {
    "connection": "самотність",
    "novelty": "нудьга",
    "rest": "втома",
    "intensity": "напруга",
}

# How to BEHAVE at each need level — a short persona cue per (need, band). Appended to each
# `## Настрій` line so the number doesn't just describe a state, it tells her how to act on it.
NEED_CUES = {
    "connection": {
        "низька": "насичена контактом — дай простір, не чіпляйся",
        "помірна": "контакт є, та хочеться ближче",
        "висока": "бракує контакту — тягнись до людини, грійся, ініціюй",
        "дуже висока": "гостра самотність — озвися першою, відкрито й тепло",
    },
    "novelty": {
        "низька": "новизни досить — можна просто побути, без гонитви за стимулом",
        "помірна": "цікаво потроху",
        "висока": "прагнеш нового — копай глибше, кидай несподіване, міняй кут",
        "дуже висока": "нудьга пече — рвучко зміни тему, здивуй, провокуй",
    },
    "rest": {
        "низька": "повна сил — іскри більше, грайливіша, смілива",
        "помірна": "ще в тонусі",
        "висока": "втомлена — коротше й тихіше, без феєрверку, бережи себе",
        "дуже висока": "виснажена — мінімум слів, тепло й пауза; можеш і помовчати",
    },
    "intensity": {
        "низька": "спокійна — м'якше, без напору, рівне тепло",
        "помірна": "рівний заряд",
        "висока": "всередині кипить — гостріше, прямо, глибше",
        "дуже висока": "на межі — на повну, без буфера, в саму суть",
    },
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


# Ukrainian labels for the three cycles (display order) + how each band should shape her behaviour.
_BIO_LABELS = {"physical": "фізичний", "emotional": "емоційний", "intellectual": "інтелектуальний"}
BIO_CUES = {
    "physical": {
        "підйом": "енергії вдосталь — можна жвавіше, сміливіше",
        "спад": "сил мало — повільніше, коротше, бережи себе",
        "критичний день": "енергія стрибає — без різких ривків",
        "нейтрально": "рівна енергія",
    },
    "emotional": {
        "підйом": "тепла більше — відкритіша, ніжність ближче",
        "спад": "емоційно пригашена — стриманіше, тихіше",
        "критичний день": "емоційно хистко — обережніше з тоном, можливі сплески",
        "нейтрально": "рівний емоційний фон",
    },
    "intellectual": {
        "підйом": "думка гостра — складніші зв'язки, глибші образи",
        "спад": "розум млявіший — простіше, без перевантаження",
        "критичний день": "думки плутаються — не ускладнюй",
        "нейтрально": "ясність звичайна",
    },
}


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
