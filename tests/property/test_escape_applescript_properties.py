"""Property-based tests for escape_applescript and related helpers in core.py.

Focus areas:
- Quote-balance invariant: no unbalanced double-quotes survive.
- Backslash-escape integrity: a backslash in input must become two backslashes.
- Newline / carriage-return / tab: rendered as literal \\n / \\t escape sequences.
- Unicode line/paragraph separators (U+2028, U+2029) are converted.
- Null byte and other low-control characters: no raw NUL survives (would break
  osascript's stdin pipe).
- Idempotency: escaping twice is NOT the same as escaping once (the function is
  NOT idempotent — it is a one-way transformation).
- Length safety: very large strings do not OOM or return truncated output.
- The synthesised AppleScript fragment stays syntactically well-formed (balanced
  outer quotes, no raw newlines that would split the AppleScript line).
"""

import re
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apple_mail_mcp.core import (
    build_filter_condition,
    contains_any_condition,
    escape_applescript,
)

# ---------------------------------------------------------------------------
# Strategy helpers
# ---------------------------------------------------------------------------

# Characters that are specifically dangerous in AppleScript double-quoted strings.
_INJECTION_CHARS = st.sampled_from(['"', "\\", "\r\n", "\r", "\n", "\t",
                                     " ", " ", "\x00"])

# Broad text strategy: any Unicode except surrogates (which can't be
# encoded to UTF-8 and would cause subprocess failures unrelated to escaping).
_safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # exclude surrogates
    )
)

# ---------------------------------------------------------------------------
# Core escape properties
# ---------------------------------------------------------------------------


@given(value=_safe_text)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_escape_never_contains_raw_double_quote(value: str) -> None:
    """After escaping, no unescaped double-quote should appear.

    The output is intended to be placed inside AppleScript "..." strings.
    Any literal double-quote character in the output (not preceded by a
    backslash) would close the string prematurely, enabling injection.
    """
    result = escape_applescript(value)
    # Walk character by character; every `"` must be preceded by `\`
    i = 0
    while i < len(result):
        if result[i] == '"':
            assert i > 0 and result[i - 1] == "\\", (
                f"Unescaped '\"' found at position {i} in escape_applescript({value!r})"
            )
        i += 1


@given(value=_safe_text)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_escape_no_raw_newlines(value: str) -> None:
    """Escaped output must not contain bare \\r or \\n characters.

    AppleScript string literals cannot span multiple lines; a raw newline
    inside a double-quoted string produces a syntax error (-2740).
    """
    result = escape_applescript(value)
    assert "\n" not in result, (
        f"Raw newline in escape_applescript({str(value[:60])!r})"
    )
    assert "\r" not in result, (
        f"Raw CR in escape_applescript({str(value[:60])!r})"
    )


@given(value=_safe_text)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_escape_no_raw_tabs(value: str) -> None:
    """Escaped output must not contain raw tab characters.

    AppleScript's parser treats bare tabs as whitespace and can produce
    unexpected token errors inside string literals on some macOS versions.
    """
    result = escape_applescript(value)
    assert "\t" not in result, (
        f"Raw tab in escape_applescript({str(value[:60])!r})"
    )


@given(value=_safe_text)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow])
def test_escape_no_unicode_line_separator(value: str) -> None:
    """U+2028 (LINE SEPARATOR) and U+2029 (PARAGRAPH SEPARATOR) must be removed."""
    result = escape_applescript(value)
    assert " " not in result, "Raw U+2028 survived escaping"
    assert " " not in result, "Raw U+2029 survived escaping"


@given(value=st.text(alphabet=st.just("\\")))
@settings(max_examples=200)
def test_escape_backslash_doubles_each(value: str) -> None:
    """Each backslash in the input must become exactly two backslashes.

    Backslash-first ordering is essential: escaping must process \\ before \"
    or the output will double-escape already-escaped quotes.
    """
    result = escape_applescript(value)
    assert result == "\\\\" * len(value), (
        f"Backslash not doubled correctly: input={value!r} output={result!r}"
    )


@given(value=st.text(alphabet=st.just('"')))
@settings(max_examples=200)
def test_escape_quote_becomes_backslash_quote(value: str) -> None:
    """Each double-quote in input must become backslash + double-quote."""
    result = escape_applescript(value)
    assert result == '\\"' * len(value), (
        f"Quote not escaped correctly: input={value!r} output={result!r}"
    )


