"""Regression tests for sender escaping in search.py.

Confirms that _build_search_script embeds the ESCAPED form of the sender
string in the generated AppleScript, not the raw value. This is a regression
test for the forgotten-wiring bug: `escaped_sender` was computed at line 277
but the raw `sender` was passed to the inline escape call at line 347, making
the `escaped_sender` variable a dead local. The fix rewires line 347 to use
the pre-computed `escaped_sender`.
"""

import asyncio
import re
from typing import Optional
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apple_mail_mcp.tools.search import _build_search_script
from apple_mail_mcp.core import escape_applescript

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_sender_fragment(script: str) -> Optional[str]:
    """Pull the value embedded in ``messageSender contains "..."`` from a script."""
    m = re.search(r'messageSender contains "([^"]*(?:\\"[^"]*)*)"', script)
    if m:
        return m.group(0)
    return None


def _script_for_sender(sender: str) -> str:
    """Build the AppleScript for a single-account search filtered by sender."""
    return _build_search_script(
        account="Work",
        mailbox="INBOX",
        subject_terms=None,
        sender=sender,
        has_attachments=None,
        read_status="all",
        date_from=None,
        date_to=None,
        include_content=False,
        content_length=300,
        offset=0,
        limit=20,
        body_text=None,
        recent_days=2.0,
    )


# ---------------------------------------------------------------------------
# Parametrized injection-class tests
# ---------------------------------------------------------------------------

_INJECTION_SENDERS = [
    ('a"b',         'raw double-quote'),
    ('a\\b',        'raw backslash'),
    ('a\nb',        'raw newline'),
    ('a\tb',        'raw tab'),
    ('a' + chr(0x2028) + 'b', 'Unicode line separator U+2028'),
]


@pytest.mark.parametrize("sender,label", _INJECTION_SENDERS, ids=[l for _, l in _INJECTION_SENDERS])
def test_sender_injection_char_is_escaped_in_script(sender: str, label: str) -> None:
    """The AppleScript produced by _build_search_script must contain the
    escaped form of the sender, not the raw injection character.

    This is the primary regression test: before the fix, escaped_sender was
    computed and then discarded, so the raw character would appear in the
    script instead of its escaped representation.
    """
    script = _script_for_sender(sender)
    expected_escaped = escape_applescript(sender)

    # The escaped form must be present in the script
    assert expected_escaped in script, (
        f"Escaped sender {expected_escaped!r} not found in script for {label}.\n"
        f"Raw sender was: {sender!r}"
    )

    # The raw injection character must NOT appear in the sender filter line
    fragment = _extract_sender_fragment(script)
    if fragment is not None:
        # Check that the raw dangerous chars are gone from the filter fragment
        assert '"' not in fragment.split('messageSender contains ', 1)[1][1:].rstrip('"'), (
            f"Raw unescaped double-quote found in sender filter for {label}: {fragment!r}"
        )
        assert '\n' not in fragment, (
            f"Raw newline found in sender filter for {label}: {fragment!r}"
        )
        assert '\t' not in fragment, (
            f"Raw tab found in sender filter for {label}: {fragment!r}"
        )


def test_escaped_sender_double_quote_not_raw_in_script() -> None:
    """Double-quote in sender must appear as backslash-quote, not bare quote."""
    script = _script_for_sender('foo"bar')
    # The escaped form is \\"  (backslash + quote) embedded in the f-string
    assert '\\"' in script, (
        f"Expected escaped double-quote in script, but got raw quote. Script snippet:\n"
        f"{script[script.find('messageSender'):script.find('messageSender')+100]}"
    )


def test_escaped_sender_backslash_doubled_in_script() -> None:
    """Backslash in sender must be doubled (\\\\) in the script."""
    script = _script_for_sender('foo\\bar')
    # After escaping: \ -> \\, so the script should contain \\
    assert '\\\\' in script, (
        "Expected doubled backslash in script for sender with backslash"
    )


def test_none_sender_produces_no_sender_filter() -> None:
    """When sender is None, no 'messageSender contains' filter appears in the script.

    Note: the AppleScript body still uses 'messageSender' as a local variable
    name (``set messageSender to ...``); what must be absent is the FILTER
    condition ``messageSender contains "..."``.
    """
    script = _build_search_script(
        account="Work",
        mailbox="INBOX",
        subject_terms=None,
        sender=None,
        has_attachments=None,
        read_status="all",
        date_from=None,
        date_to=None,
        include_content=False,
        content_length=300,
        offset=0,
        limit=20,
        body_text=None,
        recent_days=2.0,
    )
    assert 'messageSender contains "' not in script, (
        "messageSender contains filter should not appear when sender=None"
    )


def test_clean_sender_preserved_verbatim() -> None:
    """A safe sender (no special chars) must appear verbatim in the filter."""
    sender = "alice@example.com"
    script = _script_for_sender(sender)
    assert f'messageSender contains "{sender}"' in script, (
        f"Clean sender {sender!r} should appear verbatim in filter"
    )


# ---------------------------------------------------------------------------
# Hypothesis property test
# ---------------------------------------------------------------------------

# Re-use the _safe_text strategy from test_escape_applescript_properties — same definition
_safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),   # exclude surrogates
    )
)


@given(sender=_safe_text)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_sender_filter_no_raw_injection_chars(sender: str) -> None:
    """For any safe (non-surrogate) sender string, the generated AppleScript
    sender filter fragment must contain no raw double-quotes, newlines, CRs,
    or tabs that would break the AppleScript string literal.
    """
    script = _script_for_sender(sender)
    fragment = _extract_sender_fragment(script)
    if fragment is None:
        # No sender filter at all — only valid when sender was empty after escaping
        return

    # The fragment is `messageSender contains "..."` — extract the inner value
    # (the part between the outer quotes, which are part of the AppleScript syntax)
    inner_match = re.search(r'messageSender contains "(.*)"', fragment, re.DOTALL)
    if inner_match is None:
        return
    inner = inner_match.group(1)

    # No raw double-quote (all quotes must be backslash-escaped)
    for i, ch in enumerate(inner):
        if ch == '"':
            assert i > 0 and inner[i - 1] == '\\', (
                f"Unescaped double-quote at pos {i} in sender filter for {sender!r}: {inner!r}"
            )
    # No raw control characters that break AppleScript string literals
    assert '\n' not in inner, f"Raw newline in sender filter for {sender!r}"
    assert '\r' not in inner, f"Raw CR in sender filter for {sender!r}"
    assert '\t' not in inner, f"Raw tab in sender filter for {sender!r}"
