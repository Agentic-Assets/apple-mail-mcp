"""Reusable AppleScript fragment builders (NOT full scripts — no leading ``tell application "Mail"``).

Inbox-name localization, mailbox refs, field/content/date snippets shared across tool surfaces.
"""

from apple_mail_mcp.core.escaping import escape_applescript

# ---------------------------------------------------------------------------
# Shared AppleScript template helpers
# ---------------------------------------------------------------------------

# Localized inbox mailbox names. Mail.app uses the system locale to name
# the inbox folder for non-IMAP accounts (Exchange, on-my-Mac), so we must
# try multiple names to find it. IMAP accounts (iCloud, Gmail) typically
# expose 'INBOX' regardless of system language.
INBOX_NAMES = [
    "INBOX",  # IMAP standard (iCloud, Gmail, Fastmail)
    "Inbox",  # English non-IMAP
    "Boîte de réception",  # French (Exchange/Outlook on FR system)
    "Boîte aux lettres",  # French alt
    "Réception",  # French alt
    "Posteingang",  # German
    "Bandeja de entrada",  # Spanish
    "Posta in arrivo",  # Italian
    "Caixa de entrada",  # Portuguese
    "Postvak IN",  # Dutch
    "受信トレイ",  # Japanese
]

# Some providers display Sent as a top-level mailbox (for example, "Sent
# Mail") but do not resolve that display name through ``mailbox <name> of
# account``.
SENT_MAILBOX_NAMES = {
    "sent",
    "sent items",
    "sent mail",
    "sent messages",
}


def inbox_mailbox_script(var_name: str = "inboxMailbox", account_var: str = "anAccount") -> str:
    """Return AppleScript snippet to resolve the inbox mailbox.

    Iterates through INBOX_NAMES (localized variants) so non-English
    Mail.app accounts — typically Exchange on a French/German/etc.
    system where the inbox is 'Boîte de réception' / 'Posteingang' —
    still resolve correctly.
    """
    name_list = ", ".join(f'"{n}"' for n in INBOX_NAMES)
    return f"""
                set {var_name} to missing value
                repeat with __inboxLookupName in {{{name_list}}}
                    try
                        set {var_name} to mailbox (__inboxLookupName as string) of {account_var}
                        exit repeat
                    end try
                end repeat
                if {var_name} is missing value then
                    error "No inbox mailbox found for account " & (name of {account_var})
                end if"""


def content_preview_script(max_length: int, output_var: str = "outputText") -> str:
    """Return AppleScript snippet to extract and truncate email content preview."""
    return f"""
                            try
                                set msgContent to content of aMessage
                                set AppleScript's text item delimiters to {{return, linefeed}}
                                set contentParts to text items of msgContent
                                set AppleScript's text item delimiters to " "
                                set cleanText to contentParts as string
                                set AppleScript's text item delimiters to ""

                                if length of cleanText > {max_length} then
                                    set contentPreview to text 1 thru {max_length} of cleanText & "..."
                                else
                                    set contentPreview to cleanText
                                end if

                                set {output_var} to {output_var} & "   Content: " & contentPreview & return
                            on error
                                set {output_var} to {output_var} & "   Content: [Not available]" & return
                            end try"""


def date_cutoff_script(days_back: int, var_name: str = "cutoffDate") -> str:
    """Return AppleScript snippet to set a date cutoff variable."""
    if days_back <= 0:
        return ""
    return f"""
            set {var_name} to (current date) - ({days_back} * days)"""


def skip_folders_condition(var_name: str = "mailboxName") -> str:
    """Return AppleScript condition to skip system folders (Trash, Junk, etc)."""
    from apple_mail_mcp.constants import SKIP_FOLDERS

    folder_list = ", ".join(f'"{f}"' for f in SKIP_FOLDERS)
    return f"{var_name} is not in {{{folder_list}}}"


