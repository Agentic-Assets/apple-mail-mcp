"""Pure helpers for Apple Mail Drafts verification payloads."""

from pathlib import Path
from typing import Any


def _parse_expected_attachments(expected_attachments: str | list[str] | None) -> list[str]:
    """Normalize expected attachment filenames or paths to basenames."""
    if expected_attachments is None:
        return []
    if isinstance(expected_attachments, str):
        raw_values = [item.strip() for item in expected_attachments.split(",")]
    else:
        raw_values = [str(item).strip() for item in expected_attachments]
    return [Path(value).name for value in raw_values if value]


def _split_csv_addresses(value: str | None) -> list[str]:
    """Return lowercase addresses from a comma-separated expected-recipient string."""
    if not value:
        return []
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _csv_contains_all(actual: str, expected: list[str]) -> bool | None:
    """Return whether all expected values appear in actual, or None when not checked."""
    if not expected:
        return None
    actual_addresses = set(_split_csv_addresses(actual))
    return all(item in actual_addresses for item in expected)


def _normalize_attachment_rows(raw_rows: str) -> list[dict[str, Any]]:
    """Parse attachment rows emitted as name::size pairs."""
    attachments: list[dict[str, Any]] = []
    for row in raw_rows.split(";;"):
        if not row:
            continue
        name, _, size_text = row.partition("::")
        try:
            size = int(size_text)
        except ValueError:
            size = None
        attachments.append({"filename": name, "size": size})
    return attachments


def _build_verify_draft_payload(
    *,
    numeric_id: str,
    subject: str,
    to_recips: str,
    cc_recips: str,
    bcc_recips: str,
    body_preview: str,
    in_reply_to: str,
    references: str,
    quoted_text: str,
    signature_text: str,
    attachment_rows: str,
    expected_to_values: list[str],
    expected_cc_values: list[str],
    expected_subject: str | None,
    expected_body_contains: str | None,
    expected_attachment_names: list[str],
    expected_signature: bool | None,
    require_quoted_original: bool | None,
) -> dict[str, Any]:
    """Build the public verify_draft JSON payload and expectation warnings."""
    attachments_found = _normalize_attachment_rows(attachment_rows)
    found_attachment_names = {item["filename"] for item in attachments_found}
    warnings: list[str] = []

    body_contains_expected = None
    if expected_body_contains is not None:
        body_contains_expected = expected_body_contains in body_preview
        if not body_contains_expected:
            warnings.append("expected_body_missing")

    subject_matches = None
    if expected_subject is not None:
        subject_matches = subject == expected_subject
        if not subject_matches:
            warnings.append("subject_mismatch")

    to_matches = _csv_contains_all(to_recips, expected_to_values)
    if to_matches is False:
        warnings.append("to_mismatch")
    cc_matches = _csv_contains_all(cc_recips, expected_cc_values)
    if cc_matches is False:
        warnings.append("cc_mismatch")

    missing_attachments = [name for name in expected_attachment_names if name not in found_attachment_names]
    attachment_status = "not_requested"
    if expected_attachment_names:
        attachment_status = "verified" if not missing_attachments else "missing"
        if missing_attachments:
            warnings.append("expected_attachments_missing")

    signature_detected = signature_text == "true"
    if expected_signature is True and not signature_detected:
        warnings.append("signature_missing")
    if expected_signature is False and signature_detected:
        warnings.append("signature_unexpected")

    quoted_original_detected = quoted_text == "true"
    if require_quoted_original is True and not quoted_original_detected:
        warnings.append("quoted_original_missing")
    if require_quoted_original is False and quoted_original_detected:
        warnings.append("quoted_original_unexpected")

    return {
        "draft_id": numeric_id,
        "found": True,
        "recipients": {"to": to_recips, "cc": cc_recips, "bcc": bcc_recips},
        "subject": subject,
        "subject_matches_expected": subject_matches,
        "body_preview": body_preview,
        "body_contains_expected": body_contains_expected,
        "signature": {
            "requested": expected_signature,
            "detected_above_quote": signature_detected,
        },
        "attachments": {
            "expected": expected_attachment_names,
            "found": attachments_found,
            "missing": missing_attachments,
            "status": attachment_status,
        },
        "quoted_original": {"detected": quoted_original_detected, "required": require_quoted_original},
        "threading": {"in_reply_to": in_reply_to, "references": references},
        "checks": {"to_matches_expected": to_matches, "cc_matches_expected": cc_matches},
        "warnings": warnings,
    }
