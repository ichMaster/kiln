"""Unit: usage parsing (_usage_tokens) and the CLI failure reason (_cli_error_detail)."""

from __future__ import annotations

from types import SimpleNamespace

from kiln.usage import _cli_error_detail, _usage_tokens


def test_usage_tokens_from_cli_dict():
    assert _usage_tokens({"input_tokens": 5, "output_tokens": 7}) == (5, 7)


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
