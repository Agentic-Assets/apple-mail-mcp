from apple_mail_mcp.tools.draft_verification import (
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
