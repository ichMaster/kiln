"""
Config layering contract: non-secret tunables resolve **env var > YAML file > built-in default**.

state/config.yaml (agent) + server.yaml (server) are committed; .env holds only secrets + personal
fields. A missing/broken YAML falls back to the DEFAULT_* dict, so a fresh clone still starts.
"""

from __future__ import annotations

from kiln import config


def test_load_config_merges_file_over_defaults(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("tick_seconds: 2.5\nchat_model: my-model\n", encoding="utf-8")
    cfg = config.load_config(f)
    assert cfg["tick_seconds"] == 2.5  # from the file
    assert cfg["chat_model"] == "my-model"  # from the file
    assert cfg["deep_model"] == config.DEFAULT_CONFIG["deep_model"]  # default kept for absent keys


def test_load_config_missing_or_broken_falls_back(tmp_path):
    assert config.load_config(tmp_path / "nope.yaml") == config.DEFAULT_CONFIG
    bad = tmp_path / "bad.yaml"
    bad.write_text("just a string, not a mapping", encoding="utf-8")
    assert config.load_config(bad) == config.DEFAULT_CONFIG


def test_load_server_config_defaults(tmp_path):
    assert config.load_server_config(tmp_path / "nope.yaml") == config.DEFAULT_SERVER


def test_precedence_env_over_yaml_over_default(monkeypatch):
    cfg = {"tick_seconds": 1.0}
    assert config._opt_float({}, "tick_seconds", "X_KILN_TICK", 0.5) == 0.5  # default
    assert config._opt_float(cfg, "tick_seconds", "X_KILN_TICK", 0.5) == 1.0  # yaml > default
    monkeypatch.setenv("X_KILN_TICK", "9.0")
    assert config._opt_float(cfg, "tick_seconds", "X_KILN_TICK", 0.5) == 9.0  # env > yaml


def test_bool_resolution(monkeypatch):
    cfg = {"world_awareness": False}
    assert config._opt_bool(cfg, "world_awareness", "X_KILN_WA", True) is False  # yaml False
    monkeypatch.setenv("X_KILN_WA", "1")
    assert config._opt_bool(cfg, "world_awareness", "X_KILN_WA", True) is True  # env "1" wins


def test_int_resolution(monkeypatch):
    cfg = {"max_facts": 7}
    assert config._opt_int(cfg, "max_facts", "X_KILN_MF", 0) == 7
    monkeypatch.setenv("X_KILN_MF", "3")
    assert config._opt_int(cfg, "max_facts", "X_KILN_MF", 0) == 3
