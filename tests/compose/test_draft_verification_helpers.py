from apple_mail_mcp.tools.draft_verification import (
    _body_above_quote,
    _build_verify_draft_payload,
    _csv_contains_all,
    _normalize_attachment_rows,
    _parse_expected_attachments,
    _split_csv_addresses,
)


def test_parse_expected_attachments_uses_basenames_and_ignores_empty_values():
    assert _parse_expected_attachments("/tmp/report.pdf, , notes.txt") == ["report.pdf", "notes.txt"]
    assert _parse_expected_attachments(["/tmp/a.csv", " b.docx ", ""]) == ["a.csv", "b.docx"]
    assert _parse_expected_attachments(None) == []


def test_split_csv_addresses_normalizes_case_and_whitespace():
    assert _split_csv_addresses(" Sender@Example.com, cc@example.com , ") == [
        "sender@example.com",
        "cc@example.com",
    ]
    assert _split_csv_addresses(None) == []


def test_csv_contains_all_requires_exact_addresses_not_substrings():
    assert _csv_contains_all("ann@example.com, bob@example.com", ["ann@example.com"]) is True
    assert _csv_contains_all("joann@example.com", ["ann@example.com"]) is False
    assert _csv_contains_all("joann@example.com", []) is None


def test_normalize_attachment_rows_handles_bad_size_values():
    assert _normalize_attachment_rows("support.pdf::2048;;broken.dat::not-size;;empty-size::;;") == [
        {"filename": "support.pdf", "size": 2048},
        {"filename": "broken.dat", "size": None},
        {"filename": "empty-size", "size": None},
    ]


def test_build_verify_draft_payload_reports_all_warning_paths():
    payload = _build_verify_draft_payload(
        numeric_id="84053",
        subject="Actual",
        to_recips="joann@example.com",
        cc_recips="copy@example.com",
        bcc_recips="hidden@example.com",
        body_preview="Body without expected text",
        in_reply_to="<source@example.com>",
        references="<source@example.com> <older@example.com>",
        quoted_text="false",
        signature_text="false",
        attachment_rows="support.pdf::2048;;",
        expected_to_values=["ann@example.com"],
        expected_cc_values=["missing@example.com"],
        expected_subject="Expected",
        expected_body_contains="sentinel",
        expected_attachment_names=["support.pdf", "missing.docx"],
        expected_signature=True,
        require_quoted_original=True,
    )

    assert payload["draft_id"] == "84053"
    assert payload["found"] is True
    assert payload["recipients"]["bcc"] == "hidden@example.com"
    assert payload["threading"]["in_reply_to"] == "<source@example.com>"
    assert payload["attachments"]["status"] == "missing"
    assert payload["attachments"]["found"] == [{"filename": "support.pdf", "size": 2048}]
    assert payload["warnings"] == [
        "expected_body_missing",
        "subject_mismatch",
        "to_mismatch",
        "cc_mismatch",
        "expected_attachments_missing",
        "signature_missing",
        "quoted_original_missing",
    ]


def test_build_verify_draft_payload_reports_unexpected_signature_and_quote():
    payload = _build_verify_draft_payload(
        numeric_id="84054",
        subject="Subject",
        to_recips="to@example.com",
        cc_recips="",
        bcc_recips="",
        body_preview="Body",
        in_reply_to="",
        references="",
        quoted_text="true",
        signature_text="true",
        attachment_rows="",
        expected_to_values=[],
        expected_cc_values=[],
        expected_subject=None,
        expected_body_contains=None,
        expected_attachment_names=[],
        expected_signature=False,
        require_quoted_original=False,
    )

    assert payload["attachments"]["status"] == "not_requested"
    assert payload["checks"]["to_matches_expected"] is None
    assert payload["warnings"] == ["signature_unexpected", "quoted_original_unexpected"]


def test_body_above_quote_splits_at_apple_mail_attribution():
    region, has_boundary = _body_above_quote("New text here. On Monday, Ann wrote: old text also wrote: again")

    assert has_boundary is True
    assert region == "New text here. "


def test_body_above_quote_ignores_authored_wrote_prose_before_apple_mail_attribution():
    region, has_boundary = _body_above_quote("As Keynes wrote: prices will adjust. On Tue, Ann wrote: original message")

    assert has_boundary is True
    assert region == "As Keynes wrote: prices will adjust. "


def test_body_above_quote_uses_whole_body_for_bare_wrote_prose():
    body_preview = "As Keynes wrote: prices will adjust."

    region, has_boundary = _body_above_quote(body_preview)

    assert has_boundary is False
    assert region == body_preview


