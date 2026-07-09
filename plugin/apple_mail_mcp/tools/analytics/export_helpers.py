"""Shared helpers for bounded email export."""

from collections.abc import Callable

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.bounded_scan import iter_id_chunks
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, normalize_message_ids, run_applescript
from apple_mail_mcp.core.replied import sent_mailbox_resolve_script

SUPPORTED_EXPORT_FORMATS = ("txt", "html")


def normalize_export_format(format_value: str) -> str:
    normalized = (format_value or "").strip().lower()
    if normalized not in SUPPORTED_EXPORT_FORMATS:
        supported = ", ".join(SUPPORTED_EXPORT_FORMATS)
        raise ValueError(f"Invalid format '{format_value}'. Supported: {supported}")
    return normalized


def message_ids_by_mailbox(records: list[dict[str, object]], *, default_mailbox: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for record in records:
        normalized = normalize_message_ids([str(record.get("message_id", "")).strip()])
        if not normalized:
            continue
        mailbox_name = str(record.get("mailbox", "") or default_mailbox).strip() or default_mailbox
        key = (mailbox_name, normalized[0])
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(mailbox_name, []).append(normalized[0])
    return grouped


def unbounded_export_error(account: str) -> str:
    return serialize_tool_error(
        ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message="export_emails refuses filtered export without a bounded date window",
            remediation={
                "preferred": "Pass recent_days=7 or date_from='YYYY-MM-DD'",
                "exact_selector": "message_ids",
                "discovery": "search_emails(..., recent_days=..., limit=...)",
                "account": account,
            },
        )
    )


def _export_content_block(safe_format: str) -> str:
    """AppleScript block formatting the txt/html export body.

    Assumes ``messageSubject``, ``messageSender``, ``messageDate``, and
    ``messageContent`` are already bound in the enclosing scope. Shared by
    every per-message export builder to avoid repeating the format branch.
    """
    return f'''if "{safe_format}" is "txt" then
                    set exportContent to "Subject: " & messageSubject & return
                    set exportContent to exportContent & "From: " & messageSender & return
                    set exportContent to exportContent & "Date: " & (messageDate as string) & return & return
                    set exportContent to exportContent & messageContent
                else if "{safe_format}" is "html" then
                    set exportContent to "<html><body>"
                    set exportContent to exportContent & "<h2>" & messageSubject & "</h2>"
                    set exportContent to exportContent & "<p><strong>From:</strong> " & messageSender & "</p>"
                    set exportContent to exportContent & "<p><strong>Date:</strong> " & (messageDate as string) & "</p>"
                    set exportContent to exportContent & "<hr>" & messageContent
                    set exportContent to exportContent & "</body></html>"
                end if'''


def _sanitize_delimiter_block(var_name: str) -> str:
    """AppleScript block that replaces "/" with "-" in ``var_name`` in place."""
    return f'''set AppleScript's text item delimiters to "/"
                set {var_name}Parts to text items of {var_name}
                set AppleScript's text item delimiters to "-"
                set {var_name} to {var_name}Parts as string
                set AppleScript's text item delimiters to ""'''


