"""Identity-guarded internal cleanup helpers for short-lived CLI artifacts."""

from __future__ import annotations

import json

from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, normalize_message_ids
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.helpers import _resolve_account
from apple_mail_mcp.tools.draft_verification import _split_csv_addresses


def _expected_recipient_literal(expected_to: str) -> str | None:
    """Return an AppleScript list literal for non-empty expected recipients.

    Normalizes through the shared ``_split_csv_addresses`` helper (casefold,
    the one recipient-identity normalization used by verify_draft and the smoke
    CLI), then applies an ordered, case-insensitive dedupe before building the
    literal so ``--to "a@x.com, A@x.com"`` collapses to a single expected
    address.
    """
    values = list(dict.fromkeys(_split_csv_addresses(expected_to)))
    if not values:
        return None
    return "{" + ", ".join(f'"{escape_applescript(value)}"' for value in values) + "}"


def delete_draft_if_identity_matches_script(
    *,
    safe_account: str = "Test Account",
    numeric_id: str = "0",
    safe_subject: str = "SMOKE_SUBJECT",
    expected_to_literal: str = '{"smoke@example.invalid"}',
    safe_body_sentinel: str = "SMOKE_SENTINEL",
    effective_timeout: int = 120,
) -> str:
    """Return the atomic identity-guarded Drafts delete script.

    Takes the already-escaped account/subject/body-sentinel strings, a validated
    numeric draft id, and the pre-built AppleScript recipient list literal, and
    returns the full ``tell application "Mail"`` transaction that verifies the
    smoke draft's identity and deletes it only on an exact match.

    Recipient identity is proven by mutual containment (expected is a subset of
    actual AND actual is a subset of expected, both under ``ignoring case``),
    which is exact set equality and robust to duplicate recipients on either
    side: a missing recipient fails the first loop, an extra recipient fails the
    second, and duplicates pass both. There is deliberately no count-equality
    gate, because ``compose_email`` adds one ``to recipient`` per comma-split
    address without deduping while the expected literal is deduped, so the raw
    counts can differ for an identical recipient set. The body-sentinel check is
    a deliberate part of the identity guard and is retained.

    All parameters default to compile-safe sample values so the builder is
    callable with no arguments, satisfying the osacompile discovery contract
    (functions whose name ends in ``_script`` and whose output starts with
    ``tell application "Mail"`` are parse-checked).
    """
    return f'''
    tell application "Mail"
        with timeout of {effective_timeout} seconds
            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set targetDrafts to every message of draftsMailbox whose id is {numeric_id}
                if (count of targetDrafts) is not 1 then return "NOT_FOUND"

                set foundDraft to item 1 of targetDrafts
                set currentDraftId to (id of foundDraft) as string
                set expectedSubject to "{safe_subject}"
                set expectedToAddresses to {expected_to_literal}
                set expectedBodySentinel to "{safe_body_sentinel}"
                set cleanupIdentityMatches to true

                try
                    if (subject of foundDraft as string) is not expectedSubject then set cleanupIdentityMatches to false
                on error
                    set cleanupIdentityMatches to false
                end try

                set actualToAddresses to {{}}
                try
                    repeat with aRecipient in (to recipients of foundDraft)
                        try
                            set end of actualToAddresses to (address of aRecipient as string)
                        end try
                    end repeat
                end try

                repeat with expectedToAddress in expectedToAddresses
                    set expectedRecipientFound to false
                    repeat with actualToAddress in actualToAddresses
                        ignoring case
                            if (actualToAddress as string) is (expectedToAddress as string) then
                                set expectedRecipientFound to true
                            end if
                        end ignoring
                    end repeat
                    if not expectedRecipientFound then set cleanupIdentityMatches to false
                end repeat

                repeat with actualToAddress in actualToAddresses
                    set actualRecipientExpected to false
                    repeat with expectedToAddress in expectedToAddresses
                        ignoring case
                            if (actualToAddress as string) is (expectedToAddress as string) then
                                set actualRecipientExpected to true
                            end if
                        end ignoring
                    end repeat
                    if not actualRecipientExpected then set cleanupIdentityMatches to false
                end repeat

                try
                    set draftBody to content of foundDraft as string
                    if draftBody does not contain expectedBodySentinel then set cleanupIdentityMatches to false
                on error
                    set cleanupIdentityMatches to false
                end try

                if cleanupIdentityMatches then
                    delete foundDraft
                    -- Mail's delete command returning without error only proves
                    -- that deletion was issued. Re-read the exact Drafts id so
                    -- Exchange/IMAP lag fails closed instead of being reported
                    -- as confirmed cleanup.
                    repeat with readbackAttempt from 1 to 3
                        delay 0.5
                        set remainingDrafts to every message of draftsMailbox whose id is {numeric_id}
                        if (count of remainingDrafts) is 0 then
                            return "DELETED_CONFIRMED|||" & currentDraftId
                        end if
                    end repeat
                    return "DELETE_UNCONFIRMED|||" & currentDraftId
                end if
                return "IDENTITY_MISMATCH|||" & currentDraftId
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    '''


