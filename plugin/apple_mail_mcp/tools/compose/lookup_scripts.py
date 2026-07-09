"""Pure AppleScript builders for resolving a message or draft by id/subject.

No ``run_applescript`` call lives here, so these are safe to import from both
``compose.py`` and the reply-script sibling without forming a cycle.
"""

from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.bounded_scan import build_bounded_message_scan
from apple_mail_mcp.core import escape_applescript, normalize_message_ids
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP, MESSAGE_LOOKUP_CAP


def _applescript_id_list_literal(ids: "set[str] | list[str] | None") -> str:
    """Render Mail outgoing-message ids as an AppleScript string-list literal.

    Ids are numeric in practice, but they are escaped defensively so a malformed
    value can never break out of the literal. An empty/None input yields ``{}``.
    """
    if not ids:
        return "{}"
    quoted = ", ".join('"' + escape_applescript(str(i)) + '"' for i in ids)
    return "{" + quoted + "}"


def _build_found_message_lookup(
    mailbox_var: str,
    *,
    message_id: str | None,
    subject_keyword: str | None,
    recent_days: float,
    found_var: str = "foundMessage",
    messages_var: str = "mailboxMessages",
    tool_name: str = "compose",
) -> "tuple[str, ToolError | None]":
    """Build AppleScript to resolve one message by id or bounded subject search.

    Subject-keyword fallback **requires** a positive date window. Without
    a date bound, Mail.app evaluates ``every message of mailbox whose
    subject contains "..."`` across the whole remote mailbox before
    slicing, which times out on 24K+ inboxes. When ``recent_days <= 0``
    the helper returns a ``ToolError`` envelope steering callers toward
    ``message_id`` or a bounded ``recent_days`` window.
    """
    if message_id:
        normalized = normalize_message_ids([message_id])
        if not normalized:
            return "", ToolError(
                code="INVALID_MESSAGE_ID",
                message=("message_id must be a numeric Apple Mail message id."),
                remediation={
                    "preferred": ("Pass a numeric Apple Mail message id from search_emails or list_inbox_emails"),
                },
            )
        numeric_id = normalized[0]
        return (
            f"""
        set targetMessages to every message of {mailbox_var} whose id is {numeric_id}
        set {found_var} to missing value
        if (count of targetMessages) > 0 then
            set {found_var} to item 1 of targetMessages
        end if
        """,
            None,
        )

    if recent_days <= 0:
        return "", ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(f"{tool_name} refuses to scan without recent_days; pass recent_days=2 or message_id."),
            remediation={
                "preferred": "Pass recent_days=2 (default) or message_id directly",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )

    safe_keyword = escape_applescript(subject_keyword or "")
    # Bind a bounded newest-first slice, then loop with in-AppleScript date
    # + subject filters. The historical pre-filter via `whose` over the bound
    # slice crashed on Gmail (refs point at [Gmail]/All Mail); the in-loop
    # form mirrors the search_emails fast path and is safe on every account.
    # Mail returns messages newest-first, so once a message is older than
    # the cutoff the remainder of the slice is too — early exit.
    date_setup = f"set recentCutoffDate to (current date) - ({float(recent_days)} * days)\n        "
    bounded_snippet = build_bounded_message_scan(mailbox_var, MESSAGE_LOOKUP_CAP)

    return (
        f"""
        {date_setup}{bounded_snippet}
        set {messages_var} to candidateMessages
        set {found_var} to missing value

        repeat with aMessage in {messages_var}
            try
                set messageDate to date received of aMessage
                if messageDate < recentCutoffDate then exit repeat
                set messageSubject to subject of aMessage
                if messageSubject contains "{safe_keyword}" then
                    set {found_var} to aMessage
                    exit repeat
                end if
            end try
        end repeat
        """,
        None,
    )


def _build_draft_lookup(subject_keyword: str) -> str:
    """Build capped AppleScript to find one draft by subject keyword.

    Emits bounded head/tail slices + in-loop ``if`` filters (no ``whose``).
    Mail has been observed returning newest Drafts first on real accounts, but
    the bounded tail fallback keeps send/open/delete tolerant of opposite
    ordering without ever materializing the whole Drafts mailbox.
    """
    safe_draft_subject = escape_applescript(subject_keyword)
    return f"""
                set totalDrafts to count of messages of draftsMailbox
                set headEnd to totalDrafts
                if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}
                if totalDrafts is 0 then
                    set candidateMessages to {{}}
                else
                    set candidateMessages to messages 1 thru headEnd of draftsMailbox
                    if totalDrafts > {DRAFT_LIST_CAP} then
                        set tailStart to totalDrafts - {DRAFT_LIST_CAP} + 1
                        if tailStart > headEnd then
                            set candidateMessages to candidateMessages & (messages tailStart thru totalDrafts of draftsMailbox)
                        end if
                    end if
                end if
                set foundDraft to missing value
                repeat with aMessage in candidateMessages
                    try
                        if (subject of aMessage) contains "{safe_draft_subject}" then
                            set foundDraft to aMessage
                            exit repeat
                        end if
                    end try
                end repeat
    """


def _compose_signature_script(message_var: str, signature_name: str | None) -> str:
    """AppleScript fragment that applies a native Mail signature by name."""
    if not signature_name:
        return ""
    safe_signature = escape_applescript(signature_name)
    return f'set message signature of {message_var} to signature "{safe_signature}"'