def build_exact_message_export_script(
    *,
    safe_account: str,
    safe_mailbox: str,
    safe_format: str,
    safe_save_dir: str,
    message_ids: list[str],
) -> str:
    requested_ids = ", ".join(message_ids)
    return f'''
            tell application "Mail"
                set outputText to "EXPORTING MESSAGES BY ID" & return & return
                set requestedIds to {{{requested_ids}}}
                set exportCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    try
                        set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
                    on error
                        if "{safe_mailbox}" is "INBOX" then
                            set targetMailbox to mailbox "Inbox" of targetAccount
                        else
                            error "Mailbox not found: {safe_mailbox}"
                        end if
                    end try

                    set exportDir to "{safe_save_dir}/message_id_export"
                    do shell script "mkdir -p " & quoted form of exportDir

                    repeat with requestedId in requestedIds
                        set requestedIdText to requestedId as string
                        set matchedMessages to (every message of targetMailbox whose id is requestedId)
                        set foundMessage to missing value
                        if (count of matchedMessages) > 0 then
                            set foundMessage to item 1 of matchedMessages
                        end if

                        if foundMessage is not missing value then
                            try
                                set messageSubject to subject of foundMessage
                                set messageSender to sender of foundMessage
                                set messageDate to date received of foundMessage
                                set messageContent to content of foundMessage

                                set safeSubject to messageSubject
                                {_sanitize_delimiter_block("safeSubject")}

                                set exportCount to exportCount + 1
                                set fileName to exportCount & "_" & requestedIdText & "_" & safeSubject & ".{safe_format}"
                                set filePath to exportDir & "/" & fileName

                                {_export_content_block(safe_format)}

                                set fileRef to open for access POSIX file filePath with write permission
                                set eof of fileRef to 0
                                write exportContent to fileRef as «class utf8»
                                close access fileRef

                                set outputText to outputText & "✓ Exported message_id " & requestedIdText & ": " & messageSubject & return
                            on error exportErr
                                try
                                    close access fileRef
                                end try
                                set outputText to outputText & "Error exporting message_id " & requestedIdText & ": " & exportErr & return
                            end try
                        else
                            set outputText to outputText & "⚠ No email found for message_id " & requestedIdText & return
                        end if
                    end repeat

                    set outputText to outputText & return & "Exported: " & exportCount & return
                    set outputText to outputText & "Location: " & exportDir & return
                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end tell
            '''


