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


def test_biorhythm_block_renders_cycles_bands_and_cues():
    block = biorhythm_block({"physical": 0.6, "emotional": -0.9, "intellectual": 0.05})
    assert block.startswith("Біоритм дня")
    assert "- фізичний +0.60 (підйом)" in block
    assert "- емоційний -0.90 (спад)" in block
    assert "- інтелектуальний +0.05 (критичний день)" in block
    # each cycle carries a behavioural cue
    assert "енергії вдосталь" in block  # physical підйом
    assert "стриманіше" in block  # emotional спад
    assert "не ускладнюй" in block  # intellectual критичний день


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
    assert "самотність 0.72 — висока: бракує контакту" in block  # label + band + behavioural cue
    assert "нудьга 0.30 — низька" in block
    assert "втома 0.55 — помірна" in block
    assert "напруга 0.41 — помірна" in block
    assert "Біоритм дня" in block  # biorhythm sub-block embedded


def test_mood_block_missing_need_is_zero():
    block = mood_block(
        {"connection": 0.5}, {"physical": 0.0, "emotional": 0.0, "intellectual": 0.0}
    )
    assert "нудьга 0.00 — низька" in block  # absent need -> 0.0


# --- mood config loaded from state/mood.json ---


def test_load_mood_reads_the_file_with_cues():
    from kiln.mood import load_mood

    cfg = load_mood()  # the committed state/mood.json
    assert cfg["needs"]["connection"]["label"] == "самотність"
    assert "бракує контакту" in cfg["needs"]["connection"]["cues"]["висока"]
    assert cfg["biorhythm"]["periods"] == {"physical": 23, "emotional": 28, "intellectual": 33}


def test_load_mood_falls_back_when_missing(tmp_path):
    from kiln.mood import DEFAULT_MOOD, load_mood

    assert load_mood(tmp_path / "nope.json") == DEFAULT_MOOD


def test_load_mood_falls_back_on_invalid_json(tmp_path):
    from kiln.mood import DEFAULT_MOOD, load_mood

    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert load_mood(bad) == DEFAULT_MOOD


def test_default_mood_builds_working_labels_and_bands():
    # the minimal fallback must still produce labels + bands (cues empty)
    from kiln.mood import DEFAULT_MOOD, _build

    labels, cues, bands, periods, bio_bands, bio_labels, bio_cues = _build(DEFAULT_MOOD)
    assert labels["connection"] == "самотність" and bio_labels["physical"] == "фізичний"
    assert bands[0] == {"name": "низька", "below": 0.35}
    assert cues["connection"] == {}  # the fallback ships no cues (they live in the file)
