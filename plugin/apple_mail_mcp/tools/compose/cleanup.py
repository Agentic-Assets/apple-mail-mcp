"""Identity-guarded internal cleanup helpers for short-lived CLI artifacts."""

from __future__ import annotations

import json

from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, normalize_message_ids
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.helpers import _resolve_account


def _expected_recipient_literal(expected_to: str) -> str | None:
    """Return an AppleScript list literal for non-empty expected recipients."""
    values = list(dict.fromkeys(item.strip().lower() for item in expected_to.split(",") if item.strip()))
    if not values:
        return None
    return "{" + ", ".join(f'"{escape_applescript(value)}"' for value in values) + "}"


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
        return account_error
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
    script = f'''
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
                if (count of actualToAddresses) is not (count of expectedToAddresses) then set cleanupIdentityMatches to false

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
                    return "DELETED|||" & currentDraftId
                end if
                return "IDENTITY_MISMATCH|||" & currentDraftId
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    '''
    try:
        raw = compose.run_applescript(script, timeout=effective_timeout).strip()
    except AppleScriptTimeout:
        return json.dumps({"deleted": False, "error": "smoke_draft_cleanup_timeout"})

    if raw.startswith("DELETED|||"):
        return json.dumps({"deleted": True, "draft_id": raw.split("|||", 1)[1].strip()})
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
