"""Characterization tests pinning current behavior of pure ``core`` helpers.

These lock the observable behavior of low-level ``apple_mail_mcp.core`` helpers
that had no direct coverage, so a behavior-preserving split of ``core.py`` into a
``core/`` package can be verified against an unchanged contract. Every assertion
here passes against the current single-file ``core.py``.
"""

from apple_mail_mcp.core import (
    equals_any_numeric_condition,
    normalize_message_ids,
    normalize_search_terms,
    parse_email_list,
    reject_unknown_account,
)


# ---------------------------------------------------------------------------
# normalize_message_ids: dedupe, strip, digit-only, str-coerce, order-preserve
# ---------------------------------------------------------------------------


def test_normalize_message_ids_empty_returns_empty() -> None:
    assert normalize_message_ids(None) == []
    assert normalize_message_ids([]) == []


def test_normalize_message_ids_strips_dedupes_and_filters_non_digits() -> None:
    result = normalize_message_ids([" 12 ", "12", "abc", "34", 56, "", "  "])
    assert result == ["12", "34", "56"]


# ---------------------------------------------------------------------------
# normalize_search_terms: combine single + list, strip, dedupe, order-preserve
# ---------------------------------------------------------------------------


def test_normalize_search_terms_empty_returns_empty() -> None:
    assert normalize_search_terms(None, None) == []
    assert normalize_search_terms("   ", ["", "  "]) == []


def test_normalize_search_terms_combines_strips_and_dedupes() -> None:
    result = normalize_search_terms(" hi ", ["hi", "yo", "", "  ", "yo"])
    assert result == ["hi", "yo"]


# ---------------------------------------------------------------------------
# equals_any_numeric_condition: AppleScript OR fragment
# ---------------------------------------------------------------------------


def test_equals_any_numeric_condition_empty_is_false() -> None:
    assert equals_any_numeric_condition("message id", []) == "false"


def test_equals_any_numeric_condition_builds_or_clause() -> None:
    assert (
        equals_any_numeric_condition("message id", ["1", "2"])
        == "(message id is 1 or message id is 2)"
    )


# ---------------------------------------------------------------------------
# parse_email_list: indicator-driven state machine
# ---------------------------------------------------------------------------


def test_parse_email_list_parses_read_flag_and_fields() -> None:
    output = (
        "===== header line skipped =====\n"
        "✉ Subject One\n"
        "From: alice@example.com\n"
        "Date: 2024-01-01\n"
        "Preview: hello there\n"
        "✓ Subject Two\n"
        "From: bob@example.com\n"
        "TOTAL EMAILS: 2\n"
    )
    emails = parse_email_list(output)
    assert emails == [
        {
            "subject": "Subject One",
            "is_read": False,
            "sender": "alice@example.com",
            "date": "2024-01-01",
            "preview": "hello there",
        },
        {
            "subject": "Subject Two",
            "is_read": True,
            "sender": "bob@example.com",
        },
    ]


def test_parse_email_list_handles_trailing_email_without_total() -> None:
    output = "✓ Only One\nFrom: solo@example.com\n"
    emails = parse_email_list(output)
    assert emails == [
        {"subject": "Only One", "is_read": True, "sender": "solo@example.com"}
    ]


# ---------------------------------------------------------------------------
# reject_unknown_account: routes through validate_account_name / account_not_found_json
# (conftest autouse stubs both for account names "Work" and "Missing")
# ---------------------------------------------------------------------------


def test_reject_unknown_account_known_returns_none() -> None:
    assert reject_unknown_account("Work") is None


def test_reject_unknown_account_unknown_returns_error_string() -> None:
    result = reject_unknown_account("Missing")
    assert isinstance(result, str)
    assert "account_not_found" in result


def test_reject_unknown_account_unknown_json_mode_returns_json() -> None:
    result = reject_unknown_account("Missing", json_error=True)
    assert result is not None
    assert '"error": "account_not_found"' in result
    assert '"account": "Missing"' in result
