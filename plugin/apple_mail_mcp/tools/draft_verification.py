"""Pure helpers for Apple Mail Drafts verification payloads."""

import re
from collections import Counter
from pathlib import Path
from typing import Any

_APPLE_QUOTE_ATTRIBUTION = re.compile(
    r"(?<!\w)(?i:on)\s+"
    r"(?i:(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)|"
    r"\d{1,4}(?:[/-]\d{1,4}){1,2}))\b.{0,500}?\b(?i:wrote):"
)
_ORIGINAL_MESSAGE_SEPARATOR = re.compile(r"-----\s*original message\s*-----", re.IGNORECASE)
_OUTLOOK_QUOTE_HEADERS = re.compile(
    r"(?<!\w)(?i:from):\s+\S.{0,1000}?"
    r"(?<!\w)(?i:sent|date):\s+\S.{0,1000}?"
    r"(?<!\w)(?i:to|cc):\s+\S.{0,1000}?"
    r"(?<!\w)(?i:subject):"
)


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
    """Return casefolded addresses from a comma-separated recipient string.

    The one shared normalization for recipient-identity comparisons
    (verify_draft containment checks, the smoke CLI's exact-set check, and
    the identity-guarded cleanup literal); casefold so Unicode addresses
    compare the same everywhere.
    """
    if not value:
        return []
    return [item.strip().casefold() for item in value.split(",") if item.strip()]


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


def _body_above_quote(body_preview: str) -> tuple[str, bool]:
    """Return the authored region before a reliable flattened quote boundary.

    ``body_preview`` has already had line breaks flattened to spaces and is
    capped at 5000 characters by the AppleScript verifier. Bare ``"wrote:"``
    is therefore not enough to identify a quote: it can appear in ordinary
    prose. Recognize only established Apple Mail attributions (``On <date>,
    ... wrote:``), Outlook's structured header block, or its original-message
    separator. If none is present, return the full preview so callers retain
    whole-body behavior instead of rejecting valid authored text.
    """
    boundaries = [
        match.start()
        for pattern in (_APPLE_QUOTE_ATTRIBUTION, _ORIGINAL_MESSAGE_SEPARATOR, _OUTLOOK_QUOTE_HEADERS)
        if (match := pattern.search(body_preview)) is not None
    ]
    if not boundaries:
        return body_preview, False
    return body_preview[: min(boundaries)], True


def _build_source_resolution(
    in_reply_to: str,
    resolve_recent_days: float,
    matched_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the ``verify_draft`` ``source`` payload from a bounded search match.

    ``matched_record`` is the first ``search_emails`` JSON item resolved for
    the draft's ``In-Reply-To`` header via a single bounded search (already
    performed by the caller), or ``None`` when no match was found within the
    resolution window. This helper never performs I/O; it only shapes the
    tri-state result honestly (resolved / no header / not found in window).
    """
    if not in_reply_to.strip():
        return {"resolved": False, "reason": "no_in_reply_to_header"}
    if matched_record is None:
        return {"resolved": False, "reason": "not_found_in_window", "resolved_within_days": resolve_recent_days}
    return {
        "resolved": True,
        "message_id": matched_record.get("message_id"),
        "subject": matched_record.get("subject"),
        "sender": matched_record.get("sender"),
        "mailbox": "INBOX",
        "received_at": matched_record.get("received_date"),
        "resolved_within_days": resolve_recent_days,
    }


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
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the public verify_draft JSON payload and expectation warnings."""
    attachments_found = _normalize_attachment_rows(attachment_rows)
    warnings: list[str] = []

    body_contains_expected = None
    body_needle_only_in_quote = False
    if expected_body_contains is not None:
        above_quote, has_quote_boundary = _body_above_quote(body_preview)
        body_contains_expected = expected_body_contains in above_quote
        if not body_contains_expected:
            if has_quote_boundary and expected_body_contains in body_preview:
                body_needle_only_in_quote = True
                warnings.append("expected_body_only_in_quote")
            else:
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

    missing_attachments: list[str] = []
    if expected_attachment_names:
        expected_counts = Counter(expected_attachment_names)
        found_counts = Counter(item["filename"] for item in attachments_found)
        for name, expected_count in expected_counts.items():
            shortfall = expected_count - found_counts.get(name, 0)
            if shortfall > 0:
                missing_attachments.extend([name] * shortfall)
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

    payload: dict[str, Any] = {
        "draft_id": numeric_id,
        "found": True,
        "recipients": {"to": to_recips, "cc": cc_recips, "bcc": bcc_recips},
        "subject": subject,
        "subject_matches_expected": subject_matches,
        "body_preview": body_preview,
        "body_contains_expected": body_contains_expected,
        **({"body_needle_only_in_quote": True} if body_needle_only_in_quote else {}),
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
    }
    if source is not None:
        payload["source"] = source
    payload["checks"] = {"to_matches_expected": to_matches, "cc_matches_expected": cc_matches}
    payload["warnings"] = warnings
    return payload
