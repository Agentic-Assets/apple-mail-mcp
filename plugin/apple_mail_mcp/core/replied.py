"""Replied-to Message-ID detection: Sent-mailbox AppleScript fragments + the Python fetch wrapper.

``fetch_replied_ids`` resolves its default runner through ``core.run_applescript``
so the ``patch('apple_mail_mcp.core.run_applescript')`` test seam reaches it.
"""

import logging

from apple_mail_mcp import core
from apple_mail_mcp.core.applescript import AppleScriptRunner, AppleScriptTimeout
from apple_mail_mcp.core.escaping import escape_applescript

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Performance caps for replied_ids_script
# ---------------------------------------------------------------------------
# Full-header reads are expensive on Exchange (one IMAP download each).
# Capped here to avoid per-message IMAP downloads on large Exchange inboxes.
# Internal-only — not exposed as tool parameters.
REPLIED_HEADER_READ_CAP = 10


def fetch_replied_ids_script(account: str, sent_cap: int = 200) -> str:
    """Return a self-contained AppleScript that prints one Message-ID per line.

    Used by tools outside ``smart_inbox`` (``list_inbox_emails``,
    ``search_emails``) that need to know which Internet Message-IDs the
    user has replied to, without duplicating the helper handler and Sent
    mailbox scan inline.

    The script emits one ``<id@host>`` line per replied-to Message-ID.
    Empty output means the Sent mailbox was missing or contained no
    parseable replies.
    """
    escaped_account = escape_applescript(account)
    return f'''
    tell application "Mail"
        try
            set targetAccount to account "{escaped_account}"
            {replied_ids_script(account_var="targetAccount", sent_cap=sent_cap)}
            set AppleScript's text item delimiters to linefeed
            set outputText to repliedIds as string
            set AppleScript's text item delimiters to ""
            return outputText
        on error errMsg
            return ""
        end try
    end tell
    '''


def fetch_replied_ids(
    account: str,
    sent_cap: int = 200,
    timeout: int | None = 60,
    runner: AppleScriptRunner | None = None,
) -> set[str]:
    """Return the set of Message-IDs the user has replied to for *account*.

    Wraps the AppleScript helper and turns the newline-delimited output
    into a Python ``set`` for O(1) membership checks. Returns an empty
    set on timeout or error so callers can degrade gracefully (no false
    "already replied" flags when detection fails).

    The *runner* parameter is an injection seam so tools using
    module-local ``run_applescript`` symbols (which tests patch) drive
    the same script through their own patch surface. Defaults to the
    core ``run_applescript``.
    """
    script = fetch_replied_ids_script(account=account, sent_cap=sent_cap)
    fn = runner if runner is not None else core.run_applescript
    try:
        raw = fn(script, timeout=timeout)
    except AppleScriptTimeout:
        return set()
    except Exception as exc:
        logger.warning(
            "fetch_replied_ids failed for account %r: %s: %s",
            account,
            type(exc).__name__,
            exc,
        )
        return set()
    ids: set[str] = set()
    for line in raw.splitlines():
        token = line.strip()
        if not token:
            continue
        # Normalize: ensure leading "<" and trailing ">"
        if not token.startswith("<"):
            token = "<" + token
        if not token.endswith(">"):
            token = token + ">"
        ids.add(token)
    return ids


def sent_mailbox_resolve_script(var_name: str, account_var: str) -> str:
    """Return AppleScript that resolves the Sent mailbox with fallback names.

    Tries "Sent Messages" → "Sent" → "Sent Items"; sets *var_name* to
    ``missing value`` if none are found. Caller decides how to react.
    """
    return f"""
            set {var_name} to missing value
            try
                set {var_name} to mailbox "Sent Messages" of {account_var}
            on error
                try
                    set {var_name} to mailbox "Sent" of {account_var}
                on error
                    try
                        set {var_name} to mailbox "Sent Items" of {account_var}
                    end try
                end try
            end try
    """