def test_body_above_quote_splits_at_outlook_header_block():
    region, has_boundary = _body_above_quote(
        "My reply. From: Ann Example <ann@example.com> Sent: Tuesday, July 7, 2026 9:00 AM "
        "To: Bob Example <bob@example.com> Subject: Project update Original message"
    )

    assert has_boundary is True
    assert region == "My reply. "


def test_body_above_quote_returns_full_text_when_no_boundary_present():
    region, has_boundary = _body_above_quote("No attribution marker in this body at all")

    assert has_boundary is False
    assert region == "No attribution marker in this body at all"


def _base_payload_kwargs(*, body_preview: str, expected_body_contains: str) -> dict:
    return {
        "numeric_id": "1",
        "subject": "Subject",
        "to_recips": "to@example.com",
        "cc_recips": "",
        "bcc_recips": "",
        "body_preview": body_preview,
        "in_reply_to": "",
        "references": "",
        "quoted_text": "true",
        "signature_text": "false",
        "attachment_rows": "",
        "expected_to_values": [],
        "expected_cc_values": [],
        "expected_subject": None,
        "expected_body_contains": expected_body_contains,
        "expected_attachment_names": [],
        "expected_signature": None,
        "require_quoted_original": None,
    }


def test_build_verify_draft_payload_body_needle_found_above_quote_boundary():
    # "confirmed" appears both above and inside the quote; found-above wins.
    payload = _build_verify_draft_payload(
        **_base_payload_kwargs(
            body_preview="Thanks, confirmed for Tuesday. On Monday, Ann wrote: confirmed the original plan",
            expected_body_contains="confirmed",
        )
    )

    assert payload["body_contains_expected"] is True
    assert "body_needle_only_in_quote" not in payload
    assert payload["warnings"] == []


def test_build_verify_draft_payload_preserves_authored_wrote_prose_above_quote():
    payload = _build_verify_draft_payload(
        **_base_payload_kwargs(
            body_preview="As Keynes wrote: prices will adjust. On Tue, Ann wrote: original message",
            expected_body_contains="prices will adjust",
        )
    )

    assert payload["body_contains_expected"] is True
    assert "body_needle_only_in_quote" not in payload
    assert payload["warnings"] == []


def test_build_verify_draft_payload_body_needle_only_in_quote_is_false_pass():
    # Regression for AGENTIC-1192 item 2: a needle that only appears inside
    # the quoted original must not pass verification for the new reply text.
    payload = _build_verify_draft_payload(
        **_base_payload_kwargs(
            body_preview="Thanks, see you then. On Monday, Ann wrote: let's meet at noon tomorrow",
            expected_body_contains="meet at noon",
        )
    )

    assert payload["body_contains_expected"] is False
    assert payload["body_needle_only_in_quote"] is True
    assert payload["warnings"] == ["expected_body_only_in_quote"]
    assert "expected_body_missing" not in payload["warnings"]


def test_build_verify_draft_payload_body_needle_uses_full_body_when_no_quote_boundary():
    payload = _build_verify_draft_payload(
        **_base_payload_kwargs(
            body_preview="Just a quick note with no quoted original at all",
            expected_body_contains="quick note",
        )
    )

    assert payload["body_contains_expected"] is True
    assert "body_needle_only_in_quote" not in payload
    assert payload["warnings"] == []


def test_build_verify_draft_payload_body_needle_missing_entirely_unchanged():
    payload = _build_verify_draft_payload(
        **_base_payload_kwargs(
            body_preview="Thanks, see you then. On Monday, Ann wrote: let's meet at noon tomorrow",
            expected_body_contains="totally absent phrase",
        )
    )

    assert payload["body_contains_expected"] is False
    assert "body_needle_only_in_quote" not in payload
    assert payload["warnings"] == ["expected_body_missing"]


def test_build_verify_draft_payload_requires_multiset_attachment_counts():
    payload = _build_verify_draft_payload(
        numeric_id="84055",
        subject="Subject",
        to_recips="to@example.com",
        cc_recips="",
        bcc_recips="",
        body_preview="Body",
        in_reply_to="",
        references="",
        quoted_text="false",
        signature_text="false",
        attachment_rows="support.pdf::2048;;other.pdf::1024;;",
        expected_to_values=[],
        expected_cc_values=[],
        expected_subject=None,
        expected_body_contains=None,
        expected_attachment_names=["support.pdf", "support.pdf"],
        expected_signature=None,
        require_quoted_original=None,
    )

    assert payload["attachments"]["status"] == "missing"
    assert payload["attachments"]["missing"] == ["support.pdf"]
    assert "expected_attachments_missing" in payload["warnings"]