def build_mailbox_ref(
    mailbox: str,
    account_var: str = "targetAccount",
    var_name: str = "targetMailbox",
) -> str:
    """Return AppleScript snippet to resolve a mailbox by name with system-folder fallbacks.

    Handles:
    - Normal mailbox names (e.g. "Archive")
    - INBOX / Inbox case variation
    - Sent mailbox display names via account-scoped top-level enumeration
    - Nested mailbox paths using "/" separator (e.g. "Projects/2024")

    The resulting variable *var_name* will hold the resolved mailbox reference.
    """
    escaped = escape_applescript(mailbox)
    parts = mailbox.split("/")

    if len(parts) > 1:
        # Build nested mailbox reference: mailbox "Child" of mailbox "Parent" of account
        ref = f'mailbox "{escape_applescript(parts[-1])}" of '
        for i in range(len(parts) - 2, -1, -1):
            ref += f'mailbox "{escape_applescript(parts[i])}" of '
        ref += account_var
        return f"set {var_name} to {ref}"

    # When caller asks for "INBOX" (default for most tools), iterate the
    # localized fallback list so Exchange/non-English inboxes are found.
    if mailbox.upper() == "INBOX":
        name_list = ", ".join(f'"{n}"' for n in INBOX_NAMES)
        return f"""set {var_name} to missing value
            repeat with __mailboxLookupName in {{{name_list}}}
                try
                    set {var_name} to mailbox (__mailboxLookupName as string) of {account_var}
                    exit repeat
                end try
            end repeat
            if {var_name} is missing value then
                error "Mailbox not found: {escaped} (no localized inbox match)"
            end if"""

    # Some provider-specific Sent folders are returned by ``every mailbox``
    # but reject a direct named lookup. Try the requested name first so a
    # user-created mailbox with one of these names still wins, then match the
    # exact display name within the requested account only.
    if mailbox.casefold() in SENT_MAILBOX_NAMES:
        return f'''set {var_name} to missing value
            try
                set {var_name} to mailbox "{escaped}" of {account_var}
            on error
                try
                    repeat with __sentMailboxCandidate in every mailbox of {account_var}
                        if (name of __sentMailboxCandidate as string) is "{escaped}" then
                            set {var_name} to __sentMailboxCandidate
                            exit repeat
                        end if
                    end repeat
                    if {var_name} is missing value then
                        error "Sent mailbox is unavailable in account"
                    end if
                on error
                    error "Mailbox not found: {escaped}"
                end try
            end try'''

    return f'''try
                set {var_name} to mailbox "{escaped}" of {account_var}
            on error
                if "{escaped}" is "INBOX" then
                    set {var_name} to mailbox "Inbox" of {account_var}
                else
                    error "Mailbox not found: {escaped}"
                end if
            end try'''


def build_filter_condition(
    subject: str | None = None,
    sender: str | None = None,
    subject_var: str = "messageSubject",
    sender_var: str = "messageSender",
) -> str:
    """Return an AppleScript boolean expression combining subject/sender filters.

    When both are provided they are ANDed together.
    Returns ``"true"`` when neither filter is given.
    """
    conditions: list[str] = []
    if subject:
        conditions.append(f'{subject_var} contains "{escape_applescript(subject)}"')
    if sender:
        conditions.append(f'{sender_var} contains "{escape_applescript(sender)}"')
    return " and ".join(conditions) if conditions else "true"


def build_date_filter(
    days_back: int,
    var_name: str = "cutoffDate",
) -> tuple[str, str]:
    """Return (setup_script, condition_fragment) for a date-based cutoff.

    *setup_script* should be placed before the message loop.
    *condition_fragment* is an AppleScript fragment like
    ``"and messageDate > cutoffDate"`` suitable for appending to an ``if``
    clause.  When *days_back* is 0 both strings are empty.
    """
    if days_back <= 0:
        return ("", "")
    setup = f"set {var_name} to (current date) - ({days_back} * days)"
    condition = f"and messageDate > {var_name}"
    return (setup, condition)


def build_email_fields_script(
    message_var: str = "aMessage",
    include_content: bool = False,
    max_content_length: int = 300,
    output_var: str = "outputText",
) -> str:
    """Return AppleScript snippet that extracts common fields from an email.

    Sets local variables: messageSubject, messageSender, messageDate,
    messageRead.  Optionally appends a cleaned content preview to
    *output_var*.
    """
    fields = f"""set messageSubject to subject of {message_var}
                                set messageSender to sender of {message_var}
                                set messageDate to date received of {message_var}
                                set messageRead to read status of {message_var}"""

    if not include_content:
        return fields

    content = f"""
                                try
                                    set msgContent to content of {message_var}
                                    set AppleScript's text item delimiters to {{return, linefeed}}
                                    set contentParts to text items of msgContent
                                    set AppleScript's text item delimiters to " "
                                    set cleanText to contentParts as string
                                    set AppleScript's text item delimiters to ""
                                    if length of cleanText > {max_content_length} then
                                        set contentPreview to text 1 thru {max_content_length} of cleanText & "..."
                                    else
                                        set contentPreview to cleanText
                                    end if
                                    set {output_var} to {output_var} & "   Content: " & contentPreview & return
                                on error
                                    set {output_var} to {output_var} & "   Content: [Not available]" & return
                                end try"""
    return fields + content
