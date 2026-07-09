"""``export_emails`` tool: single-email, message-id, and entire-mailbox exports."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import target_selector_deprecated_error
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    validate_save_path,
)
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp

_EXPORT_ENTIRE_MAILBOX_DEFAULT = 100
_EXPORT_ENTIRE_MAILBOX_WARN_THRESHOLD = 500


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def export_emails(
    account: str | None = None,
    scope: str = "entire_mailbox",
    subject_keyword: str | None = None,
    message_id: str | None = None,
    message_ids: list[str] | None = None,
    mailbox: str = "INBOX",
    save_directory: str = "~/Desktop",
    format: str = "txt",
    max_emails: int | None = None,
    offset: int = 0,
    sort: str = "newest_first",
    sender_exact: str | None = None,
    sender_domain: str | None = None,
    email_address: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    recent_days: float = 2.0,
    include_sent: bool = True,
    timeout: int | None = None,
) -> str:
    """
    Export emails to files for backup or analysis.

    For ``entire_mailbox`` exports, the AppleScript binds only the first
    ``max_emails`` messages (``items 1 thru max_emails``) so the full message
    list of a 24K-message Exchange mailbox is never materialized.

    **Exchange / Gmail cold-cache warning:** ``entire_mailbox`` reads
    ``content of aMessage`` for every exported message. On an Exchange account
    that has not recently synced, each body read can take 1–3 seconds — 100
    emails is already 2–5 minutes of wall time. For larger metadata-only walks
    use ``full_inbox_export`` instead, which skips body reads entirely.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Falls back to
            ``DEFAULT_MAIL_ACCOUNT`` when None.
        scope: Export scope: "single_email" (requires ``message_id`` or
            ``message_ids``; ``subject_keyword`` path returns
            ``TARGET_SELECTOR_DEPRECATED``), "filtered", "correspondent",
            "thread", or "entire_mailbox"
        subject_keyword: Deprecated schema-compat selector for single_email scope.
            Returns ``TARGET_SELECTOR_DEPRECATED`` when ``message_id`` is omitted.
        message_id: Exact numeric Apple Mail message id for single_email scope
        message_ids: Optional list of exact Apple Mail message ids to export
        mailbox: Mailbox to export from (default: "INBOX")
        save_directory: Directory to save exports (default: "~/Desktop")
        format: Export format: "txt", "html" (default: "txt")
        max_emails: Maximum number of emails to export for entire_mailbox.
            Defaults to 100 for entire_mailbox scope. Values above 500 are
            accepted but will emit a performance warning — each message
            requires a body read from Mail which is expensive on Exchange
            cold-cache accounts. For large exports prefer ``full_inbox_export``.
        offset: Pagination offset for bounded filtered or entire_mailbox exports.
        sort: Export ordering. Use "newest_first" or "date_desc".
        sender_exact: Exact sender-address discovery filter for filtered scope.
        sender_domain: Sender-domain discovery filter for filtered scope.
        email_address: Address to match across sender and recipient fields for
            correspondent scope. Includes received and sent messages by default.
        date_from: Optional YYYY-MM-DD lower date bound.
        date_to: Optional YYYY-MM-DD upper date bound.
        recent_days: Bounded discovery window for filtered and thread exports.
        include_sent: Include Sent in thread exports when true.
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.

    Returns:
        Confirmation message with export location
    """

    from apple_mail_mcp.tools import analytics
    from apple_mail_mcp.tools.analytics.export_helpers import (
        build_correspondent_export_script,
        message_ids_by_mailbox,
        normalize_export_format,
        run_message_id_export,
        unbounded_export_error,
    )

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

    try:
        normalized_format = normalize_export_format(format)
    except ValueError as exc:
        return f"Error: {exc}"
    if offset < 0:
        return "Error: offset must be >= 0"
    if max_emails is not None and max_emails <= 0:
        return "Error: max_emails must be > 0"
    if sort not in {"newest_first", "date_desc"}:
        return "Error: Invalid sort. Use: newest_first or date_desc"

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = analytics.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    path_err = validate_save_path(save_directory)
    if path_err:
        return path_err

    # Apply scope-specific max_emails default and emit a performance warning
    # when the caller requests an unusually large body-read export.
    export_warning: str | None = None
    if scope == "entire_mailbox":
        if max_emails is None:
            max_emails = _EXPORT_ENTIRE_MAILBOX_DEFAULT
        elif max_emails > _EXPORT_ENTIRE_MAILBOX_WARN_THRESHOLD:
            export_warning = (
                f"⚠ Performance warning: max_emails={max_emails} will read the body of "
                f"{max_emails} messages from Mail.app. On an Exchange or Gmail account "
                "with a cold cache each body read can take 1–3 seconds, making this "
                f"export potentially {max_emails * 2 // 60}–{max_emails * 3 // 60} minutes long. "
                "For metadata-only walks over large mailboxes, use full_inbox_export instead."
            )
    elif max_emails is None:
        max_emails = 25

    save_dir = str(Path(save_directory).expanduser().resolve())

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_mailbox = escape_applescript(mailbox)
    safe_format = escape_applescript(normalized_format)
    safe_save_dir = escape_applescript(save_dir)

    if message_ids is not None:
        return run_message_id_export(
            account=account,
            safe_account=safe_account,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            ids_by_mailbox={mailbox: message_ids},
            timeout=timeout,
            runner=analytics.run_applescript,
        )

    if scope == "single_email":
        if not message_id and not subject_keyword:
            return (
                "Error: message_id is required for single_email scope "
                "(discover via search_emails(...) or list_inbox_emails(...), then pass message_id)"
            )
        if not message_id and subject_keyword:
            return target_selector_deprecated_error(
                "export_emails",
                ("subject_keyword",),
                preferred="Call search_emails(...) first, then pass message_id for scope='single_email'.",
                discovery="search_emails(subject_keyword=..., recent_days=..., limit=...)",
                exact_selector="message_id",
            )

        normalized_ids = normalize_message_ids([message_id])
        if not normalized_ids:
            return "Error: message_id must be a numeric Apple Mail message id"
        target_message_id = normalized_ids[0]
        safe_not_found_label = escape_applescript(f"message_id={target_message_id}")

        script = f'''
        tell application "Mail"
            set outputText to "EXPORTING EMAIL" & return & return

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

                -- Export by exact Mail message id (no subject scan).
                set matchedMessages to (every message of targetMailbox whose id is {target_message_id})
                set foundMessage to missing value
                if (count of matchedMessages) > 0 then
                    set foundMessage to item 1 of matchedMessages
                end if

                if foundMessage is not missing value then
                    set messageSubject to subject of foundMessage
                    set messageSender to sender of foundMessage
                    set messageDate to date received of foundMessage
                    set messageContent to content of foundMessage

                    -- Create safe filename
                    set safeSubject to messageSubject
                    set AppleScript's text item delimiters to "/"
                    set safeSubjectParts to text items of safeSubject
                    set AppleScript's text item delimiters to "-"
                    set safeSubject to safeSubjectParts as string
                    set AppleScript's text item delimiters to ""

                    set fileName to safeSubject & ".{safe_format}"
                    set filePath to "{safe_save_dir}/" & fileName

                    -- Prepare export content
                    if "{safe_format}" is "txt" then
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
                    end if

                    -- Write to file
                    set fileRef to open for access POSIX file filePath with write permission
                    set eof of fileRef to 0
                    write exportContent to fileRef as «class utf8»
                    close access fileRef

                    set outputText to outputText & "✓ Email exported successfully!" & return & return
                    set outputText to outputText & "Subject: " & messageSubject & return
                    set outputText to outputText & "Saved to: " & filePath & return

                else
                    set outputText to outputText & "⚠ No email found matching: {safe_not_found_label}" & return
                end if

            on error errMsg
                try
                    close access file filePath
                end try
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif scope == "filtered":
        if not any((sender_exact, sender_domain, date_from, date_to)):
            return "Error: scope='filtered' requires sender_exact, sender_domain, date_from, or date_to"
        if not date_from and recent_days <= 0:
            return unbounded_export_error(account)

        from apple_mail_mcp.tools.search import _search_mail_records_sync

        try:
            records = _search_mail_records_sync(
                account=account,
                mailbox=mailbox,
                sender_exact=sender_exact,
                sender_domain=sender_domain,
                date_from=date_from,
                date_to=date_to,
                include_content=False,
                content_length=0,
                offset=offset,
                limit=max_emails,
                timeout=timeout,
                recent_days=recent_days,
                date_from_explicit=date_from is not None,
            )
        except AppleScriptTimeout:
            return f"Error: AppleScript timed out while discovering filtered emails for '{account}'"
        except ValueError as exc:
            return f"Error: {exc}"
        if not records:
            return "No emails found for filtered export"

        result = run_message_id_export(
            account=account,
            safe_account=safe_account,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            ids_by_mailbox=message_ids_by_mailbox(records, default_mailbox=mailbox),
            timeout=timeout,
            runner=analytics.run_applescript,
        )
        return "FILTERED EXPORT\n\n" + result

    elif scope == "correspondent":
        if not email_address:
            return "Error: email_address is required for correspondent scope"
        effective_date_from = date_from
        if not effective_date_from:
            if recent_days <= 0:
                return unbounded_export_error(account)
            cutoff = datetime.now() - timedelta(days=recent_days)
            effective_date_from = cutoff.strftime("%Y-%m-%d")

        from apple_mail_mcp.tools.search.records import _build_applescript_date

        try:
            date_setup = _build_applescript_date("fromDate", effective_date_from) + _build_applescript_date(
                "toDate", date_to, end_of_day=True
            )
        except ValueError as exc:
            return f"Error: {exc}"
        date_filter = """
                            if messageDate < fromDate then set shouldExport to false
        """
        if date_to:
            date_filter += """
                            if messageDate > toDate then set shouldExport to false
            """
        script = build_correspondent_export_script(
            safe_account=safe_account,
            safe_email_address=escape_applescript(email_address),
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            safe_mailbox=safe_mailbox,
            scan_upper_bound=max_emails + offset,
            max_emails=max_emails,
            offset=offset,
            include_sent=include_sent,
            date_setup=date_setup,
            date_filter=date_filter,
        )

    elif scope == "thread":
        if not message_id:
            return "Error: message_id is required for thread scope"
        if recent_days <= 0:
            return unbounded_export_error(account)

        import json

        from apple_mail_mcp.tools.search import get_email_thread

        thread_mailboxes: list[str] | None = None
        if include_sent and mailbox.lower() != "all":
            thread_mailboxes = [mailbox]
            if all(candidate.lower() != "sent" for candidate in thread_mailboxes):
                thread_mailboxes.append("Sent")
        thread_result = get_email_thread(
            account=account,
            message_id=message_id,
            mailbox=mailbox,
            mailboxes=thread_mailboxes,
            max_messages=max_emails,
            recent_days=recent_days,
            include_preview=False,
            output_format="json",
            timeout=timeout,
        )
        if thread_result.startswith("Error:"):
            return thread_result
        try:
            thread_payload = json.loads(thread_result)
        except json.JSONDecodeError:
            return "Error: get_email_thread returned invalid JSON during thread export"
        records = thread_payload.get("items", [])
        if not isinstance(records, list) or not records:
            return "No emails found for thread export"
        thread_records = cast(list[dict[str, object]], records)

        result = run_message_id_export(
            account=account,
            safe_account=safe_account,
            safe_format=safe_format,
            safe_save_dir=safe_save_dir,
            ids_by_mailbox=message_ids_by_mailbox(thread_records, default_mailbox=mailbox),
            timeout=timeout,
            runner=analytics.run_applescript,
        )
        return "THREAD EXPORT\n\n" + result

    elif scope == "entire_mailbox":
        from apple_mail_mcp.tools.search.records import _build_applescript_date

        try:
            date_setup = _build_applescript_date("fromDate", date_from) + _build_applescript_date(
                "toDate", date_to, end_of_day=True
            )
        except ValueError as exc:
            return f"Error: {exc}"
        date_filter = ""
        if date_from:
            date_filter += """
                            if messageDate < fromDate then set shouldExport to false
            """
        if date_to:
            date_filter += """
                            if messageDate > toDate then set shouldExport to false
            """
        scan_upper_bound = max_emails + offset
        script = f'''
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

                -- Use Mail's count API for the headline total, then bind
                -- only the requested page window to avoid materializing
                -- the entire mailbox on large Exchange/Gmail accounts.
                set messageCount to count of messages of targetMailbox
                if messageCount > {scan_upper_bound} then
                    set mailboxMessages to messages 1 thru {scan_upper_bound} of targetMailbox
                else
                    set mailboxMessages to messages of targetMailbox
                end if
                set exportCount to 0
                set seenCount to 0

                -- Create export directory
                set exportDir to "{safe_save_dir}/{safe_mailbox}_export"
                do shell script "mkdir -p " & quoted form of exportDir

                repeat with aMessage in mailboxMessages
                    if exportCount >= {max_emails} then exit repeat
                    set seenCount to seenCount + 1

                    try
                        set messageDate to date received of aMessage
                        set shouldExport to true
                        if seenCount <= {offset} then set shouldExport to false
                        {date_filter}
                        if shouldExport then
                            set messageSubject to subject of aMessage
                            set messageSender to sender of aMessage
                            set messageContent to content of aMessage

                            -- Create safe filename with index
                            set exportCount to exportCount + 1
                            set fileName to exportCount & "_" & messageSubject & ".{safe_format}"

                            -- Remove unsafe characters
                            set AppleScript's text item delimiters to "/"
                            set fileNameParts to text items of fileName
                            set AppleScript's text item delimiters to "-"
                            set fileName to fileNameParts as string
                            set AppleScript's text item delimiters to ""

                            set filePath to exportDir & "/" & fileName

                            -- Prepare export content
                            if "{safe_format}" is "txt" then
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
                            end if

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

                set outputText to outputText & "✓ Mailbox exported successfully!" & return & return
                set outputText to outputText & "Mailbox: {safe_mailbox}" & return
                set outputText to outputText & "Total emails in mailbox: " & messageCount & return
                set outputText to outputText & "Offset: {offset}" & return
                set outputText to outputText & "Exported: " & exportCount & return
                if exportCount < messageCount then
                    set outputText to outputText & "(bounded page: offset={offset}, max_emails={max_emails})" & return
                end if
                set outputText to outputText & "Location: " & exportDir & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    else:
        return f"Error: Invalid scope '{scope}'. Use: single_email, filtered, correspondent, thread, entire_mailbox"

    try:
        result = analytics.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return f"Error: AppleScript timed out while exporting emails for '{account}'"
    if export_warning:
        return export_warning + "\n\n" + result
    return result
