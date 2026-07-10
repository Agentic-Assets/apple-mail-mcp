"""AppleScript generators for the ``search_emails`` scan path.

Pure string building only (escape / mailbox refs / scan-bound math); no
``run_applescript`` call lives here, so no package-namespace routing is needed.
"""

from apple_mail_mcp.bounded_scan import compute_scan_upper_bound
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import build_mailbox_ref, escape_applescript
from apple_mail_mcp.core.reply_state import was_replied_fragment
from apple_mail_mcp.tools.search.records import _build_applescript_date


def _build_search_script(
    account: str,
    mailbox: str,
    subject_terms: list[str] | None,
    sender: str | None,
    has_attachments: bool | None,
    read_status: str,
    date_from: str | None,
    date_to: str | None,
    include_content: bool,
    content_length: int,
    offset: int,
    limit: int,
    body_text: str | None,
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    internet_message_id: str | None = None,
    recent_days: float = 0.0,
    timeout: int | None = None,
    date_from_explicit: bool = False,
    mailboxes: list[str] | None = None,
) -> tuple[str, bool, bool]:
    """Build the AppleScript for a single account's search.

    The script caps message collection inside AppleScript via either a
    ``whose`` clause sliced down to ``items 1 thru collectLimit`` or a
    ``messages 1 thru collectLimit`` bound directly, so we never materialize
    the full message list of a large (10K+) mailbox.

    Scan-cap scales with ``recent_days`` so that narrow filters (sender,
    subject_terms) over a wider date window actually inspect a meaningful
    portion of that window — otherwise a 7-day query with default limit=20
    would only inspect the 21 newest messages and silently miss matches
    further back. Floor stays at ``collect_limit + offset`` and ceiling
    caps at ``SEARCH_WINDOW_CAP`` to keep Mail bounded on remote IMAP/Exchange mailboxes.

    Performance guidance — body_text:
        When ``body_text`` is set without an explicit ``date_from``, the
        scan_cap is further capped at ``BODY_SEARCH_AUTO_CAP`` to prevent hundreds of cold-cache
        IMAP body fetches (each can take ~1s on large Exchange inboxes).
        When the caller passes an explicit ``date_from`` (``date_from_explicit=True``),
        the cap is left as-is because the caller has explicitly bounded the window.
        A ``body_search_capped`` key is returned in structured responses when the
        cap fires to help callers understand why results may be incomplete.
    """
    escaped_sender = escape_applescript(sender) if sender else None
    escaped_sender_exact = escape_applescript(sender_exact.strip()) if sender_exact and sender_exact.strip() else None
    escaped_sender_domain = (
        escape_applescript(sender_domain.strip().lstrip("@")) if sender_domain and sender_domain.strip() else None
    )
    normalized_internet_message_id = ""
    if internet_message_id and internet_message_id.strip():
        normalized_internet_message_id = internet_message_id.strip()
        if not normalized_internet_message_id.startswith("<"):
            normalized_internet_message_id = "<" + normalized_internet_message_id
        if not normalized_internet_message_id.endswith(">"):
            normalized_internet_message_id = normalized_internet_message_id + ">"
    escaped_internet_message_id = escape_applescript(normalized_internet_message_id)
    use_body_search = body_text is not None

    collect_limit = limit + 1  # +1 for has_more probe; offset is decremented separately
    base_cap = collect_limit + offset
    # Window cap from the shared bounded-scan helper (Phase A of the
    # whose-elimination refactor — see plugin/apple_mail_mcp/bounded_scan.py).
    # We floor at base_cap so callers paginating past the helper's cap still
    # get a slice large enough to honor their offset+limit.
    if recent_days and recent_days > 0:
        window_cap = compute_scan_upper_bound(recent_days)
        # Narrow header searches are usually "needle" lookups. On large
        # Exchange accounts, binding hundreds of recent messages just to prove
        # a no-hit subject does not exist can exceed wrapper timeouts. Keep the
        # scan bounded by the caller's requested page, and only widen as the
        # requested recent window widens.
        if (
            subject_terms
            and not sender
            and not sender_exact
            and not sender_domain
            and not internet_message_id
            and body_text is None
            and has_attachments is None
            and read_status == "all"
        ):
            scan_cap = base_cap
        else:
            scan_cap = max(base_cap, window_cap)
    else:
        scan_cap = base_cap

    # Body-search cap: reading ``content of aMessage`` for every candidate is
    # O(N × message-size) and triggers cold-cache IMAP fetches on large remote
    # mailboxes. When the caller has not explicitly bounded the window with
    # ``date_from``, cap at 100 to keep wall time reasonable. When an explicit
    # ``date_from`` is passed the caller has intentionally bounded the scan, so
    # we leave the cap as-is.
    BODY_SEARCH_AUTO_CAP = SCAN_BOUNDS["BODY_SEARCH_AUTO_CAP"]
    body_search_capped = False
    if use_body_search and not date_from_explicit and scan_cap > BODY_SEARCH_AUTO_CAP:
        scan_cap = min(scan_cap, BODY_SEARCH_AUTO_CAP)
        body_search_capped = True

    # Hard ceiling: regardless of how base_cap/window_cap/body-cap computed
    # above, never bind more than SEARCH_HARD_CEILING messages in a single
    # `messages 1 thru scan_cap` slice. This is what actually bounds wall
    # time on large cold-cache Exchange accounts — the scaled caps above can
    # still produce values above this floor when offset/limit are large.
    scan_cap = min(scan_cap, SCAN_BOUNDS["SEARCH_HARD_CEILING"])

    # Track whether the mailbox-count cap is active (mailbox="All" path).
    # The AppleScript guard caps at MAX_MAILBOXES_PER_SEARCH; we surface this
    # to callers via a warnings field so they know results may be incomplete on
    # accounts with many labels (e.g. Gmail with 200+ labels).
    mailbox_count_capped = mailbox == "All"

    bounded_candidate_script = f"""
                            set matchingMessages to {{}}
                            set candidateMessages to {{}}
                            set scanUpperBound to {scan_cap}
                            try
                                set candidateMessages to messages 1 thru scanUpperBound of currentMailbox
                            on error
                                try
                                    set candidateMessages to messages of currentMailbox
                                end try
                            end try
    """

    _max_mailboxes_per_search = (
        SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH_ALL"] if mailbox == "All" else SCAN_BOUNDS["MAX_MAILBOXES_PER_SEARCH"]
    )
    if mailboxes:
        # Explicit mailbox list: look up each named folder, degrade gracefully
        # if a name doesn't exist (emits ERROR_MAILBOX instead of hard failure).
        mailbox_lookups = "\n".join(
            f"""                try
                    set end of searchMailboxes to mailbox "{escape_applescript(mb)}" of targetAccount
                on error
                    set end of recordLines to "ERROR_MAILBOX|||{escape_applescript(mb)}|||mailbox not found"
                end try"""
            for mb in mailboxes
        )
        mailbox_script = f"""
                set searchMailboxes to {{}}
{mailbox_lookups}
        """
        skip_script = ""
    elif mailbox == "All":
        mailbox_script = f"""
                set searchMailboxes to every mailbox of targetAccount
                if (count of searchMailboxes) > {_max_mailboxes_per_search} then
                    set searchMailboxes to items 1 thru {_max_mailboxes_per_search} of searchMailboxes
                end if
        """
        skip_script = """
                        set skipFolders to {"Trash", "Junk", "Junk Email", "Deleted Items", "Sent", "Sent Items", "Sent Messages", "Drafts", "Spam", "Deleted Messages"}
                        repeat with skipFolder in skipFolders
                            if mailboxName is skipFolder then
                                set shouldSkip to true
                                exit repeat
                            end if
                        end repeat
        """
    else:
        _mailbox_resolve = build_mailbox_ref(mailbox, account_var="targetAccount", var_name="searchMailbox")
        mailbox_script = f"""
                {_mailbox_resolve}
                set searchMailboxes to {{searchMailbox}}
        """
        skip_script = ""

    date_setup = _build_applescript_date("fromDate", date_from)
    date_setup += _build_applescript_date("toDate", date_to, end_of_day=True)

    escaped_account = escape_applescript(account)
    account_setup = f'''
                set searchAccounts to {{account "{escaped_account}"}}
        '''

    # Build per-message filter block. Avoid broad `every message ... whose`
    # filters because Mail.app can materialize remote mailboxes before applying
    # them. We bind a bounded newest-first slice, then filter in that slice.
    #
    # Date lower bounds need a special fast path: Exchange inboxes can have
    # tens of thousands of messages, and even a bounded 300-message slice is
    # too slow if we read subject/sender/body for every no-hit query. Mail
    # returns mailbox messages newest-first, so once a message is older than
    # fromDate the rest of the slice is outside the requested window.
    early_date_break = "if messageDate < fromDate then exit repeat" if date_from and not date_to else ""
    escaped_body = escape_applescript(body_text) if body_text else ""
    per_msg_conditions: list[str] = []
    if subject_terms:
        subject_checks = " or ".join(f'messageSubject contains "{escape_applescript(t)}"' for t in subject_terms)
        per_msg_conditions.append(f"({subject_checks})")
        candidate_subject_checks = " or ".join(f'subject contains "{escape_applescript(t)}"' for t in subject_terms)
    else:
        candidate_subject_checks = ""
    if sender:
        per_msg_conditions.append(f'messageSender contains "{escaped_sender}"')
    if escaped_sender_exact:
        per_msg_conditions.append(
            f'(messageSender is "{escaped_sender_exact}" or messageSender contains "<{escaped_sender_exact}>")'
        )
    if escaped_sender_domain:
        per_msg_conditions.append(f'messageSender contains "@{escaped_sender_domain}"')
    if escaped_internet_message_id:
        per_msg_conditions.append(
            f'(internetMessageId is "{escaped_internet_message_id}" or internetMessageId is "{escaped_internet_message_id.strip("<>")}")'
        )
    if read_status == "read":
        per_msg_conditions.append("messageRead is true")
    elif read_status == "unread":
        per_msg_conditions.append("messageRead is false")
    if date_from:
        per_msg_conditions.append("messageDate >= fromDate")
    if date_to:
        per_msg_conditions.append("messageDate <= toDate")
    if has_attachments is True:
        per_msg_conditions.append("(count of mail attachments of aMessage) > 0")
    elif has_attachments is False:
        per_msg_conditions.append("(count of mail attachments of aMessage) = 0")
    if use_body_search:
        per_msg_conditions.append(f'msgContent contains "{escaped_body}"')

    if (
        subject_terms
        and not sender
        and not sender_exact
        and not sender_domain
        and not internet_message_id
        and has_attachments is None
        and read_status == "all"
        and not use_body_search
        and not date_to
    ):
        # Fast no-hit/needle path: filter the already-bounded newest slice
        # with the cheapest possible per-message reads. Avoid `whose` here:
        # AppleScript does not reliably apply it to a list of message objects
        # returned by `messages 1 thru N`. The slice is deliberately tiny for
        # default subject lookups, so a subject-only loop is fast and avoids
        # date/sender/read-status/body fetches on large Exchange inboxes.
        message_collection = f"""
                                {bounded_candidate_script}
                            ignoring case
                                repeat with aMessage in candidateMessages
                                    if (count of matchingMessages) >= {scan_cap} then exit repeat
                                    try
                                        set messageSubject to subject of aMessage
                                        if {candidate_subject_checks} then
                                            set end of matchingMessages to aMessage
                                        end if
                                    end try
                                end repeat
                            end ignoring
        """
    elif per_msg_conditions:
        combined_condition = " and ".join(per_msg_conditions)
        content_read_block = (
            """
                                        set msgContent to ""
                                        try
                                            set msgContent to content of aMessage
                                        end try
        """
            if use_body_search
            else ""
        )
        message_collection = f"""
                                {bounded_candidate_script}
                            ignoring case
                                repeat with aMessage in candidateMessages
                                    if (count of matchingMessages) >= {scan_cap} then exit repeat
                                    try
                                        set messageDate to date received of aMessage
                                        {early_date_break}
                                        set messageSubject to subject of aMessage
                                        set messageSender to sender of aMessage
                                        set internetMessageId to ""
                                        try
                                            set internetMessageId to message id of aMessage
                                        end try
                                        set messageRead to read status of aMessage
                                        {content_read_block}
                                        if {combined_condition} then
                                            set end of matchingMessages to aMessage
                                        end if
                                    end try
                                end repeat
                            end ignoring
        """
    else:
        message_collection = f"""
                                {bounded_candidate_script}
                            repeat with aMessage in candidateMessages
                                try
                                    set messageDate to date received of aMessage
                                    {early_date_break}
                                    set end of matchingMessages to aMessage
                                end try
                            end repeat
        """

    # Template the inner AppleScript timeout from the same value the outer
    # run_applescript wrapper will use, minus 10 s so the AS timeout fires
    # before SIGKILL and Mail.app can clean up gracefully. Floor at 30 s to
    # keep the script meaningful on very tight timeouts.
    inner_timeout = max(30, (timeout if timeout is not None else 180) - 10)

    script = f"""
    on sanitize_field(value)
        try
            set valueText to value as string
        on error
            set valueText to ""
        end try

        set AppleScript's text item delimiters to {{return, linefeed, tab}}
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to "|||"
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " | "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to ""
        return valueText
    end sanitize_field

    on pad2(numberValue)
        if numberValue < 10 then
            return "0" & (numberValue as string)
        end if
        return numberValue as string
    end pad2

    on month_number(monthValue)
        set monthValues to {{January, February, March, April, May, June, July, August, September, October, November, December}}
        repeat with monthIndex from 1 to 12
            if item monthIndex of monthValues is monthValue then
                return monthIndex
            end if
        end repeat
        return 0
    end month_number

    on iso_datetime(dateValue)
        set yearValue to year of dateValue as integer
        set monthValue to my month_number(month of dateValue)
        set dayValue to day of dateValue as integer
        set hourValue to hours of dateValue
        set minuteValue to minutes of dateValue
        set secondValue to seconds of dateValue
        return (yearValue as string) & "-" & my pad2(monthValue) & "-" & my pad2(dayValue) & "T" & my pad2(hourValue) & ":" & my pad2(minuteValue) & ":" & my pad2(secondValue)
    end iso_datetime

    tell application "Mail"
        with timeout of {inner_timeout} seconds
            try
                set recordLines to {{}}
                set offsetRemaining to {offset}
                set collectLimit to {collect_limit}
                {date_setup}
                {account_setup}

                repeat with targetAccount in searchAccounts
                    if collectLimit <= 0 then exit repeat
                    set accountName to my sanitize_field(name of targetAccount)
                    {mailbox_script}

                    repeat with currentMailbox in searchMailboxes
                        if collectLimit <= 0 then exit repeat

                        -- NB: do NOT wrap this per-mailbox scan in `with timeout`.
                        -- Materializing `messages 1 thru N` on a 24K+ Exchange
                        -- mailbox routinely exceeds a short timeout; the inner
                        -- candidate-fetch try/catch then swallows the timeout and
                        -- the mailbox silently returns 0 rows. Per-mailbox failures
                        -- are already isolated by the `on error -> ERROR_MAILBOX`
                        -- handler below, and the whole call is bounded by the
                        -- outer call-level timeout budget ({inner_timeout}s).
                        try
                            set mailboxName to my sanitize_field(name of currentMailbox)
                            set shouldSkip to false
                            {skip_script}

                                if not shouldSkip then
                                    {message_collection}
                                    set matchingCount to count of matchingMessages

                                    if offsetRemaining >= matchingCount then
                                        set offsetRemaining to offsetRemaining - matchingCount
                                    else
                                        set startIndex to offsetRemaining + 1
                                        set availableCount to matchingCount - offsetRemaining
                                        if availableCount > collectLimit then
                                            set endIndex to startIndex + collectLimit - 1
                                        else
                                            set endIndex to startIndex + availableCount - 1
                                        end if

                                        if endIndex >= startIndex then
                                            set targetMessages to items startIndex thru endIndex of matchingMessages

                                            repeat with aMessage in targetMessages
                                                try
                                                    set messageId to my sanitize_field(id of aMessage)
                                                    set internetMessageId to ""
                                                    try
                                                        set internetMessageId to my sanitize_field(message id of aMessage)
                                                    end try
                                                    set messageSubject to my sanitize_field(subject of aMessage)
                                                    set messageSender to my sanitize_field(sender of aMessage)
                                                    set messageRead to read status of aMessage
                                                    set messageDate to date received of aMessage
                                                    set receivedAt to my iso_datetime(messageDate)
                                                    set contentPreview to ""
                                                    -- Recipients (to/cc) are intentionally NOT resolved here.
                                                    -- Per-message `to recipients`/`address of` can HANG (not error,
                                                    -- so `on error` cannot catch it) on large remote Exchange/Gmail
                                                    -- mailboxes, blocking the whole bulk scan until timeout. Fetch
                                                    -- recipients per message via get_email_by_id instead (single,
                                                    -- bounded, fast). Emit empty placeholders to keep field alignment.
                                                    set toRecips to ""
                                                    set ccRecips to ""

                                                    if {str(include_content).lower()} then
                                                        try
                                                            set msgContent to content of aMessage
                                                            set AppleScript's text item delimiters to {{return, linefeed, tab}}
                                                            set contentParts to text items of msgContent
                                                            set AppleScript's text item delimiters to " "
                                                            set cleanText to contentParts as string
                                                            set AppleScript's text item delimiters to ""
                                                            if {content_length} > 0 and length of cleanText > {content_length} then
                                                                set contentPreview to my sanitize_field(text 1 thru {content_length} of cleanText & "...")
                                                            else
                                                                set contentPreview to my sanitize_field(cleanText)
                                                            end if
                                                        on error
                                                            set contentPreview to ""
                                                        end try
                                                    end if

                                                    set readValue to "false"
                                                    if messageRead then
                                                        set readValue to "true"
                                                    end if
                                                    {was_replied_fragment(var="aMessage")}

                                                    set recordLine to messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & contentPreview & "|||" & toRecips & "|||" & ccRecips & "|||" & "" & "|||" & "" & "|||" & "" & "|||" & wasRepliedToken
                                                    set end of recordLines to recordLine
                                                    set collectLimit to collectLimit - 1
                                                    if collectLimit <= 0 then exit repeat
                                                end try
                                            end repeat
                                        end if

                                        set offsetRemaining to 0
                                    end if
                                end if
                        on error errMsg
                            -- Emit a structured marker so Python can surface it
                            -- in error_details instead of silently discarding it.
                            set end of recordLines to "ERROR_MAILBOX|||" & (name of currentMailbox) & "|||" & errMsg
                        end try
                    end repeat
                end repeat

                if (count of recordLines) is 0 then
                    return ""
                end if

                set AppleScript's text item delimiters to linefeed
                set outputText to recordLines as string
                set AppleScript's text item delimiters to ""
                return outputText
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    """

    return script, body_search_capped, mailbox_count_capped


def _list_accounts_script() -> str:
    """Tiny AppleScript that returns one account name per line."""
    return """
    tell application "Mail"
        set acctNames to {}
        repeat with anAccount in (every account)
            set end of acctNames to (name of anAccount)
        end repeat
        set AppleScript's text item delimiters to linefeed
        return acctNames as string
    end tell
    """
