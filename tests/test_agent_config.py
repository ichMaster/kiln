"""
KILN-056: per-agent calibration — `config.AgentConfig.for_agent`.

Unit only (pure config loading, no brain, no paid calls): the **default** agent reproduces the
module-global calibration byte-for-byte; a **named** agent loads its own `state/{id}/`
need-model / mood / tunables; a **missing** file heals to `DEFAULT_*`; two `AgentConfig`s are
**independent** objects (no shared mutable aliasing).
"""

from __future__ import annotations

import json

import yaml

import kiln.config as config
from kiln.config import DEFAULT_CONFIG, DEFAULT_NEEDS, AgentConfig, _build_needs
from kiln.mood import DEFAULT_MOOD, load_mood


def test_default_agent_reproduces_module_globals():
    """`for_agent(None|"agnika")` == today's globals — the v1.1 back-compat contract."""
    for agent in (None, "agnika"):
        c = AgentConfig.for_agent(agent)
        assert c.agent_id == "agnika"
        # need MODEL
        assert c.drift == config.DRIFT
        assert c.satiation == config.SATIATION
        assert c.need_triggers == config.NEED_TRIGGERS
        assert c.reach_out_need == config.REACH_OUT_NEED
        assert c.reach_out_models == config.REACH_OUT_MODELS
        assert c.reflect_need == config.REFLECT_NEED
        assert c.self_cooldown == config.SELF_COOLDOWN
        assert c.thought_cooldown == config.THOUGHT_COOLDOWN
        assert c.rest_wake == config.REST_WAKE
        # tunables
        assert c.chat_model == config.CHAT_MODEL
        assert c.deep_model == config.DEEP_MODEL
        assert c.thought_model == config.THOUGHT_MODEL
        assert c.tick_seconds == config.TICK_SECONDS
        assert c.think_threshold == config.THINK_THRESHOLD
        assert c.thinking_tokens == config.THINKING_TOKENS
        assert c.agent_name == config.AGENT_NAME
        assert c.recent_messages == config.RECENT_MESSAGES
        # mood
        assert c.mood == load_mood()


def _write_pashu(state_dir, *, drift_conn, chat_model):
    """Author a minimal-but-valid `state/pashu/` under a tmp STATE_DIR (distinct from Agnika)."""
    pashu = state_dir / "pashu"
    pashu.mkdir(parents=True)
    needs = dict(DEFAULT_NEEDS)
    needs["drift"] = {**DEFAULT_NEEDS["drift"], "connection": drift_conn}  # a distinct need model
    (pashu / "needs_model.yaml").write_text(yaml.safe_dump(needs), encoding="utf-8")
    (pashu / "mood.json").write_text(json.dumps(DEFAULT_MOOD, ensure_ascii=False), encoding="utf-8")
    (pashu / "config.yaml").write_text(yaml.safe_dump({"chat_model": chat_model}), encoding="utf-8")


def test_named_agent_loads_its_own_files(monkeypatch, tmp_path):
    sdir = tmp_path / "state"
    sdir.mkdir()
    _write_pashu(sdir, drift_conn=0.0042, chat_model="pashu-model")
    monkeypatch.setattr(config, "STATE_DIR", sdir)  # AgentPaths.for_agent resolves under this
    c = AgentConfig.for_agent("pashu")
    assert c.agent_id == "pashu"
    assert c.drift["connection"] == 0.0042  # its OWN need model
    assert c.chat_model == "pashu-model"  # its OWN tunables
    assert c.drift["connection"] != config.DRIFT["connection"]  # distinct from Agnika


def test_missing_files_fall_back_to_defaults(monkeypatch, tmp_path):
    sdir = tmp_path / "state"
    sdir.mkdir()  # but state/ghost/ does not exist
    monkeypatch.setattr(config, "STATE_DIR", sdir)
    c = AgentConfig.for_agent("ghost")
    assert c.drift == _build_needs(DEFAULT_NEEDS)["DRIFT"]
    assert c.satiation == _build_needs(DEFAULT_NEEDS)["SATIATION"]
    assert c.mood == DEFAULT_MOOD
    assert c.chat_model == DEFAULT_CONFIG["chat_model"]
    assert c.tick_seconds == DEFAULT_CONFIG["tick_seconds"]


def test_two_configs_are_independent(monkeypatch, tmp_path):
    """Independent: mutating one config's need model touches neither the other nor defaults."""
    sdir = tmp_path / "state"
    sdir.mkdir()
    _write_pashu(sdir, drift_conn=0.0042, chat_model="pashu-model")
    monkeypatch.setattr(config, "STATE_DIR", sdir)
    a = AgentConfig.for_agent("pashu")
    b = AgentConfig.for_agent("pashu")
    a.drift["connection"] = 999.0
    assert b.drift["connection"] == 0.0042
    assert DEFAULT_NEEDS["drift"]["connection"] != 999.0
