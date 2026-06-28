"""Unit: biorhythm — the deterministic per-day sine cycles (KILN-035). Pure, zero paid calls."""

from __future__ import annotations

import datetime as dt
import math

from kiln.mood import bio_band, biorhythm, biorhythm_block, mood_block, need_band

BIRTH = dt.datetime(2001, 8, 12, 17, 10)  # Agnika's canon birthday


def test_biorhythm_birthday_is_all_zero():
    bio = biorhythm(BIRTH, BIRTH)  # n=0 -> sin(0) = 0 for every cycle
    assert bio["physical"] == 0.0 and bio["emotional"] == 0.0 and bio["intellectual"] == 0.0


def test_biorhythm_emotional_peak_at_quarter_cycle():
    # emotional period = 28; +7 days -> sin(2π·7/28) = sin(π/2) = 1.0 (hand-computed peak)
    bio = biorhythm(dt.datetime(2001, 8, 19, 9, 0), BIRTH)
    assert bio["emotional"] == math.sin(math.pi / 2)  # exactly 1.0
    assert abs(bio["emotional"] - 1.0) < 1e-9


def test_biorhythm_counts_dates_not_time():
    # same date, different time-of-day -> identical (time ignored, so it's constant across a day)
    a = biorhythm(dt.datetime(2026, 6, 28, 1, 0), BIRTH)
    b = biorhythm(dt.datetime(2026, 6, 28, 23, 0), BIRTH)
    assert a == b


def test_biorhythm_values_in_range():
    for d in range(0, 70, 3):
        bio = biorhythm(BIRTH + dt.timedelta(days=d), BIRTH)
        assert all(-1.0 <= v <= 1.0 for v in bio.values())


def test_bio_band():
    assert bio_band(0.9) == "підйом"
    assert bio_band(-0.9) == "спад"
    assert bio_band(0.05) == "критичний день"  # near a zero-crossing
    assert bio_band(0.3) == "нейтрально"


def test_biorhythm_block_renders_cycles_bands_and_cue():
    block = biorhythm_block({"physical": 0.6, "emotional": -0.9, "intellectual": 0.05})
    assert "Біоритм дня:" in block
    assert "фізичний +0.60" in block and "емоційний -0.90" in block
    assert "підйом" in block and "спад" in block and "критичний день" in block
    # the one-line tone cue keyed on the (low) emotional cycle
    assert "Емоційно пригашений день" in block.splitlines()[-1]


# --- mood-from-needs (KILN-036) ---


def test_need_band_boundaries():
    assert need_band(0.30) == "низька"
    assert need_band(0.35) == "помірна" and need_band(0.55) == "помірна"
    assert need_band(0.65) == "висока" and need_band(0.72) == "висока"
    assert need_band(0.85) == "дуже висока" and need_band(0.99) == "дуже висока"


def test_mood_block_renders_all_needs_with_labels_and_bands():
    needs = {"connection": 0.72, "novelty": 0.30, "rest": 0.55, "intensity": 0.41}
    bio = {"physical": 0.6, "emotional": 0.2, "intellectual": -0.4}
    block = mood_block(needs, bio)
    assert block.startswith("## Настрій")
    assert "близькість 0.72 — висока" in block
    assert "новизна 0.30 — низька" in block
    assert "втома 0.55 — помірна" in block
    assert "напруга 0.41 — помірна" in block
    assert "Біоритм дня:" in block  # biorhythm sub-block embedded


def test_mood_block_missing_need_is_zero():
    block = mood_block(
        {"connection": 0.5}, {"physical": 0.0, "emotional": 0.0, "intellectual": 0.0}
    )
    assert "новизна 0.00 — низька" in block  # absent need -> 0.0