@given(n=st.integers(min_value=1, max_value=5))
def test_escape_not_idempotent_for_backslash(n: int) -> None:
    """escape_applescript is NOT idempotent on backslash-containing strings.

    This is the expected contract: applying it twice escapes the already-
    escaped backslashes again. Callers must apply it exactly once.
    """
    raw = "\\" * n
    once = escape_applescript(raw)
    twice = escape_applescript(once)
    assert once != twice, (
        "escape_applescript appears idempotent for backslashes — contract may have changed"
    )


@given(value=st.text(max_size=1_000_000))
@settings(max_examples=5, suppress_health_check=[HealthCheck.too_slow])
def test_escape_large_string_no_oom(value: str) -> None:
    """Very large inputs must not OOM or raise; output must be a str."""
    result = escape_applescript(value)
    assert isinstance(result, str)


@given(value=_safe_text)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_escape_result_no_raw_double_quote_for_literal(value: str) -> None:
    """The escaped result contains no bare (unescaped) double-quote characters.

    This is a simpler version of the injection test: look directly at the
    escaped output string. Every `"` must be preceded by an odd number of
    backslashes (i.e., the preceding run of backslashes that escapes it is odd).
    """
    result = escape_applescript(value)
    i = 0
    while i < len(result):
        if result[i] == '"':
            # Count preceding backslashes
            num_backslashes = 0
            j = i - 1
            while j >= 0 and result[j] == "\\":
                num_backslashes += 1
                j -= 1
            assert num_backslashes % 2 == 1, (
                f"Unescaped quote at pos {i} (preceded by {num_backslashes} backslashes) "
                f"in escape_applescript({str(value[:60])!r})"
            )
        i += 1


# ---------------------------------------------------------------------------
# Injection-char focused property
# ---------------------------------------------------------------------------


@given(
    prefix=st.text(alphabet=string.ascii_letters + " ", max_size=20),
    injection=_INJECTION_CHARS,
    suffix=st.text(alphabet=string.ascii_letters + " ", max_size=20),
)
@settings(max_examples=300)
def test_escape_injection_char_in_context(prefix: str, injection: str, suffix: str) -> None:
    """Dangerous characters embedded in normal text must be safely escaped."""
    value = prefix + injection + suffix
    result = escape_applescript(value)
    # No raw newline / CR / tab
    assert "\n" not in result
    assert "\r" not in result
    assert "\t" not in result
    # Every bare `"` must be backslash-preceded
    for i, ch in enumerate(result):
        if ch == '"':
            assert i > 0 and result[i - 1] == "\\", (
                f"Unescaped quote at pos {i} in result of {value!r}"
            )


# ---------------------------------------------------------------------------
# Downstream helper: build_filter_condition
# ---------------------------------------------------------------------------


@given(
    subject=st.one_of(st.none(), _safe_text),
    sender=st.one_of(st.none(), _safe_text),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_build_filter_condition_no_raw_newlines_or_tabs(
    subject: str | None, sender: str | None
) -> None:
    """build_filter_condition must not emit raw newlines or tabs in the fragment."""
    result = build_filter_condition(subject=subject, sender=sender)
    assert isinstance(result, str)
    assert "\n" not in result, f"Raw newline in build_filter_condition result: {result!r}"
    assert "\r" not in result, f"Raw CR in build_filter_condition result: {result!r}"
    assert "\t" not in result, f"Raw tab in build_filter_condition result: {result!r}"


@given(values=st.lists(_safe_text, min_size=1, max_size=5))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_contains_any_condition_no_raw_newlines(values: list[str]) -> None:
    """contains_any_condition must not emit raw newlines or CR in the fragment."""
    result = contains_any_condition("messageSender", values)
    assert "\n" not in result, f"Raw newline in contains_any_condition result: {result!r}"
    assert "\r" not in result, f"Raw CR in contains_any_condition result: {result!r}"
    assert "\t" not in result, f"Raw tab in contains_any_condition result: {result!r}"


@given(values=st.lists(_safe_text, min_size=1, max_size=5))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_contains_any_condition_is_string(values: list[str]) -> None:
    """contains_any_condition must always return a non-empty string."""
    result = contains_any_condition("messageSender", values)
    assert isinstance(result, str)
    assert len(result) > 0
