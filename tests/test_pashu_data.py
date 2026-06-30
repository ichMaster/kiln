"""
KILN-058: Pashu's authored data (`state/pashu/`) loads and is a genuinely distinct agent.

Reads the committed `state/pashu/` via the KILN-056 loaders (no tmp / monkeypatch): the need model
and mood parse to valid structures, the canon and self-trigger prompts are present, and Pashu's
calibration differs from Agnika's. Pure config loading — no brain, no paid calls.
"""

from __future__ import annotations

from kiln.config import AgentConfig, AgentPaths
from kiln.memory import load_canon, load_prompts
from kiln.mood import _build


def test_pashu_files_load_to_valid_structures():
    pashu = AgentConfig.for_agent("pashu")
    assert pashu.agent_id == "pashu"
    # need model loaded (not the DEFAULT fallback shape only — real keys present)
    assert pashu.need_triggers and pashu.drift and pashu.satiation
    assert "connection" in pashu.need_triggers
    # mood is structurally valid (builds without falling back to DEFAULT_MOOD)
    assert "needs" in pashu.mood and "need_bands" in pashu.mood
    _build(pashu.mood)  # raises KeyError/TypeError if the mood config is malformed
    # canon + reach-out prompts authored
    p = AgentPaths.for_agent("pashu")
    assert load_canon(p.canon_file).strip()
    assert load_prompts(p.prompts_file).get("connection")  # has [connection] reach-out lines


def test_pashu_calibration_differs_from_agnika():
    pashu = AgentConfig.for_agent("pashu")
    agnika = AgentConfig.for_agent("agnika")
    assert pashu.agent_name != agnika.agent_name  # "Pashu" vs "Агніка"
    # a distinct need model: Pashu reaches out at a higher connection threshold
    assert (
        pashu.need_triggers["connection"]["threshold"]
        != agnika.need_triggers["connection"]["threshold"]
    )
    assert pashu.self_cooldown != agnika.self_cooldown  # quieter after reaching out