def build_entire_mailbox_export_script(
    *,
    safe_account: str,
    safe_mailbox: str,
    safe_format: str,
    safe_save_dir: str,
    max_emails: int,
    offset: int,
    date_setup: str,
    date_filter: str,
) -> str:
    """Bounded ``messages pageStart thru pageEnd`` page-slice export.

    Never binds the full ``messages of targetMailbox``. ``date_filter`` runs
    WITHIN the page window, so out-of-range messages still count against
    the page but are skipped from the on-disk export.
    """
    return f'''
    tell application "Mail"
        set outputText to "EXPORTING MAILBOX" & return & return
        {date_setup}

        try
            set targetAccount to account "{safe_account}"
            -- Try to get mailbox
            try
                set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
            on error
                if "{safe_mailbox}" is "INBOX" then
                    set targetMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {safe_mailbox}"
                end if
            end try

            -- Bind only the requested page window; never the full message list.
            set messageCount to count of messages of targetMailbox
            set pageStart to {offset} + 1
            set pageEnd to {offset} + {max_emails}
            if pageEnd > messageCount then set pageEnd to messageCount
            set exportCount to 0

            -- Create export directory
            set exportDir to "{safe_save_dir}/{safe_mailbox}_export"
            do shell script "mkdir -p " & quoted form of exportDir

            if pageStart <= messageCount and pageStart <= pageEnd then
                set pageMessages to messages pageStart thru pageEnd of targetMailbox

                repeat with aMessage in pageMessages
                    try
                        set messageDate to date received of aMessage
                        set shouldExport to true
                        {date_filter}
                        if shouldExport then
                            set messageSubject to subject of aMessage
                            set messageSender to sender of aMessage
                            set messageContent to content of aMessage

                            -- Create safe filename with index
                            set exportCount to exportCount + 1
                            set fileName to exportCount & "_" & messageSubject & ".{safe_format}"

                            -- Remove unsafe characters
                            {_sanitize_delimiter_block("fileName")}

                            set filePath to exportDir & "/" & fileName

                            {_export_content_block(safe_format)}

                            -- Write to file
                            set fileRef to open for access POSIX file filePath with write permission
                            set eof of fileRef to 0
                            write exportContent to fileRef as «class utf8»
                            close access fileRef
                        end if

                    on error
                        -- Close file handle before continuing to avoid fd leak
                        try
                            close access fileRef
                        end try
                    end try
                end repeat
            end if

            set outputText to outputText & "✓ Mailbox exported successfully!" & return & return
            set outputText to outputText & "Mailbox: {safe_mailbox}" & return
            set outputText to outputText & "Total emails in mailbox: " & messageCount & return
            set outputText to outputText & "Offset: {offset}" & return
            set outputText to outputText & "Exported: " & exportCount & return
            set outputText to outputText & "(bounded page: offset={offset}, max_emails={max_emails})" & return
            set outputText to outputText & "Location: " & exportDir & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''


def build_multi_mailbox_id_export_script(
    *,
    safe_account: str,
    candidate_mailboxes: list[str],
    safe_format: str,
    safe_save_dir: str,
    message_ids: list[str],
) -> str:
    """Export ids across a fixed candidate mailbox list; never opens "All Mail".

    Tries each ``candidate_mailboxes`` name directly (``INBOX`` falls back to
    ``Inbox``), keeps the ones that open, then id-looks-up each requested id
    across them (first match wins) via the accepted ``whose id is`` pattern
    (see ``search/by_id.py``). Gmail reports a thread message's container as
    the virtual, unopenable "All Mail" mailbox, so this never trusts that name.
    """
    requested_ids = ", ".join(message_ids)
    open_blocks: list[str] = []
    for index, name in enumerate(candidate_mailboxes, start=1):
        safe_name = escape_applescript(name)
        var_name = f"mb{index}"
        inbox_fallback = ""
        if name.strip().upper() == "INBOX":
            inbox_fallback = f'''
                    if "{safe_name}" is "INBOX" then
                        try
                            set {var_name} to mailbox "Inbox" of targetAccount
                            set end of openMailboxes to {var_name}
                        end try
                    end if'''
        open_blocks.append(f'''
                try
                    set {var_name} to mailbox "{safe_name}" of targetAccount
                    set end of openMailboxes to {var_name}
                on error{inbox_fallback}
                end try''')
    open_section = "\n".join(open_blocks)

    return f'''
    tell application "Mail"
        set outputText to "" & return
        set requestedIds to {{{requested_ids}}}
        set openMailboxes to {{}}
        set exportCount to 0

        try
            set targetAccount to account "{safe_account}"
            {open_section}

            set exportDir to "{safe_save_dir}/thread_export"
            do shell script "mkdir -p " & quoted form of exportDir

            repeat with requestedId in requestedIds
                set requestedIdText to requestedId as string
                set foundMessage to missing value
                repeat with mb in openMailboxes
                    try
                        set matchedMessages to (every message of mb whose id is requestedId)
                        if (count of matchedMessages) > 0 then
                            set foundMessage to item 1 of matchedMessages
                            exit repeat
                        end if
                    end try
                end repeat

                if foundMessage is not missing value then
                    try
                        set messageSubject to subject of foundMessage
                        set messageSender to sender of foundMessage
                        set messageDate to date received of foundMessage
                        set messageContent to content of foundMessage

                        set safeSubject to messageSubject
                        {_sanitize_delimiter_block("safeSubject")}

                        set exportCount to exportCount + 1
                        set fileName to exportCount & "_" & requestedIdText & "_" & safeSubject & ".{safe_format}"
                        set filePath to exportDir & "/" & fileName

                        {_export_content_block(safe_format)}

                        set fileRef to open for access POSIX file filePath with write permission
                        set eof of fileRef to 0
                        write exportContent to fileRef as «class utf8»
                        close access fileRef

                        set outputText to outputText & "✓ Exported message_id " & requestedIdText & ": " & messageSubject & return
                    on error exportErr
                        try
                            close access fileRef
                        end try
                        set outputText to outputText & "Error exporting message_id " & requestedIdText & ": " & exportErr & return
                    end try
                else
                    set outputText to outputText & "⚠ No email found for message_id " & requestedIdText & return
                end if
            end repeat

            set outputText to outputText & return & "Exported: " & exportCount & return
            set outputText to outputText & "Location: " & exportDir & return
        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''