def delete_draft_if_identity_matches(
    *,
    account: str | None,
    draft_id: str,
    expected_subject: str,
    expected_to: str,
    expected_body_sentinel: str,
    timeout: int | None = None,
) -> str:
    """Delete one smoke draft only after atomic, in-Drafts identity validation.

    This is intentionally an internal helper, not a ``manage_drafts`` action.
    Generic delete callers retain their established exact-id behavior, while the
    smoke path is protected from Exchange Drafts numeric-id reassignment by
    checking its generated subject, requested recipients, and body sentinel in
    the same AppleScript transaction that performs the delete.
    """
    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return json.dumps({"deleted": False, "error": "account_resolution_failed", "detail": account_error})
    assert account is not None

    normalized_ids = normalize_message_ids([draft_id])
    if not normalized_ids:
        return json.dumps({"deleted": False, "error": "invalid_draft_id"})
    expected_to_literal = _expected_recipient_literal(expected_to)
    if not expected_subject or not expected_to_literal or not expected_body_sentinel:
        return json.dumps({"deleted": False, "error": "incomplete_smoke_draft_identity"})

    numeric_id = normalized_ids[0]
    safe_account = escape_applescript(account)
    safe_subject = escape_applescript(expected_subject)
    safe_body_sentinel = escape_applescript(expected_body_sentinel)
    effective_timeout = timeout if timeout is not None else 120
    script = delete_draft_if_identity_matches_script(
        safe_account=safe_account,
        numeric_id=numeric_id,
        safe_subject=safe_subject,
        expected_to_literal=expected_to_literal,
        safe_body_sentinel=safe_body_sentinel,
        effective_timeout=effective_timeout,
    )
    try:
        raw = compose.run_applescript(script, timeout=effective_timeout).strip()
    except AppleScriptTimeout:
        return json.dumps({"deleted": False, "error": "smoke_draft_cleanup_timeout"})

    if raw.startswith("DELETED_CONFIRMED|||"):
        return json.dumps(
            {
                "deleted": True,
                "confirmed": True,
                "draft_id": raw.split("|||", 1)[1].strip(),
            }
        )
    if raw.startswith("DELETE_UNCONFIRMED|||"):
        return json.dumps(
            {
                "deleted": False,
                "confirmed": False,
                "delete_issued": True,
                "draft_id": raw.split("|||", 1)[1].strip(),
                "error": "smoke_draft_cleanup_unconfirmed",
            }
        )
    if raw.startswith("IDENTITY_MISMATCH|||"):
        return json.dumps(
            {
                "deleted": False,
                "draft_id": raw.split("|||", 1)[1].strip(),
                "error": "smoke_draft_identity_mismatch",
            }
        )
    if raw == "NOT_FOUND":
        return json.dumps({"deleted": False, "error": "smoke_draft_not_found"})
    return json.dumps({"deleted": False, "error": "smoke_draft_cleanup_failed"})
