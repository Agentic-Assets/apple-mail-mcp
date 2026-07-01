"""Characterization tests for pure ``tools.search`` helpers with no prior direct
coverage. These pin the byte-exact AppleScript substrings and return contracts of
helpers that move verbatim into the planned ``search/`` package (records.py /
thread.py), so the behavior-preserving split cannot silently alter them.

They use no AppleScript I/O (pure functions), so they need no run_applescript
mock and pass identically against the pre-split single-file module.
"""

import pytest

from apple_mail_mcp.tools.search import (
    _applescript_string_list,
    _build_applescript_date,
    _extract_thread_header_tokens,
    _normalize_thread_header_id,
)


def test_build_applescript_date_start_of_day() -> None:
    script = _build_applescript_date("fromDate", "2024-03-05")
    assert "set fromDate to current date" in script
    assert "set year of fromDate to 2024" in script
    assert "set month of fromDate to March" in script
    assert "set day of fromDate to 5" in script
    assert "set time of fromDate to 0" in script


def test_build_applescript_date_end_of_day_seconds() -> None:
    script = _build_applescript_date("toDate", "2024-03-05", end_of_day=True)
    assert "set time of toDate to 86399" in script


def test_build_applescript_date_blank_returns_empty() -> None:
    assert _build_applescript_date("x", None) == ""
    assert _build_applescript_date("x", "") == ""


def test_build_applescript_date_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError, match=r"Invalid date '2024-13-40'\. Use YYYY-MM-DD"):
        _build_applescript_date("x", "2024-13-40")


def test_extract_thread_header_tokens_dedupes_normalizes_sorts() -> None:
    tokens = _extract_thread_header_tokens("<ABC@x.com>", "foo@Bar.com <ABC@x.com>")
    assert tokens == ["abc@x.com", "foo@bar.com"]


def test_extract_thread_header_tokens_ignores_empty() -> None:
    assert _extract_thread_header_tokens(None, "", "   ") == []


def test_normalize_thread_header_id_strips_brackets_and_lowercases() -> None:
    assert _normalize_thread_header_id("  <Foo@X>  ") == "foo@x"


def test_applescript_string_list_renders_literal() -> None:
    assert _applescript_string_list(["abc", "def"]) == '{"abc", "def"}'
    assert _applescript_string_list([]) == "{}"