def run_multi_mailbox_id_export(
    *,
    account: str,
    safe_account: str,
    candidate_mailboxes: list[str],
    safe_format: str,
    safe_save_dir: str,
    message_ids: list[str],
    timeout: int | None,
    runner: Callable[[str, int | None], str] = run_applescript,
) -> str:
    """Chunked wrapper around ``build_multi_mailbox_id_export_script``.

    Mirrors :func:`run_message_id_export`, scanning a fixed candidate
    mailbox list per id instead of a caller-supplied "All Mail" mailbox.
    """
    normalized_ids = normalize_message_ids(message_ids)
    if not normalized_ids:
        return "Error: message ids must contain one or more numeric Mail ids"

    chunk_results: list[str] = []
    for chunk in iter_id_chunks(normalized_ids):
        script = build_multi_mailbox_id_export_script(
            safe_account=safe_account,
            candidate_mailboxes=candidate_mailboxes,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            message_ids=chunk,
        )
        try:
            chunk_results.append(runner(script, timeout if timeout is not None else 120))
        except AppleScriptTimeout:
            return f"Error: AppleScript timed out while exporting message_ids for '{account}'"

    return "\n\n".join(chunk_results)


def build_correspondent_export_script(
    *,
    safe_account: str,
    safe_email_address: str,
    safe_format: str,
    safe_save_dir: str,
    safe_mailbox: str,
    scan_upper_bound: int,
    max_emails: int,
    offset: int,
    include_sent: bool,
    date_setup: str,
    date_filter: str,
) -> str:
    sent_resolve = sent_mailbox_resolve_script("sentMailbox", "targetAccount")
    sent_append = ""
    if include_sent:
        sent_append = """
                    if sentMailbox is not missing value then set end of searchMailboxes to sentMailbox
        """
    return f'''
        using terms from application "Mail"
            on messageHasCorrespondent(aMessage, emailNeedle)
                ignoring case
                    try
                        if sender of aMessage contains emailNeedle then return true
                    end try
                    try
                        repeat with aRecipient in recipients of aMessage
                            if address of aRecipient contains emailNeedle then return true
                        end repeat
                    end try
                    try
                        repeat with aRecipient in to recipients of aMessage
                            if address of aRecipient contains emailNeedle then return true
                        end repeat
                    end try
                    try
                        repeat with aRecipient in cc recipients of aMessage
                            if address of aRecipient contains emailNeedle then return true
                        end repeat
                    end try
                    try
                        repeat with aRecipient in bcc recipients of aMessage
                            if address of aRecipient contains emailNeedle then return true
                        end repeat
                    end try
                end ignoring
                return false
            end messageHasCorrespondent
        end using terms from

        tell application "Mail"
            set outputText to "EXPORTING CORRESPONDENT" & return & return
            {date_setup}

            try
                set targetAccount to account "{safe_account}"
                try
                    set targetMailbox to mailbox "{safe_mailbox}" of targetAccount
                on error
                    if "{safe_mailbox}" is "INBOX" then
                        set targetMailbox to mailbox "Inbox" of targetAccount
                    else
                        error "Mailbox not found: {safe_mailbox}"
                    end if
                end try
                {sent_resolve}
                set searchMailboxes to {{targetMailbox}}
                {sent_append}

                set exportDir to "{safe_save_dir}/correspondent_export"
                do shell script "mkdir -p " & quoted form of exportDir
                set totalExportCount to 0
                set globalMatchedCount to 0

                repeat with currentMailbox in searchMailboxes
                    if totalExportCount >= {max_emails} then exit repeat
                    set mailboxName to name of currentMailbox
                    set messageCount to count of messages of currentMailbox
                    if messageCount > {scan_upper_bound} then
                        set mailboxMessages to messages 1 thru {scan_upper_bound} of currentMailbox
                    else
                        set mailboxMessages to messages of currentMailbox
                    end if
                    set mailboxExportCount to 0

                    repeat with aMessage in mailboxMessages
                        if totalExportCount >= {max_emails} then exit repeat
                        try
                            set messageDate to date received of aMessage
                            set shouldExport to my messageHasCorrespondent(aMessage, "{safe_email_address}")
                            {date_filter}
                            if shouldExport then
                                set globalMatchedCount to globalMatchedCount + 1
                                if globalMatchedCount > {offset} then
                                    set messageSubject to subject of aMessage
                                    set messageSender to sender of aMessage
                                    set messageContent to content of aMessage

                                    set mailboxExportCount to mailboxExportCount + 1
                                    set totalExportCount to totalExportCount + 1
                                    set fileName to totalExportCount & "_" & mailboxName & "_" & messageSubject & ".{safe_format}"
                                    {_sanitize_delimiter_block("fileName")}
                                    set filePath to exportDir & "/" & fileName

                                    if "{safe_format}" is "txt" then
                                        set exportContent to "Subject: " & messageSubject & return
                                        set exportContent to exportContent & "From: " & messageSender & return
                                        set exportContent to exportContent & "Mailbox: " & mailboxName & return
                                        set exportContent to exportContent & "Date: " & (messageDate as string) & return & return
                                        set exportContent to exportContent & messageContent
                                    else if "{safe_format}" is "html" then
                                        set exportContent to "<html><body>"
                                        set exportContent to exportContent & "<h2>" & messageSubject & "</h2>"
                                        set exportContent to exportContent & "<p><strong>From:</strong> " & messageSender & "</p>"
                                        set exportContent to exportContent & "<p><strong>Mailbox:</strong> " & mailboxName & "</p>"
                                        set exportContent to exportContent & "<p><strong>Date:</strong> " & (messageDate as string) & "</p>"
                                        set exportContent to exportContent & "<hr>" & messageContent
                                        set exportContent to exportContent & "</body></html>"
                                    end if

                                    set fileRef to open for access POSIX file filePath with write permission
                                    set eof of fileRef to 0
                                    write exportContent to fileRef as «class utf8»
                                    close access fileRef
                                end if
                            end if
                        on error
                            try
                                close access fileRef
                            end try
                        end try
                    end repeat

                    set outputText to outputText & mailboxName & ": exported " & mailboxExportCount & return
                end repeat

                set outputText to outputText & return & "Email address: {safe_email_address}" & return
                set outputText to outputText & "Exported: " & totalExportCount & return
                set outputText to outputText & "Location: " & exportDir & return
            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''


def run_message_id_export(
    *,
    account: str,
    safe_account: str,
    safe_format: str,
    safe_save_dir: str,
    ids_by_mailbox: dict[str, list[str]],
    timeout: int | None,
    runner: Callable[[str, int | None], str] = run_applescript,
) -> str:
    chunk_results: list[str] = []
    invalid_ids: list[str] = []
    for mailbox_name, ids in ids_by_mailbox.items():
        raw_ids = [str(value).strip() for value in ids if str(value).strip()]
        normalized_ids = normalize_message_ids(raw_ids)
        invalid_ids.extend(value for value in raw_ids if not value.isdigit())
        if not normalized_ids:
            continue
        safe_mailbox = escape_applescript(mailbox_name)
        for chunk in iter_id_chunks(normalized_ids):
            script = build_exact_message_export_script(
                safe_account=safe_account,
                safe_mailbox=safe_mailbox,
                safe_format=safe_format,
                safe_save_dir=safe_save_dir,
                message_ids=chunk,
            )
            try:
                chunk_results.append(runner(script, timeout if timeout is not None else 120))
            except AppleScriptTimeout:
                return f"Error: AppleScript timed out while exporting message_ids for '{account}'"

    if invalid_ids and chunk_results:
        chunk_results.append(f"Ignored invalid message_ids: {', '.join(invalid_ids)}")
    if not chunk_results:
        return "Error: 'message_ids' must contain one or more numeric Mail ids"
    return "\n\n".join(chunk_results)
