"""AppleScript fragments shared by bounded email-export builders."""

from apple_mail_mcp.core.script_fragments import build_mailbox_ref

SUPPORTED_EXPORT_FORMATS = ("txt", "html", "eml")


def mailbox_lookup_block(
    mailbox: str,
    *,
    account_var: str = "targetAccount",
    var_name: str = "targetMailbox",
) -> str:
    """Resolve an unescaped mailbox through the shared canonical resolver.

    This preserves special-folder and localized INBOX fallbacks while also
    accepting documented ``Parent/Child`` paths. Escaping happens exactly once
    in :func:`build_mailbox_ref`.
    """
    return build_mailbox_ref(mailbox, account_var=account_var, var_name=var_name)


def normalize_export_format(format_value: str) -> str:
    """Return an allowlisted export format or raise an actionable error."""
    normalized = (format_value or "").strip().lower()
    if normalized not in SUPPORTED_EXPORT_FORMATS:
        supported = ", ".join(SUPPORTED_EXPORT_FORMATS)
        raise ValueError(f"Invalid format '{format_value}'. Supported: {supported}")
    return normalized


def export_content_block(safe_format: str, *, include_mailbox: bool = False) -> str:
    """Return the format-specific content assignment for an export message.

    The ``eml`` path deliberately uses Mail's raw ``source`` property rather
    than synthesizing headers from a few visible fields. That preserves the
    RFC 822 header block and MIME structure for downstream mail clients.
    """
    mailbox_text = 'set exportContent to exportContent & "Mailbox: " & mailboxName & return' if include_mailbox else ""
    mailbox_html = (
        'set exportContent to exportContent & "<p><strong>Mailbox:</strong> " & mailboxName & "</p>"'
        if include_mailbox
        else ""
    )
    return f'''if "{safe_format}" is "txt" then
                    set exportContent to "Subject: " & messageSubject & return
                    set exportContent to exportContent & "From: " & messageSender & return
                    {mailbox_text}
                    set exportContent to exportContent & "Date: " & (messageDate as string) & return & return
                    set exportContent to exportContent & messageContent
                else if "{safe_format}" is "html" then
                    set exportContent to "<html><body>"
                    set exportContent to exportContent & "<h2>" & messageSubject & "</h2>"
                    set exportContent to exportContent & "<p><strong>From:</strong> " & messageSender & "</p>"
                    {mailbox_html}
                    set exportContent to exportContent & "<p><strong>Date:</strong> " & (messageDate as string) & "</p>"
                    set exportContent to exportContent & "<hr>" & messageContent
                    set exportContent to exportContent & "</body></html>"
                else if "{safe_format}" is "eml" then
                    set exportContent to source of aMessage
                end if'''


def sanitize_delimiter_block(var_name: str) -> str:
    """Return an AppleScript fragment replacing slashes in a filename."""
    return f'''set AppleScript's text item delimiters to "/"
                set {var_name}Parts to text items of {var_name}
                set AppleScript's text item delimiters to "-"
                set {var_name} to {var_name}Parts as string
                set AppleScript's text item delimiters to ""'''


def deterministic_newest_first_block() -> str:
    """Sort a bounded Mail page by received date, then numeric message id.

    Mail's list ordering is not an API contract. The insertion sort operates
    only on the already-bounded page (at most 50 messages), avoiding a full
    mailbox materialization while giving equal-date records a stable tie-break.
    """
    return """-- Deterministic newest-first order within the bounded page.
                set orderedPageMessages to {}
                repeat with candidateMessage in pageMessages
                    set candidateDate to date received of candidateMessage
                    set candidateId to id of candidateMessage
                    set insertionIndex to (count of orderedPageMessages) + 1
                    if (count of orderedPageMessages) > 0 then
                        repeat with sortedIndex from 1 to (count of orderedPageMessages)
                            set existingMessage to item sortedIndex of orderedPageMessages
                            set existingDate to date received of existingMessage
                            set existingId to id of existingMessage
                            if candidateDate > existingDate or (candidateDate = existingDate and candidateId > existingId) then
                                set insertionIndex to sortedIndex
                                exit repeat
                            end if
                        end repeat
                    end if
                    if insertionIndex > (count of orderedPageMessages) then
                        set end of orderedPageMessages to candidateMessage
                    else
                        set item insertionIndex of orderedPageMessages to candidateMessage
                    end if
                end repeat
                set pageMessages to orderedPageMessages"""


def attachment_bundle_setup_block(*, include_attachments: bool) -> str:
    """Return per-message EML bundle setup when attachment extraction is on.

    The caller has already validated that this is an EML export. The resulting
    layout is ``{index}_{subject}/message.eml`` with an ``attachments`` folder.
    """
    if not include_attachments:
        return ""
    return f'''set bundleName to exportCount & "_" & messageSubject
                {sanitize_delimiter_block("bundleName")}
                set bundleDir to exportDir & "/" & bundleName
                set attachmentDir to bundleDir & "/attachments"
                do shell script "mkdir -p " & quoted form of attachmentDir
                set filePath to bundleDir & "/message.eml"'''


def attachment_bundle_save_block(
    *, include_attachments: bool, max_attachment_bytes: int, max_total_attachment_bytes: int
) -> str:
    """Return a size- and total-capped Mail attachment extraction fragment."""
    if not include_attachments:
        return ""
    return f"""set savedAttachmentCount to 0
                set skippedAttachmentCount to 0
                set messageAttachments to mail attachments of aMessage
                set attachmentCount to count of messageAttachments
                repeat with attachmentIndex from 1 to attachmentCount
                    try
                        set anAttachment to item attachmentIndex of messageAttachments
                        set attachmentSize to file size of anAttachment as integer
                        if attachmentSize >= 0 and attachmentSize <= {max_attachment_bytes} and (exportAttachmentBytes + attachmentSize) <= {max_total_attachment_bytes} then
                            set attachmentFileName to attachmentIndex & "_" & name of anAttachment
                            {sanitize_delimiter_block("attachmentFileName")}
                            save anAttachment in POSIX file (attachmentDir & "/" & attachmentFileName)
                            set exportAttachmentBytes to exportAttachmentBytes + attachmentSize
                            set savedAttachmentCount to savedAttachmentCount + 1
                        else
                            set skippedAttachmentCount to skippedAttachmentCount + 1
                        end if
                    on error
                        set skippedAttachmentCount to skippedAttachmentCount + 1
                    end try
                end repeat
                set outputText to outputText & "Attachments saved: " & savedAttachmentCount & "; skipped: " & skippedAttachmentCount & return"""
