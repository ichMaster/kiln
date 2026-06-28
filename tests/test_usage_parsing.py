"""Unit: usage parsing (_usage_tokens / _cache_tokens), the per-turn line, and the CLI reason."""

from __future__ import annotations

from types import SimpleNamespace

from kiln.usage import _cache_tokens, _cli_error_detail, _usage_tokens, print_tech, usage_record


def test_usage_tokens_from_cli_dict():
    assert _usage_tokens({"input_tokens": 5, "output_tokens": 7}) == (5, 7)


def test_cache_tokens_from_cli_dict():
    u = {"cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}
    assert _cache_tokens(u) == (100, 20)


def test_cache_tokens_from_sdk_object():
    obj = SimpleNamespace(cache_read_input_tokens=30, cache_creation_input_tokens=0)
    assert _cache_tokens(obj) == (30, 0)


def test_cache_tokens_absent_is_zero():
    assert _cache_tokens(None) == (0, 0)
    assert _cache_tokens({"input_tokens": 5}) == (0, 0)  # no cache fields


def test_print_tech_shows_cache_when_present(capsys):
    rec = usage_record(
        "claude-haiku-4-5",
        {"input_tokens": 3, "output_tokens": 4, "cache_read_input_tokens": 50},
    )
    print_tech(rec)
    out = capsys.readouterr().out
    assert "cache 50r/0w" in out  # the cache segment


def test_print_tech_omits_cache_when_absent(capsys):
    print_tech(usage_record("claude-haiku-4-5", {"input_tokens": 3, "output_tokens": 4}))
    assert "cache" not in capsys.readouterr().out


def test_usage_tokens_from_sdk_object():
    obj = SimpleNamespace(input_tokens=5, output_tokens=7)
    assert _usage_tokens(obj) == (5, 7)


def test_usage_tokens_none():
    assert _usage_tokens(None) == (None, None)


def test_usage_tokens_missing_fields_default_none():
    assert _usage_tokens({}) == (None, None)


def test_cli_error_detail_prefers_stderr():
    r = SimpleNamespace(stderr="  boom  \n", stdout="")
    assert _cli_error_detail(r) == "boom"


def test_cli_error_detail_reads_json_result_field():
    r = SimpleNamespace(stderr="", stdout='{"result": "перевищено ліміт"}')
    assert _cli_error_detail(r) == "перевищено ліміт"


def test_cli_error_detail_plain_stdout():
    r = SimpleNamespace(stderr="", stdout="несподіваний вивід")
    assert _cli_error_detail(r) == "несподіваний вивід"


def test_cli_error_detail_empty():
    r = SimpleNamespace(stderr="", stdout="")
    assert _cli_error_detail(r) == "no details"


def test_cli_error_detail_truncates_long():
    r = SimpleNamespace(stderr="x" * 500, stdout="")
    assert len(_cli_error_detail(r)) == 300