def replied_ids_script(
    account_var: str = "targetAccount",
    sent_cap: int = 200,
    replied_var: str = "repliedIds",
) -> str:
    """Return AppleScript that builds a Message-ID lookup list from Sent.

    Sets one AppleScript variable inside a ``tell application "Mail"`` block:

    - *replied_var* (default ``repliedIds``): list of Internet Message-IDs the
      user has replied to, extracted from ``In-Reply-To:`` and ``References:``
      headers in the Sent mailbox (each token like ``<abc@example.com>``).

    Replied detection is header-based only (Message-ID / In-Reply-To /
    References). No subject-fallback path exists — subject matching is
    unreliable and prohibitively expensive on large Exchange inboxes.

    Performance:
        - Bounded newest-first slice of *sent_cap* messages (default 200).
        - NO ``whose`` clauses — Mail.app can materialize remote sent mailboxes.
        - Full-header reads are capped at ``REPLIED_HEADER_READ_CAP`` messages
          to avoid per-message IMAP downloads on Exchange.
        - Each per-message access is wrapped in ``try`` so one bad message
          doesn't kill the loop.
        - The entire block is wrapped in ``try`` so a missing sent mailbox
          or transient error doesn't break the caller.
    """
    sent_resolve = sent_mailbox_resolve_script("sentMailbox", account_var)
    return f"""
            set {replied_var} to {{}}
            try
                {sent_resolve}
                if sentMailbox is not missing value then
                    set sentMessages to {{}}
                    set sentCount to count of messages of sentMailbox
                    if sentCount > {sent_cap} then
                        set sentUpperBound to {sent_cap}
                    else
                        set sentUpperBound to sentCount
                    end if
                    -- Limit full-header reads to {REPLIED_HEADER_READ_CAP} messages to avoid
                    -- per-message IMAP downloads on Exchange which cause timeouts.
                    set headerReadCap to {REPLIED_HEADER_READ_CAP}
                    set headerReadCount to 0
                    if sentUpperBound > 0 then
                        set sentMessages to messages 1 thru sentUpperBound of sentMailbox
                    end if
                    repeat with aSentMessage in sentMessages
                        try
                            set msgHeaders to ""
                            try
                                if headerReadCount < headerReadCap then
                                    set msgHeaders to all headers of aSentMessage
                                    set headerReadCount to headerReadCount + 1
                                end if
                            end try
                            if msgHeaders is not "" then
                                -- Scan header lines for In-Reply-To: and References:
                                set AppleScript's text item delimiters to {{return, linefeed}}
                                set headerLines to text items of msgHeaders
                                set AppleScript's text item delimiters to ""
                                repeat with headerLine in headerLines
                                    set headerLineText to headerLine as string
                                    set isReplyHeader to false
                                    ignoring case
                                        if headerLineText starts with "In-Reply-To:" then
                                            set isReplyHeader to true
                                        else if headerLineText starts with "References:" then
                                            set isReplyHeader to true
                                        end if
                                    end ignoring
                                    if isReplyHeader then
                                        -- Extract <id@host> tokens via text item
                                        -- delimiters: split on "<" then trim at ">".
                                        set AppleScript's text item delimiters to "<"
                                        set ltParts to text items of headerLineText
                                        set AppleScript's text item delimiters to ""
                                        if (count of ltParts) > 1 then
                                            repeat with i from 2 to (count of ltParts)
                                                set tokenText to item i of ltParts as string
                                                set AppleScript's text item delimiters to ">"
                                                set gtParts to text items of tokenText
                                                set AppleScript's text item delimiters to ""
                                                if (count of gtParts) > 1 then
                                                    set idValue to item 1 of gtParts as string
                                                    if idValue is not "" then
                                                        set end of {replied_var} to "<" & idValue & ">"
                                                    end if
                                                end if
                                            end repeat
                                        end if
                                    end if
                                end repeat
                            end if
                        end try
                    end repeat
                end if
            end try
    """
