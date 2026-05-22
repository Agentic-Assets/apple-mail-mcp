"""Core helpers: AppleScript execution, escaping, parsing, and preference injection."""

import json
import os
import subprocess
from typing import Optional, List, Dict, Any, Tuple

from apple_mail_mcp.server import USER_PREFERENCES


def inject_preferences(func):
    """Decorator that appends user preferences to tool docstrings"""
    if USER_PREFERENCES:
        if func.__doc__:
            func.__doc__ = (
                func.__doc__.rstrip() + f"\n\nUser Preferences: {USER_PREFERENCES}"
            )
        else:
            func.__doc__ = f"User Preferences: {USER_PREFERENCES}"
    return func


def escape_applescript(value: str) -> str:
    """Escape a string for safe injection into AppleScript double-quoted strings.

    Handles backslashes first, then double quotes, then newlines/returns/tabs,
    and Unicode line/paragraph separators to prevent injection and AppleScript
    syntax errors.
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        # Unicode line/paragraph separators can break AppleScript string parsing
        .replace("\u2028", "\\n")
        .replace("\u2029", "\\n")
    )


def _sanitize_for_json(text: str) -> str:
    """Sanitize text for safe JSON serialization over MCP stdio transport.

    Preserves Unicode (including Cyrillic, CJK, Arabic, etc.) while
    stripping control characters.
    """
    # Normalize line endings first (AppleScript uses \r)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip control characters but keep \n, \t, and all printable Unicode
    return "".join(ch for ch in text if ch in ("\n", "\t") or (ord(ch) >= 32))


class AppleScriptTimeout(Exception):
    """Raised when an AppleScript invocation exceeds its per-call timeout."""


def run_applescript(script: str, timeout: Optional[int] = 120) -> str:
    """Execute AppleScript via stdin pipe for reliable multi-line handling.

    Raises ``AppleScriptTimeout`` (subclass of Exception) on per-call timeout
    so callers can isolate slow-account failures without losing siblings'
    partial results.
    """
    effective_timeout = 120 if timeout is None else timeout
    try:
        result = subprocess.run(
            ["osascript", "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=effective_timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            if stderr:
                raise Exception(f"AppleScript error: {stderr}")
        output = result.stdout.decode("utf-8", errors="replace").strip()
        return _sanitize_for_json(output)
    except subprocess.TimeoutExpired:
        raise AppleScriptTimeout("AppleScript execution timed out")
    except AppleScriptTimeout:
        raise
    except Exception as e:
        raise Exception(f"AppleScript execution failed: {str(e)}")


def normalize_search_terms(
    search_term: Optional[str] = None,
    search_terms: Optional[List[str]] = None,
) -> List[str]:
    """Return de-duplicated, non-empty search terms preserving order."""
    normalized = []

    if search_term and search_term.strip():
        normalized.append(search_term.strip())

    if search_terms:
        for term in search_terms:
            if term and term.strip():
                normalized.append(term.strip())

    unique_terms = []
    for term in normalized:
        if term not in unique_terms:
            unique_terms.append(term)

    return unique_terms


def contains_any_condition(field_name: str, values: List[str]) -> str:
    """Return AppleScript OR conditions for substring matches."""
    if not values:
        return "true"

    escaped_values = [escape_applescript(value) for value in values]
    parts = [f'{field_name} contains "{value}"' for value in escaped_values]
    return "(" + " or ".join(parts) + ")"


def normalize_message_ids(message_ids: Optional[List[Any]]) -> List[str]:
    """Return de-duplicated numeric Mail ids as strings preserving order."""
    if not message_ids:
        return []

    normalized = []
    for value in message_ids:
        value_text = str(value).strip()
        if value_text and value_text.isdigit() and value_text not in normalized:
            normalized.append(value_text)

    return normalized


def list_mail_account_names(timeout: Optional[int] = 30) -> List[str]:
    """Return configured Mail account names. Cheap (<1s) on any setup."""
    script = '''
    tell application "Mail"
        set acctNames to {}
        repeat with anAccount in (every account)
            set end of acctNames to (name of anAccount)
        end repeat
        set AppleScript's text item delimiters to linefeed
        return acctNames as string
    end tell
    '''
    raw = run_applescript(script, timeout=timeout)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def validate_account_name(account: str, timeout: Optional[int] = 30) -> Optional[str]:
    """Return an error string when *account* is unknown, else None."""
    if not account or not str(account).strip():
        return "Error: account name is required"
    names = list_mail_account_names(timeout=timeout)
    if account not in names:
        available = ", ".join(names) if names else "(none configured)"
        return (
            f"Error: account_not_found — '{account}' is not configured in Mail. "
            f"Available accounts: {available}"
        )
    return None


SENSITIVE_DIRS = (
    ".ssh",
    ".gnupg",
    ".config",
    ".aws",
    ".claude",
    os.path.join("Library", "LaunchAgents"),
    os.path.join("Library", "LaunchDaemons"),
    os.path.join("Library", "Keychains"),
)


def validate_save_path(
    path: str,
    *,
    path_label: str = "Save path",
    sensitive_action: str = "export emails to",
) -> Optional[str]:
    """Return an error string when *path* is outside home or under a sensitive dir."""
    home_dir = os.path.expanduser("~")
    resolved = os.path.realpath(os.path.expanduser(path))

    if not resolved.startswith(home_dir + os.sep) and resolved != home_dir:
        return (
            f"Error: {path_label} must be under your home directory ({home_dir}). "
            f"Got: {resolved}"
        )

    for rel in SENSITIVE_DIRS:
        sensitive_dir = os.path.join(home_dir, rel)
        if resolved.startswith(sensitive_dir + os.sep) or resolved == sensitive_dir:
            return f"Error: Cannot {sensitive_action} sensitive directory: {sensitive_dir}"

    return None


def account_not_found_json(account: str, timeout: Optional[int] = 30) -> str:
    """Structured JSON error for unknown account names."""
    names = list_mail_account_names(timeout=timeout)
    return json.dumps(
        {
            "error": "account_not_found",
            "account": account,
            "available_accounts": names,
            "emails": [],
        },
        indent=2,
    )


def reject_unknown_account(
    account: str,
    *,
    timeout: Optional[int] = None,
    json_error: bool = False,
) -> Optional[str]:
    """Return an error response string when *account* is unknown, else None."""
    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if not account_err:
        return None
    if json_error:
        return account_not_found_json(account, timeout=validation_timeout)
    return account_err


def equals_any_numeric_condition(field_name: str, values: List[str]) -> str:
    """Return AppleScript OR conditions for numeric equality matches."""
    if not values:
        return "false"

    parts = [f"{field_name} is {value}" for value in values]
    return "(" + " or ".join(parts) + ")"


def parse_email_list(output: str) -> List[Dict[str, Any]]:
    """Parse the structured email output from AppleScript"""
    emails = []
    lines = output.split("\n")

    current_email = {}
    for line in lines:
        line = line.strip()
        if (
            not line
            or line.startswith("=")
            or line.startswith("━")
            or line.startswith("📧")
            or line.startswith("⚠")
        ):
            continue

        if line.startswith("✉") or line.startswith("✓"):
            # New email entry
            if current_email:
                emails.append(current_email)

            is_read = line.startswith("✓")
            subject = line[2:].strip()  # Remove indicator
            current_email = {"subject": subject, "is_read": is_read}
        elif line.startswith("From:"):
            current_email["sender"] = line[5:].strip()
        elif line.startswith("Date:"):
            current_email["date"] = line[5:].strip()
        elif line.startswith("Preview:"):
            current_email["preview"] = line[8:].strip()
        elif line.startswith("TOTAL EMAILS"):
            # End of email list
            if current_email:
                emails.append(current_email)
            break

    if current_email and current_email not in emails:
        emails.append(current_email)

    return emails


# ---------------------------------------------------------------------------
# Shared AppleScript template helpers
# ---------------------------------------------------------------------------

# Localized inbox mailbox names. Mail.app uses the system locale to name
# the inbox folder for non-IMAP accounts (Exchange, on-my-Mac), so we must
# try multiple names to find it. IMAP accounts (iCloud, Gmail) typically
# expose 'INBOX' regardless of system language.
INBOX_NAMES = [
    "INBOX",                  # IMAP standard (iCloud, Gmail, Fastmail)
    "Inbox",                  # English non-IMAP
    "Boîte de réception",     # French (Exchange/Outlook on FR system)
    "Boîte aux lettres",      # French alt
    "Réception",              # French alt
    "Posteingang",            # German
    "Bandeja de entrada",     # Spanish
    "Posta in arrivo",        # Italian
    "Caixa de entrada",       # Portuguese
    "Postvak IN",             # Dutch
    "受信トレイ",             # Japanese
]


def inbox_mailbox_script(
    var_name: str = "inboxMailbox", account_var: str = "anAccount"
) -> str:
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
    """Return AppleScript snippet to resolve a mailbox by name with INBOX fallback.

    Handles:
    - Normal mailbox names (e.g. "Archive")
    - INBOX / Inbox case variation
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
        return f'''set {var_name} to missing value
            repeat with __mailboxLookupName in {{{name_list}}}
                try
                    set {var_name} to mailbox (__mailboxLookupName as string) of {account_var}
                    exit repeat
                end try
            end repeat
            if {var_name} is missing value then
                error "Mailbox not found: {escaped} (no localized inbox match)"
            end if'''

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
    subject: Optional[str] = None,
    sender: Optional[str] = None,
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
) -> Tuple[str, str]:
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
    # Minimal inline stripPrefixes handler (no-op) — replied_ids_script
    # references `my stripPrefixes(...)` only for the subjects fallback,
    # which callers using this entrypoint do not consume. Keep the
    # handler as a no-op identity to satisfy the reference without
    # importing constants here.
    return f'''
    on stripPrefixes(subj)
        return subj
    end stripPrefixes

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
    timeout: Optional[int] = 60,
    runner=None,
) -> set:
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
    fn = runner if runner is not None else run_applescript
    try:
        raw = fn(script, timeout=timeout)
    except AppleScriptTimeout:
        return set()
    except Exception:
        return set()
    ids: set = set()
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
    return f'''
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
    '''


def replied_ids_script(
    account_var: str = "targetAccount",
    sent_cap: int = 200,
    replied_var: str = "repliedIds",
    subjects_var: str = "sentSubjects",
    strip_prefixes_handler: str = "stripPrefixes",
) -> str:
    """Return AppleScript that builds Message-ID + subject lookup lists from Sent.

    Sets two AppleScript variables inside a ``tell application "Mail"`` block:

    - *replied_var* (default ``repliedIds``): list of Internet Message-IDs the
      user has replied to, extracted from ``In-Reply-To:`` and ``References:``
      headers in the Sent mailbox (each token like ``<abc@example.com>``).
    - *subjects_var* (default ``sentSubjects``): fallback list of stripped
      subjects from the Sent mailbox, kept for resilience when header
      extraction fails (e.g. very old Exchange messages).

    The caller must have a handler named *strip_prefixes_handler* (default
    ``stripPrefixes``) in scope — typically appended via
    ``_strip_subject_prefixes_script()``.

    Performance:
        - Bounded newest-first slice of *sent_cap* messages (default 200).
        - NO ``whose`` clauses — Mail.app can materialize remote sent mailboxes.
        - Each per-message access is wrapped in ``try`` so one bad message
          doesn't kill the loop.
        - The entire block is wrapped in ``try`` so a missing sent mailbox
          or transient error doesn't break the caller.
    """
    sent_resolve = sent_mailbox_resolve_script("sentMailbox", account_var)
    return f'''
            set {replied_var} to {{}}
            set {subjects_var} to {{}}
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
                    if sentUpperBound > 0 then
                        set sentMessages to messages 1 thru sentUpperBound of sentMailbox
                    end if
                    repeat with aSentMessage in sentMessages
                        try
                            set msgHeaders to ""
                            try
                                set msgHeaders to all headers of aSentMessage
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
                            -- Fallback subject collection
                            try
                                set sentSubj to subject of aSentMessage
                                set baseSent to my {strip_prefixes_handler}(sentSubj)
                                set end of {subjects_var} to baseSent
                            end try
                        end try
                    end repeat
                end if
            end try
    '''
