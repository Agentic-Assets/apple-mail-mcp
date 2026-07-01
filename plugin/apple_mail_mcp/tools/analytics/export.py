"""``export_emails`` tool: single-email, message-id, and entire-mailbox exports."""

from pathlib import Path

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import iter_id_chunks
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    validate_save_path,
)
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import analytics

_EXPORT_ENTIRE_MAILBOX_DEFAULT = 100
_EXPORT_ENTIRE_MAILBOX_WARN_THRESHOLD = 500


def _build_exact_message_export_script(
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
                                set AppleScript's text item delimiters to "/"
                                set safeSubjectParts to text items of safeSubject
                                set AppleScript's text item delimiters to "-"
                                set safeSubject to safeSubjectParts as string
                                set AppleScript's text item delimiters to ""

                                set exportCount to exportCount + 1
                                set fileName to exportCount & "_" & requestedIdText & "_" & safeSubject & ".{safe_format}"
                                set filePath to exportDir & "/" & fileName

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
            ``TARGET_SELECTOR_DEPRECATED``) or "entire_mailbox"
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
        timeout: Optional AppleScript timeout in seconds. Defaults to 120s.

    Returns:
        Confirmation message with export location
    """

    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: 'account' is required (no DEFAULT_MAIL_ACCOUNT configured)"

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
        max_emails = 1000  # legacy default for future scopes

    save_dir = str(Path(save_directory).expanduser().resolve())

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_mailbox = escape_applescript(mailbox)
    safe_format = escape_applescript(format)
    safe_save_dir = escape_applescript(save_dir)

    if message_ids is not None:
        raw_ids = [str(value).strip() for value in message_ids if str(value).strip()]
        normalized_ids = normalize_message_ids(raw_ids)
        invalid_ids = [value for value in raw_ids if not value.isdigit()]
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"

        chunk_results: list[str] = []
        for chunk in iter_id_chunks(normalized_ids):
            script = _build_exact_message_export_script(
                safe_account=safe_account,
                safe_mailbox=safe_mailbox,
                safe_format=safe_format,
                safe_save_dir=safe_save_dir,
                message_ids=chunk,
            )
            try:
                chunk_results.append(analytics.run_applescript(script, timeout=timeout if timeout is not None else 120))
            except AppleScriptTimeout:
                return f"Error: AppleScript timed out while exporting message_ids for '{account}'"

        if invalid_ids:
            chunk_results.append(f"Ignored invalid message_ids: {', '.join(invalid_ids)}")
        return "\n\n".join(chunk_results)

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

    elif scope == "entire_mailbox":
        script = f'''
        tell application "Mail"
            set outputText to "EXPORTING MAILBOX" & return & return

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
                -- only the first max_emails messages to avoid materializing
                -- the entire mailbox on large Exchange/Gmail accounts.
                set messageCount to count of messages of targetMailbox
                if messageCount > {max_emails} then
                    set mailboxMessages to messages 1 thru {max_emails} of targetMailbox
                else
                    set mailboxMessages to messages of targetMailbox
                end if
                set exportCount to 0

                -- Create export directory
                set exportDir to "{safe_save_dir}/{safe_mailbox}_export"
                do shell script "mkdir -p " & quoted form of exportDir

                repeat with aMessage in mailboxMessages
                    if exportCount >= {max_emails} then exit repeat

                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
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
                set outputText to outputText & "Exported: " & exportCount & return
                if exportCount < messageCount then
                    set outputText to outputText & "(capped at max_emails={max_emails})" & return
                end if
                set outputText to outputText & "Location: " & exportDir & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    else:
        return f"Error: Invalid scope '{scope}'. Use: single_email, entire_mailbox"

    try:
        result = analytics.run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return f"Error: AppleScript timed out while exporting emails for '{account}'"
    if export_warning:
        return export_warning + "\n\n" + result
    return result
