"""Composition tools: sending, replying, forwarding, and drafts."""

import os
import re
import subprocess
import tempfile
import time
from email.message import EmailMessage
from html import escape as html_escape
from pathlib import Path

from apple_mail_mcp import server  # public alias used by tests
from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error
from apple_mail_mcp.bounded_scan import (
    build_bounded_filtered_scan,
    build_bounded_message_scan,
)
from apple_mail_mcp.constants import SCAN_BOUNDS
from apple_mail_mcp.core import (
    SENSITIVE_DIRS,
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    normalize_message_ids,
    run_applescript,
    validate_account_name,
    validate_save_path,
)
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, WRITE_TOOL_ANNOTATIONS, mcp

# Backwards-compat aliases; centralized in constants.SCAN_BOUNDS so a single
# edit retunes every tool. Tests assert literal "items 1 thru 100" /
# "messages 1 thru 100" so changing the cap value here would require coordinated
# updates in tests/test_phase_2_scan_hardening.py.
DRAFT_LIST_CAP = SCAN_BOUNDS["DRAFT_LOOKUP"]
MESSAGE_LOOKUP_CAP = SCAN_BOUNDS["MESSAGE_LOOKUP"]
# Maximum number of Mail compose windows that may be open simultaneously when
# mode="open" is used. Each call in mode="open" leaves a window open; at high
# counts NSWindowServer OOMs. Agents doing bulk drafting must use mode="draft".
MAX_OPEN_COMPOSE_WINDOWS = 5
_THREADED_SUBJECT_RE = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.IGNORECASE)
_QUOTED_THREAD_MARKERS_RE = re.compile(r"(?im)(^on .+ wrote:\s*$|^-{2,}\s*original message\s*-{2,}|^from:\s*.+$|^> .+)")


def _count_open_outgoing_messages(timeout: int = 10) -> int:
    """Return the current count of open outgoing messages (compose windows) in Mail.

    Uses ``count of outgoing messages of application "Mail"`` which reflects
    each compose window exactly. Returns -1 when the probe fails (AppleScript
    error or timeout), so callers can fail-open.
    """
    script = """
    tell application "Mail"
        try
            return count of outgoing messages
        on error
            return -1
        end try
    end tell
    """
    try:
        raw = run_applescript(script, timeout=timeout).strip()
        return int(raw) if raw.lstrip("-").isdigit() else -1
    except Exception:  # noqa: BLE001 — probe must never propagate; fail-open
        return -1


def _check_open_compose_window_cap(timeout: int = 10) -> "str | None":
    """Return a serialized ToolError if the open-compose-window cap is reached.

    Returns None when it is safe to open another window. Fails open (returns
    None) when the probe itself errors, so a transient Mail.app glitch does
    not permanently block mode='open' calls.
    """
    count = _count_open_outgoing_messages(timeout=timeout)
    if count < 0:
        # Probe failed — fail open to avoid blocking legitimate calls.
        return None
    if count >= MAX_OPEN_COMPOSE_WINDOWS:
        from apple_mail_mcp.backend.base import ToolError, serialize_tool_error

        err = ToolError(
            code="TOO_MANY_OPEN_DRAFTS",
            message=(
                f"Mail already has {count} compose window(s) open "
                f"(cap: {MAX_OPEN_COMPOSE_WINDOWS}). Opening more windows risks "
                "running out of NSWindowServer resources."
            ),
            remediation={
                "preferred": ("Use mode='draft' to save quietly to Drafts without opening a window"),
                "alternative": ("Close some open compose windows in Mail, then retry with mode='open'"),
                "open_window_count": count,
                "cap": MAX_OPEN_COMPOSE_WINDOWS,
            },
        )
        return serialize_tool_error(err)
    return None


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
    ``message_id`` or the explicit ``full_inbox_export`` escape hatch.
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
                "fallback_tool": "full_inbox_export",
                "fallback_tool_args": {"mailbox": mailbox_var},
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

    Emits the bounded-slice + in-loop ``if`` pattern via
    ``build_bounded_filtered_scan``. The historical pre-filter-then-slice
    form materialized the entire Drafts folder before slicing and carried
    the same Gmail-list-eval risk as the v3.4.x inbox unread crash; see
    ``tests/test_no_unbounded_whose.py`` for the lint that forbids it.
    """
    safe_draft_subject = escape_applescript(subject_keyword)
    bounded_filter = build_bounded_filtered_scan(
        mailbox_var="draftsMailbox",
        scan_cap=DRAFT_LIST_CAP,
        target_max=1,
        condition_expr=f'(subject of aMessage) contains "{safe_draft_subject}"',
        output_var="draftMessages",
    )
    return f"""
                {bounded_filter}
                set foundDraft to missing value
                if (count of draftMessages) > 0 then
                    set foundDraft to item 1 of draftMessages
                end if
    """


def _resolve_account(account: str | None, timeout: int | None = None) -> tuple[str | None, str | None]:
    """Resolve an account argument against ``DEFAULT_MAIL_ACCOUNT``.

    Returns ``(resolved_account, error_message)``. Tools call this at the top
    of their body so callers can omit ``account`` when a default is configured
    via the ``DEFAULT_MAIL_ACCOUNT`` env var. The attribute is read lazily off
    ``apple_mail_mcp.server`` so tests can monkeypatch it after import.
    """
    if account is None or account == "":
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return None, ("Error: No account specified and no DEFAULT_MAIL_ACCOUNT env var set.")
    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return None, account_err
    return account, None


def _resolve_signature_name(include_signature: bool, signature_name: str | None) -> str | None:
    """Return the Mail signature name to apply, or None when disabled/unset."""
    if not include_signature:
        return None
    if signature_name is not None:
        signature_name = signature_name.strip()
        return signature_name or None
    default_signature = _server.DEFAULT_MAIL_SIGNATURE
    return default_signature.strip() if default_signature else None


def _compose_signature_script(message_var: str, signature_name: str | None) -> str:
    """AppleScript fragment that applies a native Mail signature by name."""
    if not signature_name:
        return ""
    safe_signature = escape_applescript(signature_name)
    return f'set message signature of {message_var} to signature "{safe_signature}"'


def _split_addresses(value: str | None) -> list[str]:
    """Return trimmed recipient addresses preserving order."""
    if not value:
        return []
    return [addr.strip() for addr in value.split(",") if addr and addr.strip()]


def _build_recipient_loops(
    cc: str | None,
    bcc: str | None,
    *,
    message_var: str | None = None,
    compact: bool = False,
    indent: str = "            ",
    trailing_indent: str | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """Build CC/BCC AppleScript loop fragments and parsed address lists."""
    recipients_cc = _split_addresses(cc)
    recipients_bcc = _split_addresses(bcc)
    of_msg = f" of {message_var}" if message_var else ""
    trail = trailing_indent if trailing_indent is not None else indent

    def _loop(kind: str, addresses: list[str]) -> str:
        if compact:
            script = ""
            for addr in addresses:
                safe_addr = escape_applescript(addr)
                script += (
                    f"make new {kind} recipient at end of {kind} recipients{of_msg} "
                    f'with properties {{address:"{safe_addr}"}}\n'
                )
            return script
        script = ""
        for addr in addresses:
            safe_addr = escape_applescript(addr)
            script += f'''
{indent}make new {kind} recipient at end of {kind} recipients{of_msg} with properties {{address:"{safe_addr}"}}
{trail}'''
        return script

    return (
        _loop("cc", recipients_cc),
        _loop("bcc", recipients_bcc),
        recipients_cc,
        recipients_bcc,
    )


def _safe_eml_name(subject: str | None) -> str:
    """Return a filesystem-safe filename stem for draft exports."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (subject or "rich-email-draft").strip())
    cleaned = cleaned.strip("-._") or "rich-email-draft"
    return cleaned[:80]


def _default_rich_draft_path(subject: str | None) -> Path:
    """Return default output path for generated rich draft EML files."""
    drafts_dir = Path.home() / "Library" / "Caches" / "apple-mail-mcp" / "rich-drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    return drafts_dir / (_safe_eml_name(subject) + ".eml")


def _account_default_alias_if_single(account: str, timeout: int | None = None) -> str | None:
    """Return the sole alias of `account` when it has exactly one configured
    email address, else None. Used when no explicit sender is requested so
    that single-address accounts still send from their own alias rather than
    Mail's global "Send new messages from" preference.
    """
    safe_account = escape_applescript(account)
    script = f'''
    tell application "Mail"
        try
            set targetAccount to account "{safe_account}"
            set emailAddrs to email addresses of targetAccount
            if (count of emailAddrs) is 1 then
                return item 1 of emailAddrs
            end if
            return ""
        on error
            return ""
        end try
    end tell
    '''
    if timeout is None:
        result = (run_applescript(script) or "").strip()
    else:
        result = (run_applescript(script, timeout=timeout) or "").strip()
    return result or None


def _compose_sender_script(variable: str, account_ref: str, sender_override: str | None) -> str:
    """Return AppleScript that sets the sender for a compose/reply/forward
    outgoing message variable, respecting Mail's account-level defaults.

    With `sender_override` the value is applied unconditionally. Without an
    override, Mail's global composing preference may otherwise win over the
    caller's account choice, so the sender is pinned to the account's only
    alias when the account has a single address, and left untouched for
    multi-alias accounts so the user's Mail preference stays in effect.
    """
    if sender_override:
        safe_sender = escape_applescript(sender_override)
        return f'set sender of {variable} to "{safe_sender}"'
    return (
        f"set emailAddrs to email addresses of {account_ref}\n"
        f"if (count of emailAddrs) is 1 then\n"
        f"    set sender of {variable} to item 1 of emailAddrs\n"
        f"end if"
    )


def _validate_from_address(
    account: str,
    from_address: str | None,
    timeout: int | None = None,
) -> tuple[str | None, str | None]:
    """Return (validated_address, error_message) for a sender override.

    When `from_address` is blank the override is skipped and both values
    are None. Otherwise the candidate is matched case-insensitively
    against the account's configured email addresses, and the original
    casing from Mail is returned on success.
    """
    if from_address is None:
        return None, None
    candidate = from_address.strip()
    if not candidate:
        return None, None
    safe_account = escape_applescript(account)
    script = f'''
    tell application "Mail"
        try
            set targetAccount to account "{safe_account}"
            set emailAddrs to email addresses of targetAccount
            set AppleScript's text item delimiters to linefeed
            set addrText to emailAddrs as text
            set AppleScript's text item delimiters to ""
            return addrText
        on error
            return ""
        end try
    end tell
    '''
    raw = (run_applescript(script) if timeout is None else run_applescript(script, timeout=timeout)) or ""
    aliases = [line.strip() for line in raw.splitlines() if line.strip()]
    if not aliases:
        return None, (f"Error: Could not read email addresses for account {account!r}.")
    lowered = {alias.lower(): alias for alias in aliases}
    match = lowered.get(candidate.lower())
    if not match:
        return None, (
            f"Error: 'from_address' {candidate!r} is not configured on account "
            f"{account!r}. Known addresses: {', '.join(aliases)}"
        )
    return match, None


_CDATA_BLOCK_PATTERN = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def _strip_cdata_wrappers(text: str | None) -> str | None:
    """Remove XML CDATA section markers from user-provided body content.

    LLM callers occasionally wrap email bodies in `<![CDATA[...]]>`. HTML
    parsers treat the opening `<![CDATA[` as a bogus comment that ends at
    the first `>` in the actual content, so it's invisible — but the
    trailing `]]>` has no preceding `<` and renders as literal text at the
    end of the message. Strip both forms so callers don't have to know.
    """
    if not text:
        return text
    text = _CDATA_BLOCK_PATTERN.sub(r"\1", text)
    return text.replace("<![CDATA[", "").replace("]]>", "")


def _standalone_compose_thread_warning(
    subject: str,
    body: str | None,
    body_html: str | None,
    standalone_confirmed: bool,
) -> str | None:
    """Return an error when a new compose looks like an accidental reply."""
    if standalone_confirmed:
        return None

    signals = []
    if _THREADED_SUBJECT_RE.search(subject or ""):
        signals.append("threaded subject prefix")

    combined_body = "\n".join(part for part in ((body or ""), (body_html or "")) if part)
    if _QUOTED_THREAD_MARKERS_RE.search(combined_body):
        signals.append("quoted-thread markers")

    if not signals:
        return None

    return (
        "Error: compose_email creates a standalone new message and will not "
        "include the original email thread. This draft looks like a reply or "
        f"forward ({', '.join(signals)}). Use reply_to_email(message_id=...) "
        "or forward_email(message_id=...) after locating the source message. "
        "If you intentionally want a brand-new standalone message, pass "
        "standalone_confirmed=True."
    )


def _build_html_from_text(text_body: str | None) -> str:
    """Return a simple HTML wrapper for plain text content."""
    safe_body = html_escape(text_body or "")
    return (
        '<html><body style="font-family: -apple-system, BlinkMacSystemFont, '
        "'Segoe UI', Arial, sans-serif; line-height: 1.45; color: #111111;\">"
        '<pre style="white-space: pre-wrap; font: inherit; margin: 0;">' + safe_body + "</pre></body></html>"
    )


def _prepare_rich_bodies(
    subject: str,
    text_body: str | None,
    html_body: str | None,
) -> tuple[str, str, list[str]]:
    """Return plain-text and HTML bodies, filling sensible placeholders."""
    plain_body = text_body or ""
    rich_body = html_body or ""

    if not plain_body and not rich_body:
        plain_body = "Draft outline\n\n- Add recipients\n- Add the final rich-text content\n- Review before sending"
        rich_body = _build_html_from_text(plain_body)
        return plain_body, rich_body, ["body"]

    if rich_body and not plain_body:
        plain_body = (
            subject.strip() + "\n\n" if subject and subject.strip() else ""
        ) + "This message contains rich HTML content. Open it in Mail for the rendered version."

    if plain_body and not rich_body:
        rich_body = _build_html_from_text(plain_body)

    return plain_body, rich_body, []


def _send_blocked(mode: str | None) -> str | None:
    """Return an error when the active server mode disallows sending."""
    if mode != "send":
        return None
    if _server.READ_ONLY:
        return "Error: Sending is disabled in read-only mode."
    if _server.DRAFT_SAFE:
        return "Error: Sending is disabled in draft-safe mode. Use mode='draft' or mode='open'."
    return None


def _save_front_compose_window_as_draft(
    *,
    close_after_save: bool = False,
    retries: int = 10,
    delay_seconds: float = 0.5,
    timeout: int | None = None,
) -> bool:
    """Ask Mail to save the front compose window as a draft."""
    close_script = ""
    if close_after_save:
        close_script = """
            delay 0.2
            close window 1 saving no
        """
    script = f"""
    tell application "Mail"
        try
            activate
            if (count of windows) is 0 then
                return "not-found"
            end if
        on error errMsg
            return "error: " & errMsg
        end try
    end tell

    tell application "System Events"
        try
            set frontmost of process "Mail" to true
            delay 0.2
            keystroke "s" using command down
        on error errMsg
            return "error: " & errMsg
        end try
    end tell

    tell application "Mail"
        try
            delay 0.5
            {close_script}
            return "saved"
        on error errMsg
            return "error: " & errMsg
        end try
    end tell
    """

    for _ in range(retries):
        if timeout is None:
            result = run_applescript(script).strip().lower()
        else:
            result = run_applescript(script, timeout=timeout).strip().lower()
        if result == "saved":
            return True
        if result.startswith("error:"):
            break
        time.sleep(delay_seconds)
    return False


@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def create_rich_email_draft(
    account: str | None = None,
    subject: str = "",
    to: str | None = None,
    text_body: str | None = None,
    html_body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    output_path: str | None = None,
    open_in_mail: bool = True,
    save_as_draft: bool = True,
    review_in_mail: bool = False,
    from_address: str | None = None,
    timeout: int | None = None,
    standalone_confirmed: bool = False,
) -> str:
    """
    Create a rich-text email draft by generating an unsent `.eml` message and optionally opening it in Mail.

    This is the preferred path for HTML or richly formatted emails because Mail reliably renders `.eml`
    content, while setting raw HTML through AppleScript often stores the literal markup instead.

    Args:
        account: Account name to use for the sender identity (e.g., "Work", "Oracle"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject: Subject line for the draft (optional; defaults to empty)
        to: Optional recipient email address(es), comma-separated for multiple
        text_body: Optional plain-text body. If omitted but html_body is provided, a fallback plain body is generated.
        html_body: Optional HTML body. If omitted but text_body is provided, a basic HTML wrapper is generated.
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        output_path: Optional path for the generated `.eml` file
        open_in_mail: If True and the subject is nonblank, open the generated `.eml` in Mail and save the front compose window to Drafts (default: True). Blank-subject drafts are written as `.eml` only by default to avoid opening incomplete drafts. Pass False to only create the `.eml` file.
        save_as_draft: Retained for compatibility; opened Mail drafts are always saved before being closed or left open.
        review_in_mail: If True, leave the saved compose window open for review. Defaults to closing the saved window after creating the draft.
        from_address: Optional sender address to stamp into the `.eml` `From:` header. Must be one of the account's configured email addresses. When omitted, Mail fills the account's default "Send new messages from" address on open.
        timeout: Optional per-AppleScript timeout in seconds for the helper calls (sender alias lookup and draft save). Defaults to the standard 120s.
        standalone_confirmed: Required explicit override when the subject/body looks like a reply or forward but the caller intentionally wants a new standalone draft.

    Returns:
        Confirmation with the generated `.eml` path, missing details, and Mail-open/save status
    """
    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None
    if not account.strip():
        return "Error: 'account' is required"

    text_body = _strip_cdata_wrappers(text_body)
    html_body = _strip_cdata_wrappers(html_body)

    thread_warning = _standalone_compose_thread_warning(subject, text_body, html_body, standalone_confirmed)
    if thread_warning:
        return thread_warning

    try:
        sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
        if sender_error:
            return sender_error

        sender_address = sender_override or _account_default_alias_if_single(account, timeout=timeout)
    except AppleScriptTimeout:
        return (
            "Error: AppleScript timed out while resolving sender for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )

    recipients_to = _split_addresses(to)
    recipients_cc = _split_addresses(cc)
    recipients_bcc = _split_addresses(bcc)
    plain_body, rich_body, body_missing = _prepare_rich_bodies(subject, text_body, html_body)

    missing_details = []
    if not subject or not subject.strip():
        missing_details.append("subject")
    if not recipients_to:
        missing_details.append("to")
    missing_details.extend(body_missing)

    message = EmailMessage()
    if subject:
        message["Subject"] = subject
    if sender_address:
        message["From"] = sender_address
    if recipients_to:
        message["To"] = ", ".join(recipients_to)
    if recipients_cc:
        message["Cc"] = ", ".join(recipients_cc)
    if recipients_bcc:
        message["Bcc"] = ", ".join(recipients_bcc)
    message["X-Unsent"] = "1"
    message.set_content(plain_body)
    message.add_alternative(rich_body, subtype="html")

    if output_path:
        # Resolve and validate the caller-supplied path before writing.
        # validate_save_path also enforces a home-dir restriction which is
        # intentionally narrower here — we only guard sensitive dirs.
        try:
            draft_path = Path(output_path).expanduser()
            _resolved = str(draft_path.resolve())
        except (RuntimeError, ValueError, OSError) as exc:
            return f"Error: output_path is not a valid filesystem path: {exc}"
        _home = Path.home().resolve()
        for _rel in SENSITIVE_DIRS:
            _sensitive = str(_home / _rel)
            if _resolved.startswith(_sensitive + os.sep) or _resolved == _sensitive:
                return (
                    f"Error: output_path targets a sensitive directory and cannot be "
                    f"used as a draft destination: {_sensitive}"
                )
    else:
        draft_path = _default_rich_draft_path(subject)

    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_bytes(bytes(message))

    can_open_in_mail = bool(subject and subject.strip())
    mail_open_skipped = open_in_mail and not can_open_in_mail
    opened = False
    saved = False
    if open_in_mail and can_open_in_mail:
        try:
            subprocess.run(["open", "-a", "Mail", str(draft_path)], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            return (
                f"Error: Failed to open draft in Mail.app: {exc}. The .eml file was written but Mail could not open it."
            )
        opened = True
        try:
            saved = _save_front_compose_window_as_draft(
                close_after_save=not review_in_mail,
                timeout=timeout,
            )
        except AppleScriptTimeout:
            saved = False

    output_lines = ["RICH EMAIL DRAFT", "", "✓ Rich draft prepared successfully!", ""]
    output_lines.append("Account: " + account)
    output_lines.append("Subject: " + (subject if subject else "[empty]"))
    output_lines.append("EML path: " + str(draft_path))
    output_lines.append("Opened in Mail: " + ("yes" if opened else "no"))
    if opened:
        output_lines.append("Saved in Drafts: " + ("yes" if saved else "no"))
        output_lines.append("Left open for review: " + ("yes" if review_in_mail else "no"))
    if sender_address:
        output_lines.append("From: " + sender_address)
    if recipients_to:
        output_lines.append("To: " + ", ".join(recipients_to))
    if recipients_cc:
        output_lines.append("CC: " + ", ".join(recipients_cc))
    if recipients_bcc:
        output_lines.append("BCC: " + ", ".join(recipients_bcc))
    output_lines.append("Missing details: " + (", ".join(missing_details) if missing_details else "none"))
    output_lines.append(
        "Note: Prefer this `.eml` workflow for HTML email drafts; Mail renders it more reliably than raw HTML injected via AppleScript content."
    )
    if mail_open_skipped:
        output_lines.append(
            "Note: Blank-subject rich drafts are written as `.eml` only by default to avoid opening incomplete drafts."
        )
    return "\n".join(output_lines)


def _send_html_email(
    account: str,
    to: str,
    subject: str,
    body_plain: str,
    body_html: str,
    cc: str | None = None,
    bcc: str | None = None,
    attachments_script: str = "",
    mode: str = "send",
    sender_override: str | None = None,
    timeout: int | None = None,
    signature_name: str | None = None,
) -> str:
    """Send an HTML-formatted email via NSPasteboard clipboard injection.

    Uses AppleScriptObjC to place HTML on the clipboard with the proper
    pasteboard type, creates a compose window, tabs into the body, and
    pastes.  Then sends, saves as draft, or leaves open for review.
    """
    safe_account = escape_applescript(account)
    escaped_subject = escape_applescript(subject)

    # Build recipient scripts
    to_lines = ""
    for addr in _split_addresses(to):
        to_lines += (
            f'make new to recipient at end of to recipients with properties {{address:"{escape_applescript(addr)}"}}\n'
        )

    cc_lines, bcc_lines, _, _ = _build_recipient_loops(cc, bcc, compact=True)

    sender_script = _compose_sender_script("newMsg", f'account "{safe_account}"', sender_override)
    signature_script = _compose_signature_script("newMsg", signature_name)

    # Mode-specific behaviour after paste
    if mode == "send":
        post_paste_script = """
            -- Send via keyboard shortcut
            keystroke "d" using {command down, shift down}
        """
        success_text = "Email sent successfully (HTML)"
    elif mode == "draft":
        post_paste_script = """
            -- Save as draft: save then close (one persist only)
            delay 0.5
            tell application "Mail"
                save newMsg
                close window 1 saving no
            end tell
        """
        success_text = "Email saved as draft (HTML)"
    else:  # open
        post_paste_script = """
            -- Save first, then leave open for review
            keystroke "s" using command down
            delay 0.5
            tell application "Mail"
                save newMsg
            end tell
        """
        success_text = "Email opened in Mail for review (HTML). Edit and send when ready."

    # Write HTML to temp file so the AppleScript can read it without
    # worrying about escaping quotes/special chars in the HTML string.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".html",
        prefix="mail_html_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(body_html)
        html_temp_path = tmp.name

    script = f'''
use framework "Foundation"
use framework "AppKit"
use scripting additions

-- Step 1: Read HTML from temp file and place on clipboard
set htmlString to do shell script "cat " & quoted form of "{html_temp_path}"
set pb to current application's NSPasteboard's generalPasteboard()

-- Save current clipboard for restoration
set oldClip to pb's stringForType:(current application's NSPasteboardTypeString)

pb's clearContents()
set htmlData to (current application's NSString's stringWithString:htmlString)'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
pb's setData:htmlData forType:(current application's NSPasteboardTypeHTML)

-- Step 2: Create compose window (empty body so signature doesn't interfere)
tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{escaped_subject}", content:"", visible:true}}
    {sender_script}
    {signature_script}
    tell newMsg
        {to_lines}
        {cc_lines}
        {bcc_lines}
        {attachments_script}
    end tell
    activate
end tell

-- Step 3: Wait for compose window to render
delay 2.5

-- Step 4: Tab from header fields into body, then paste
tell application "System Events"
    set frontmost of process "Mail" to true
    delay 0.5
    tell process "Mail"
        -- Tab through: To -> Cc -> Bcc -> Subject -> Body
        -- 7 tabs covers all combinations of visible/hidden CC/BCC fields
        repeat 7 times
            key code 48
            delay 0.1
        end repeat
        delay 0.3

        -- Paste HTML without Cmd+A so Mail's native signature remains intact.
        keystroke "v" using command down
        delay 0.5

        {post_paste_script}
    end tell
end tell

-- Step 5: Clean up temp file
do shell script "rm -f " & quoted form of "{html_temp_path}"

-- Step 6: Restore clipboard
if oldClip is not missing value then
    pb's clearContents()
    pb's setString:oldClip forType:(current application's NSPasteboardTypeString)
end if

return "{success_text}"
'''

    try:
        output = run_applescript(script, timeout=timeout if timeout is not None else 30)
        # Build confirmation message
        confirm = f"{output}\n\nFrom: {account}\nTo: {to}\nSubject: {subject}"
        if cc:
            confirm += f"\nCC: {cc}"
        if bcc:
            confirm += f"\nBCC: {bcc}"
        return confirm
    except AppleScriptTimeout:
        return "Error: HTML email script timed out"
    except Exception as e:
        err = str(e)
        if err.startswith("AppleScript error: "):
            err = err[len("AppleScript error: ") :]
        elif err.startswith("AppleScript execution failed: "):
            err = err[len("AppleScript execution failed: ") :]
        return f"Error: HTML email send failed: {err}"
    finally:
        temp_path = Path(html_temp_path)
        if temp_path.exists():
            temp_path.unlink()


def _validate_attachment_paths(attachments: str) -> tuple[list[str], str | None]:
    """Validate and resolve attachment file paths.

    Splits comma-separated paths, expands tildes, resolves symlinks,
    and enforces security constraints (home-dir-only, no sensitive dirs,
    file must exist).

    Returns:
        A tuple of (resolved_paths, error_message).
        If error_message is not None, resolved_paths should be ignored.
    """
    resolved_paths: list[str] = []
    raw_paths = [p.strip() for p in attachments.split(",")]

    for raw_path in raw_paths:
        if not raw_path:
            continue

        # Expand tilde and resolve symlinks
        resolved_path = Path(raw_path).expanduser().resolve()
        resolved = str(resolved_path)

        path_err = validate_save_path(
            resolved,
            path_label="Attachment path",
            sensitive_action="attach files from",
        )
        if path_err:
            return [], path_err

        # File must exist
        if not resolved_path.is_file():
            return [], f"Error: Attachment file does not exist: {resolved}"

        resolved_paths.append(resolved)

    if not resolved_paths:
        return [], "Error: No valid attachment paths provided."

    return resolved_paths, None


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS)
@inject_preferences
def reply_to_email(
    account: str | None = None,
    subject_keyword: str = "",
    reply_body: str = "",
    reply_to_all: bool = False,
    cc: str | None = None,
    bcc: str | None = None,
    send: bool = False,
    mode: str | None = None,
    attachments: str | None = None,
    body_html: str | None = None,
    from_address: str | None = None,
    message_id: str | None = None,
    recent_days: float = 2.0,
    timeout: int | None = None,
    include_signature: bool = True,
    signature_name: str | None = None,
) -> str:
    """
    Reply to an email by message_id (preferred) or subject keyword.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject_keyword: Keyword to search for in email subjects (omit when message_id is set)
        reply_body: The body text of the reply
        reply_to_all: If True, reply to all recipients; if False, reply only to sender (default: False)
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        send: If True, send immediately; if False (default), save as draft. Ignored if mode is set.
        mode: Delivery mode — "draft" (default, save quietly to Drafts and close), "open" (save first, then leave compose window open for review), or "send" (send immediately). Overrides send parameter when set.
        attachments: Optional file paths to attach, comma-separated for multiple (e.g., "/path/to/file1.png,/path/to/file2.pdf")
        body_html: Optional HTML body for rich formatting (bold, headings, links, colors). When provided, the reply is pasted as HTML. The plain 'reply_body' field is still required as fallback text.
        from_address: Optional sender address to use for this reply. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        message_id: Exact numeric Apple Mail message id from search/list tools. Required preference over subject_keyword whenever an id is available.
        recent_days: When searching by subject_keyword, only scan messages from the last N days (default: 2.0 / 48h). Must be > 0 — full-mailbox subject scans are refused; pass `message_id` for constant-cost lookups or fall back to `full_inbox_export`.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to 120s for the main reply script and up to 30s for alias validation.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.

    Returns:
        Confirmation message with details of the reply sent, saved draft, or opened draft
    """

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None
    if not message_id and not subject_keyword:
        return "Error: 'subject_keyword' or 'message_id' is required"

    lookup_script, lookup_error = _build_found_message_lookup(
        "inboxMailbox",
        message_id=message_id,
        subject_keyword=subject_keyword or None,
        recent_days=recent_days,
        messages_var="inboxMessages",
        tool_name="reply_to_email",
    )
    if lookup_error:
        if isinstance(lookup_error, ToolError):
            return serialize_tool_error(lookup_error)
        return lookup_error

    reply_body = _strip_cdata_wrappers(reply_body) or ""
    body_html = _strip_cdata_wrappers(body_html)

    try:
        sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
    except AppleScriptTimeout:
        return (
            "Error: AppleScript timed out while validating sender for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    if sender_error:
        return sender_error
    resolved_signature_name = _resolve_signature_name(include_signature, signature_name)

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_subject_keyword = escape_applescript(subject_keyword) if subject_keyword else ""
    not_found_message = (
        f"Error: No email found for message_id={message_id}"
        if message_id
        else f"Error: No email found matching: {safe_subject_keyword}"
    )

    # Write reply body to a temp file to avoid AppleScript string escaping
    # issues with special characters (em dashes, curly quotes, colons, etc.)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="mail_reply_",
        delete=False,
        encoding="utf-8",
    ) as body_tmp:
        body_tmp.write(reply_body)
        body_temp_path = body_tmp.name

    # If body_html provided, write it to a temp file for the AppleScript to read.
    # If plain text only, wrap it in basic HTML so the clipboard paste renders
    # properly in Mail's HTML compose view (preserving line breaks and gap).
    html_temp_path = None
    # Append an empty paragraph to create a visible gap before the quoted original.
    # Mail strips trailing <br> tags, so we use a <p> with &nbsp; instead.
    gap_html = "<div><br></div><div><br></div>"
    if body_html:
        html_content = body_html + gap_html
    else:
        # Wrap plain text in HTML, converting newlines to <br>
        escaped_plain = html_escape(reply_body)
        escaped_plain = escaped_plain.replace("\n", "<br>")
        html_content = f"<div>{escaped_plain}</div>{gap_html}"
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".html",
        prefix="mail_reply_html_",
        delete=False,
        encoding="utf-8",
    ) as html_tmp:
        html_tmp.write(html_content)
        html_temp_path = html_tmp.name

    # Build the reply command based on reply_to_all flag
    if reply_to_all:
        reply_command = "set replyMessage to reply foundMessage with opening window and reply to all"
    else:
        reply_command = "set replyMessage to reply foundMessage with opening window"

    cc_script, bcc_script, _, _ = _build_recipient_loops(cc, bcc, message_var="replyMessage")

    # Build attachment script if provided
    attachment_script = ""
    attachment_info = ""
    if attachments:
        validated_paths, error = _validate_attachment_paths(attachments)
        if error:
            return error
        for path in validated_paths:
            safe_path = escape_applescript(path)
            attachment_script += f'''
                set theFile to POSIX file "{safe_path}"
                make new attachment with properties {{file name:theFile}} at after the last paragraph
                delay 1
            '''
            attachment_info += f"  {path}\n"

    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""
    safe_attachment_info = escape_applescript(attachment_info) if attachment_info else ""

    # Resolve delivery mode: mode parameter takes precedence over send boolean
    if mode is not None:
        if mode not in ("send", "draft", "open"):
            return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
        effective_mode = mode
    else:
        effective_mode = "send" if send else "draft"

    blocked = _send_blocked(effective_mode)
    if blocked:
        return blocked

    if effective_mode == "open":
        cap_err = _check_open_compose_window_cap()
        if cap_err:
            return cap_err

    # Read body from temp file in AppleScript (avoids all string escaping issues)
    read_body_script = f'set replyBodyText to do shell script "cat " & quoted form of "{body_temp_path}"'

    # Determine behavior per mode
    # All modes use HTML clipboard paste (via NSPasteboard) to insert the reply body.
    # This preserves Mail.app's native quoted original in the HTML layer.
    # (setting `content` via AppleScript overwrites the HTML layer entirely,
    # destroying the email thread history.)

    if effective_mode == "send":
        header_text = "SENDING REPLY"
        post_paste_action = """
                delay 0.5
                tell application "Mail"
                    send replyMessage
                end tell"""
        success_text = "Reply sent successfully!"
    elif effective_mode == "open":
        header_text = "OPENING REPLY FOR REVIEW"
        post_paste_action = """
                delay 0.5
                tell application "Mail"
                    save replyMessage
                end tell"""
        success_text = "Reply opened in Mail for review. Edit and send when ready."
    else:  # draft
        header_text = "SAVING REPLY AS DRAFT"
        post_paste_action = """
                delay 0.5
                tell application "Mail"
                    save replyMessage
                    close window 1 saving no
                end tell"""
        success_text = "Reply saved as draft!"

    cleanup_script = f'do shell script "rm -f " & quoted form of "{body_temp_path}"'
    html_cleanup_script = f'do shell script "rm -f " & quoted form of "{html_temp_path}"'

    sender_script = _compose_sender_script("replyMessage", "targetAccount", sender_override)
    signature_script = _compose_signature_script("replyMessage", resolved_signature_name)

    script = f'''
use framework "Foundation"
use framework "AppKit"
use scripting additions

-- Step 1: Place reply body HTML on clipboard via NSPasteboard
set htmlString to do shell script "cat " & quoted form of "{html_temp_path}"
set pb to current application's NSPasteboard's generalPasteboard()
set oldClip to pb's stringForType:(current application's NSPasteboardTypeString)
pb's clearContents()
set htmlData to (current application's NSString's stringWithString:htmlString)'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
pb's setData:htmlData forType:(current application's NSPasteboardTypeHTML)

-- Step 2: Find the email and create reply
tell application "Mail"
    set outputText to "{header_text}" & return & return

    try
        -- Read reply body from temp file (for output text only)
        {read_body_script}

        set targetAccount to account "{safe_account}"
        {inbox_mailbox_script("inboxMailbox", "targetAccount")}
        {lookup_script}

        if foundMessage is not missing value then
            set messageSubject to subject of foundMessage
            set messageSender to sender of foundMessage
            set messageDate to date received of foundMessage

            -- Create reply
            {reply_command}
            delay 0.5

            {sender_script}
            {signature_script}

            -- Add CC/BCC recipients
            {cc_script}
            {bcc_script}

            -- Add attachments
            {attachment_script}

            -- Paste reply body (HTML already on clipboard from Step 1)
            set visible of replyMessage to true
            activate
            delay 1.5

            tell application "System Events"
                tell process "Mail"
                    keystroke "v" using command down
                end tell
            end tell
            delay 0.5

            {post_paste_action}

            set outputText to outputText & "{success_text}" & return
            set outputText to outputText & "To: " & messageSender & return
            set outputText to outputText & "Subject: " & messageSubject & return
    '''

    if cc:
        script += f"""
                set outputText to outputText & "CC: {safe_cc}" & return
    """

    if bcc:
        script += f"""
                set outputText to outputText & "BCC: {safe_bcc}" & return
    """

    if attachments:
        script += f'''
                set outputText to outputText & "Attachments:" & return & "{safe_attachment_info}" & return
    '''

    script += f"""
            else
                set outputText to outputText & "{not_found_message}" & return
            end if

            -- Clean up temp files
            {cleanup_script}
            {html_cleanup_script}

        on error errMsg
            -- Clean up temp files even on error
            try
                {cleanup_script}
                {html_cleanup_script}
            end try
            -- Restore clipboard before returning on error
            if oldClip is not missing value then
                pb's clearContents()
                pb's setString:oldClip forType:(current application's NSPasteboardTypeString)
            end if
            return "Error: " & errMsg & return & "Please check that the account name is correct and the email exists."
        end try

        -- Restore clipboard
        if oldClip is not missing value then
            pb's clearContents()
            pb's setString:oldClip forType:(current application's NSPasteboardTypeString)
        end if

        return outputText
    end tell
    """

    try:
        if timeout is None:
            return run_applescript(script)
        return run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while replying on account {account!r}. Try again or pass a larger `timeout`."
        )
    except Exception as e:
        err = str(e)
        if err.startswith("AppleScript error: "):
            err = err[len("AppleScript error: ") :]
        elif err.startswith("AppleScript execution failed: "):
            err = err[len("AppleScript execution failed: ") :]
        return f"Error: Reply failed: {err}"
    finally:
        # Belt-and-suspenders cleanup in case AppleScript didn't run
        body_path = Path(body_temp_path)
        if body_path.exists():
            body_path.unlink()
        if html_temp_path:
            html_path = Path(html_temp_path)
            if html_path.exists():
                html_path.unlink()


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS)
@inject_preferences
def compose_email(
    account: str | None = None,
    to: str = "",
    subject: str = "",
    body: str = "",
    cc: str | None = None,
    bcc: str | None = None,
    attachments: str | None = None,
    mode: str = "draft",
    body_html: str | None = None,
    from_address: str | None = None,
    timeout: int | None = None,
    include_signature: bool = True,
    signature_name: str | None = None,
    standalone_confirmed: bool = False,
) -> str:
    """
    Compose a new standalone email from a specific account.

    This tool never includes the original email thread. Use ``reply_to_email``
    or ``forward_email`` with ``message_id`` when responding to existing mail.

    Args:
        account: Account name to send from (e.g., "Gmail", "Work", "Personal"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        to: Recipient email address(es), comma-separated for multiple
        subject: Email subject line
        body: Email body text (used as plain-text fallback when body_html is provided)
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        attachments: Optional file paths to attach, comma-separated for multiple (e.g., "/path/to/file1.png,/path/to/file2.pdf")
        mode: Delivery mode — "draft" (default, save quietly to Drafts), "open" (save first, then leave compose window open for review), or "send" (send immediately)
        body_html: Optional HTML body for rich formatting (bold, headings, links, colors). When provided, the email is sent as HTML. The plain 'body' field is still required as fallback text.
        from_address: Optional sender address to use for this message. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to the standard 120s. Raise this when working with large mailboxes or slow accounts.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.
        standalone_confirmed: Required explicit override when the subject/body looks like a reply or forward but the caller intentionally wants a new standalone message.

    Returns:
        Confirmation message with details of the email
    """

    # Validate mode
    if mode not in ("send", "draft", "open"):
        return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
    blocked = _send_blocked(mode)
    if blocked:
        return blocked

    if mode == "open":
        cap_err = _check_open_compose_window_cap()
        if cap_err:
            return cap_err

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None
    if not to:
        return "Error: 'to' is required"

    body = _strip_cdata_wrappers(body) or ""
    body_html = _strip_cdata_wrappers(body_html)

    thread_warning = _standalone_compose_thread_warning(subject, body, body_html, standalone_confirmed)
    if thread_warning:
        return thread_warning

    # Validate optional sender override
    try:
        sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
    except AppleScriptTimeout:
        return (
            "Error: AppleScript timed out while validating sender for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    if sender_error:
        return sender_error
    resolved_signature_name = _resolve_signature_name(include_signature, signature_name)

    # Validate and resolve attachments early
    attachment_script = ""
    attachment_info = ""
    if attachments:
        validated_paths, error = _validate_attachment_paths(attachments)
        if error:
            return error
        for path in validated_paths:
            safe_path = escape_applescript(path)
            attachment_script += f'''
                set theFile to POSIX file "{safe_path}"
                make new attachment with properties {{file name:theFile}} at after the last paragraph
                delay 1
            '''
            attachment_info += f"  {path}\n"

    # --- HTML path: use NSPasteboard clipboard injection ---
    if body_html:
        return _send_html_email(
            account=account,
            to=to,
            subject=subject,
            body_plain=body,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            attachments_script=attachment_script,
            mode=mode,
            sender_override=sender_override,
            timeout=timeout,
            signature_name=resolved_signature_name,
        )

    # --- Plain-text path: existing AppleScript approach ---
    safe_account = escape_applescript(account)
    escaped_subject = escape_applescript(subject)
    escaped_body = escape_applescript(body)

    # Build TO recipients (split comma-separated addresses)
    to_script = ""
    for addr in _split_addresses(to):
        safe_addr = escape_applescript(addr)
        to_script += f'''
                make new to recipient at end of to recipients with properties {{address:"{safe_addr}"}}
        '''

    cc_script, bcc_script, _, _ = _build_recipient_loops(
        cc,
        bcc,
        indent="                ",
        trailing_indent="            ",
    )

    safe_to = escape_applescript(to)
    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""
    safe_attachment_info = escape_applescript(attachment_info) if attachment_info else ""

    sender_script = _compose_sender_script("newMessage", "targetAccount", sender_override)
    signature_script = _compose_signature_script("newMessage", resolved_signature_name)

    # Determine behavior per mode
    if mode == "send":
        header_text = "COMPOSING EMAIL"
        visible = "false"
        send_command = "send newMessage"
        success_text = "✓ Email sent successfully!"
    elif mode == "open":
        header_text = "OPENING EMAIL FOR REVIEW"
        visible = "true"
        send_command = "save newMessage\n            activate"
        success_text = "✓ Email opened in Mail for review. Edit and send when ready."
    else:  # draft
        header_text = "SAVING EMAIL AS DRAFT"
        visible = "false"
        send_command = "save newMessage"
        success_text = "✓ Email saved as draft!"

    script = f'''
    tell application "Mail"
        set outputText to "{header_text}" & return & return

        try
            set targetAccount to account "{safe_account}"

            -- Create new outgoing message
            set newMessage to make new outgoing message with properties {{subject:"{escaped_subject}", content:"{escaped_body}", visible:{visible}}}

            {sender_script}
            {signature_script}

            -- Add TO/CC/BCC recipients
            tell newMessage
                {to_script}
                {cc_script}
                {bcc_script}
            end tell

            -- Add attachments
            tell newMessage
                {attachment_script}
            end tell

            -- Send, save as draft, or leave open for review
            {send_command}

            set outputText to outputText & "{success_text}" & return
            set outputText to outputText & "To: {safe_to}" & return
            set outputText to outputText & "Subject: {escaped_subject}" & return
    '''

    if cc:
        script += f"""
            set outputText to outputText & "CC: {safe_cc}" & return
    """

    if bcc:
        script += f"""
            set outputText to outputText & "BCC: {safe_bcc}" & return
    """

    if attachments:
        script += f'''
            set outputText to outputText & "Attachments:" & return & "{safe_attachment_info}" & return
    '''

    script += """

        on error errMsg
            return "Error: " & errMsg & return & "Please check that the account name and email addresses are correct."
        end try

        return outputText
    end tell
    """

    try:
        result = run_applescript(script) if timeout is None else run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while composing email for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    return result


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS)
@inject_preferences
def forward_email(
    account: str | None = None,
    subject_keyword: str = "",
    to: str = "",
    message: str | None = None,
    mailbox: str = "INBOX",
    cc: str | None = None,
    bcc: str | None = None,
    from_address: str | None = None,
    mode: str = "draft",
    message_id: str | None = None,
    recent_days: float = 2.0,
    timeout: int | None = None,
    include_signature: bool = True,
    signature_name: str | None = None,
) -> str:
    """
    Forward an email to one or more recipients.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject_keyword: Keyword to search for in email subjects (omit when message_id is set)
        to: Recipient email address(es), comma-separated for multiple
        message: Optional message to add before forwarded content
        mailbox: Mailbox to search in (default: "INBOX")
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        from_address: Optional sender address to use when forwarding. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        mode: Delivery mode — "draft" (default, save quietly to Drafts), "open" (save first, then leave compose window open for review), or "send" (send immediately)
        message_id: Exact numeric Apple Mail message id from search/list tools. Required preference over subject_keyword whenever an id is available.
        recent_days: When searching by subject_keyword, only scan messages from the last N days (default: 2.0 / 48h). Must be > 0 — full-mailbox subject scans are refused; pass `message_id` for constant-cost lookups or fall back to `full_inbox_export`.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to the standard 120s. Raise this when working with large mailboxes or slow accounts.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.

    Returns:
        Confirmation message with details of forwarded email
    """

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None
    if not message_id and not subject_keyword:
        return "Error: 'subject_keyword' or 'message_id' is required"
    if not to:
        return "Error: 'to' is required"

    lookup_script, lookup_error = _build_found_message_lookup(
        "targetMailbox",
        message_id=message_id,
        subject_keyword=subject_keyword or None,
        recent_days=recent_days,
        tool_name="forward_email",
    )
    if lookup_error:
        if isinstance(lookup_error, ToolError):
            return serialize_tool_error(lookup_error)
        return lookup_error

    message = _strip_cdata_wrappers(message)

    # Validate mode
    if mode not in ("send", "draft", "open"):
        return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
    blocked = _send_blocked(mode)
    if blocked:
        return blocked

    if mode == "open":
        cap_err = _check_open_compose_window_cap()
        if cap_err:
            return cap_err

    try:
        sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
    except AppleScriptTimeout:
        return (
            "Error: AppleScript timed out while validating sender for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    if sender_error:
        return sender_error
    resolved_signature_name = _resolve_signature_name(include_signature, signature_name)

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_subject_keyword = escape_applescript(subject_keyword) if subject_keyword else ""
    safe_to = escape_applescript(to)
    safe_mailbox = escape_applescript(mailbox)
    not_found_message = (
        f"Error: No email found for message_id={message_id}"
        if message_id
        else f"Error: No email found matching: {safe_subject_keyword}"
    )

    sender_script = _compose_sender_script("forwardMessage", "targetAccount", sender_override)
    signature_script = _compose_signature_script("forwardMessage", resolved_signature_name)

    cc_script, bcc_script, _, _ = _build_recipient_loops(cc, bcc, message_var="forwardMessage")

    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""

    # Build TO recipients (split comma-separated)
    to_script = ""
    for addr in _split_addresses(to):
        safe_addr = escape_applescript(addr)
        to_script += f'''
                make new to recipient at end of to recipients of forwardMessage with properties {{address:"{safe_addr}"}}
        '''

    # If an optional message is provided, write it as HTML to a temp file
    # for NSPasteboard clipboard injection (preserves forwarded content).
    fwd_html_temp_path = None
    fwd_html_paste_script = ""
    fwd_html_cleanup_script = ""
    if message:
        escaped_plain = html_escape(message)
        escaped_plain = escaped_plain.replace("\n", "<br>")
        fwd_html_content = f"{escaped_plain}<br><br>"
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            prefix="mail_fwd_html_",
            delete=False,
            encoding="utf-8",
        ) as fwd_html_tmp:
            fwd_html_tmp.write(fwd_html_content)
            fwd_html_temp_path = fwd_html_tmp.name
        fwd_html_cleanup_script = f'do shell script "rm -f " & quoted form of "{fwd_html_temp_path}"'
        fwd_html_paste_script = f"""
                set visible of forwardMessage to true
                activate
                delay 1.5

                set htmlString to do shell script "cat " & quoted form of "{fwd_html_temp_path}"
                set pb to current application's NSPasteboard's generalPasteboard()
                set oldClip to pb's stringForType:(current application's NSPasteboardTypeString)
                pb's clearContents()
                set htmlData to (current application's NSString's stringWithString:htmlString)'s dataUsingEncoding:(current application's NSUTF8StringEncoding)
                pb's setData:htmlData forType:(current application's NSPasteboardTypeHTML)

                tell application "System Events"
                    tell process "Mail"
                        keystroke "v" using command down
                    end tell
                end tell
                delay 0.5

                if oldClip is not missing value then
                    pb's clearContents()
                    pb's setString:oldClip forType:(current application's NSPasteboardTypeString)
                end if
        """

    use_frameworks = ""
    if message:
        use_frameworks = """use framework "Foundation"
use framework "AppKit"
use scripting additions
"""

    if mode == "send":
        header_text = "FORWARDING EMAIL"
        post_forward_action = "send forwardMessage"
        success_text = "Email forwarded successfully."
    elif mode == "open":
        header_text = "OPENING FORWARD FOR REVIEW"
        post_forward_action = (
            "set visible of forwardMessage to true\n            save forwardMessage\n            activate"
        )
        success_text = "Forward opened in Mail for review. Edit and send when ready."
    else:
        header_text = "SAVING FORWARD AS DRAFT"
        post_forward_action = "save forwardMessage\n            close window 1 saving no"
        success_text = "Forward saved as draft."

    script = f'''{use_frameworks}
tell application "Mail"
    set outputText to "{header_text}" & return & return

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

        {lookup_script}

        if foundMessage is not missing value then
            set messageSubject to subject of foundMessage
            set messageSender to sender of foundMessage
            set messageDate to date received of foundMessage

            -- Create forward
            set forwardMessage to forward foundMessage with opening window

            {sender_script}
            {signature_script}

            -- Add recipients
            {to_script}

            -- Add CC/BCC recipients
            {cc_script}
            {bcc_script}

            -- Add optional message via HTML clipboard paste (preserves forwarded content)
            {fwd_html_paste_script}

            -- Send, save as draft, or leave open for review
            {post_forward_action}

            -- Clean up temp files
            {fwd_html_cleanup_script}

            set outputText to outputText & "{success_text}" & return
            set outputText to outputText & "To: {safe_to}" & return
            set outputText to outputText & "Subject: " & messageSubject & return
    '''

    if cc:
        script += f"""
                set outputText to outputText & "CC: {safe_cc}" & return
    """

    if bcc:
        script += f"""
                set outputText to outputText & "BCC: {safe_bcc}" & return
    """

    script += f"""
            else
                set outputText to outputText & "{not_found_message}" & return
            end if

        on error errMsg
            try
                {fwd_html_cleanup_script}
            end try
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    """

    try:
        if timeout is None:
            return run_applescript(script)
        return run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while forwarding email for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    except Exception as e:
        if not message:
            raise
        err = str(e)
        if err.startswith("AppleScript error: "):
            err = err[len("AppleScript error: ") :]
        elif err.startswith("AppleScript execution failed: "):
            err = err[len("AppleScript execution failed: ") :]
        return f"Error: Forward failed: {err}"
    finally:
        if fwd_html_temp_path:
            fwd_html_path = Path(fwd_html_temp_path)
            if fwd_html_path.exists():
                fwd_html_path.unlink()


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS)
@inject_preferences
def manage_drafts(
    account: str | None = None,
    action: str = "list",
    subject: str | None = None,
    to: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    draft_subject: str | None = None,
    from_address: str | None = None,
    timeout: int | None = None,
    standalone_confirmed: bool = False,
    hide_empty: bool = False,
    dry_run: bool = True,
    max_deletes: int = 20,
) -> str:
    """
    Manage draft emails - list, create, send, open, delete, or cleanup_empty drafts.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        action: Action to perform: "list", "create", "send", "open", "delete", "cleanup_empty". Use "open" to open a draft in a visible compose window for review before sending. Use "cleanup_empty" to remove orphaned blank drafts (preview-only by default).
        subject: Email subject (required for create)
        to: Recipient email(s) for create (comma-separated)
        body: Email body (required for create)
        cc: Optional CC recipients for create
        bcc: Optional BCC recipients for create
        draft_subject: Subject keyword to find draft (required for send/open/delete)
        from_address: Optional sender address for new drafts (action="create"). Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to the standard 120s. Raise this when working with large mailboxes or slow accounts.
        standalone_confirmed: Required explicit override for action="create" when the subject/body looks like a reply or forward but the caller intentionally wants a new standalone draft.
        hide_empty: For action="list", skip drafts whose subject AND body are both blank (orphaned compose windows). Default False (show everything).
        dry_run: For action="cleanup_empty", when True (default) only previews which blank drafts would be removed without deleting. Set False to actually delete. Ignored by other actions.
        max_deletes: For action="cleanup_empty", maximum number of blank drafts to delete in one call (safety cap). Default 20. Ignored by other actions.

    Returns:
        Formatted output based on action. For action="list" each draft now reports
        its message id, To recipients, and a short body snippet so the list is
        directly triageable; verify full threading with `get_email_by_id`.
    """

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None

    body = _strip_cdata_wrappers(body)

    # Escape account for all paths
    safe_account = escape_applescript(account)

    if action == "list":
        hide_empty_flag = "true" if hide_empty else "false"
        script = f'''
        tell application "Mail"
            set hideEmpty to {hide_empty_flag}
            set draftLines to ""
            set shownCount to 0

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set draftMessages to messages 1 thru {DRAFT_LIST_CAP} of draftsMailbox

                repeat with aDraft in draftMessages
                    try
                        set draftSubject to subject of aDraft
                        set draftId to (id of aDraft) as string
                        set draftDate to date sent of aDraft

                        -- Body snippet (first 140 chars, whitespace collapsed)
                        set draftBody to ""
                        try
                            set draftBody to content of aDraft
                        end try
                        set AppleScript's text item delimiters to {{return, linefeed, tab}}
                        set bodyParts to text items of draftBody
                        set AppleScript's text item delimiters to " "
                        set bodySnippet to bodyParts as string
                        set AppleScript's text item delimiters to ""
                        if length of bodySnippet > 140 then
                            set bodySnippet to (text 1 thru 140 of bodySnippet) & "..."
                        end if

                        if hideEmpty and draftSubject is "" and bodySnippet is "" then
                            -- skip orphaned blank draft
                        else
                            -- Recipients (Drafts is a small, bounded mailbox)
                            set draftTo to ""
                            try
                                set toAddrs to {{}}
                                repeat with aRecip in (to recipients of aDraft)
                                    try
                                        set end of toAddrs to (address of aRecip)
                                    end try
                                end repeat
                                set AppleScript's text item delimiters to ", "
                                set draftTo to toAddrs as string
                                set AppleScript's text item delimiters to ""
                            end try

                            set shownCount to shownCount + 1
                            set draftLines to draftLines & "✉ " & draftSubject & return
                            set draftLines to draftLines & "   Id: " & draftId & "   To: " & draftTo & return
                            set draftLines to draftLines & "   Created: " & (draftDate as string) & return
                            if bodySnippet is not "" then
                                set draftLines to draftLines & "   " & bodySnippet & return
                            end if
                            set draftLines to draftLines & return
                        end if
                    end try
                end repeat

            on error errMsg
                return "Error: " & errMsg
            end try

            return "DRAFT EMAILS - {safe_account}" & return & return & "Found " & shownCount & " draft(s)" & return & return & draftLines
        end tell
        '''

    elif action == "create":
        if not subject or not to or not body:
            return "Error: 'subject', 'to', and 'body' are required for creating drafts"

        thread_warning = _standalone_compose_thread_warning(subject, body, None, standalone_confirmed)
        if thread_warning:
            return thread_warning

        try:
            sender_override, sender_error = _validate_from_address(account, from_address, timeout=timeout)
        except AppleScriptTimeout:
            return (
                "Error: AppleScript timed out while validating sender for account "
                f"{account!r}. Try again or pass a larger `timeout`."
            )
        if sender_error:
            return sender_error

        escaped_subject = escape_applescript(subject)
        escaped_body = escape_applescript(body)
        safe_to = escape_applescript(to)

        sender_script = _compose_sender_script("newDraft", "targetAccount", sender_override)

        # Build TO recipients (split comma-separated)
        to_script = ""
        to_addresses = [addr.strip() for addr in to.split(",")]
        for addr in to_addresses:
            safe_addr = escape_applescript(addr)
            to_script += f'''
                    make new to recipient at end of to recipients with properties {{address:"{safe_addr}"}}
            '''

        # Build CC recipients if provided
        cc_script = ""
        if cc:
            cc_addresses = [addr.strip() for addr in cc.split(",")]
            for addr in cc_addresses:
                safe_addr = escape_applescript(addr)
                cc_script += f'''
                    make new cc recipient at end of cc recipients with properties {{address:"{safe_addr}"}}
                '''

        # Build BCC recipients if provided
        bcc_script = ""
        if bcc:
            bcc_addresses = [addr.strip() for addr in bcc.split(",")]
            for addr in bcc_addresses:
                safe_addr = escape_applescript(addr)
                bcc_script += f'''
                    make new bcc recipient at end of bcc recipients with properties {{address:"{safe_addr}"}}
                '''

        script = f'''
        tell application "Mail"
            set outputText to "CREATING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"

                -- Create new outgoing message (draft)
                set newDraft to make new outgoing message with properties {{subject:"{escaped_subject}", content:"{escaped_body}", visible:false}}

                {sender_script}

                -- Add recipients
                tell newDraft
                    {to_script}
                    {cc_script}
                    {bcc_script}
                end tell

                -- Save to drafts (don't send)
                -- The draft is automatically saved to Drafts folder

                set outputText to outputText & "✓ Draft created successfully!" & return & return
                set outputText to outputText & "Subject: {escaped_subject}" & return
                set outputText to outputText & "To: {safe_to}" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "send":
        if _server.READ_ONLY:
            return "Error: Sending drafts is disabled in read-only mode."
        if _server.DRAFT_SAFE:
            return "Error: Sending drafts is disabled in draft-safe mode."
        if not draft_subject:
            return "Error: 'draft_subject' is required for sending drafts"

        safe_draft_subject = escape_applescript(draft_subject)

        script = f'''
        tell application "Mail"
            set outputText to "SENDING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                {_build_draft_lookup(draft_subject)}

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft

                    -- Send the draft
                    send foundDraft

                    set outputText to outputText & "✓ Draft sent successfully!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return

                else
                    set outputText to outputText & "⚠ No draft found matching: {safe_draft_subject}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "open":
        if not draft_subject:
            return "Error: 'draft_subject' is required for opening drafts"

        safe_draft_subject = escape_applescript(draft_subject)

        script = f'''
        tell application "Mail"
            set outputText to "OPENING DRAFT FOR REVIEW" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                {_build_draft_lookup(draft_subject)}

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft

                    -- Open the draft in a visible compose window
                    set draftWindow to open foundDraft
                    activate

                    set outputText to outputText & "✓ Draft opened in Mail for review!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return
                    set outputText to outputText & return & "Edit and send when ready." & return

                else
                    set outputText to outputText & "⚠ No draft found matching: {safe_draft_subject}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "delete":
        if not draft_subject:
            return "Error: 'draft_subject' is required for deleting drafts"

        safe_draft_subject = escape_applescript(draft_subject)

        script = f'''
        tell application "Mail"
            set outputText to "DELETING DRAFT" & return & return

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                {_build_draft_lookup(draft_subject)}

                if foundDraft is not missing value then
                    set draftSubject to subject of foundDraft

                    -- Delete the draft
                    delete foundDraft

                    set outputText to outputText & "✓ Draft deleted successfully!" & return
                    set outputText to outputText & "Subject: " & draftSubject & return

                else
                    set outputText to outputText & "⚠ No draft found matching: {safe_draft_subject}" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    elif action == "cleanup_empty":
        if max_deletes < 1:
            return "Error: 'max_deletes' must be >= 1 for cleanup_empty"
        dry_run_flag = "true" if dry_run else "false"
        mode_label = "PREVIEW (dry run)" if dry_run else "DELETING"
        script = f'''
        tell application "Mail"
            set isDryRun to {dry_run_flag}
            set maxDeletes to {max_deletes}
            set reportLines to ""
            set emptyCount to 0
            set actedCount to 0

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set draftMessages to messages 1 thru {DRAFT_LIST_CAP} of draftsMailbox

                -- Collect empty drafts first (subject blank AND body empty), then
                -- act on them by reference so deletion does not shift indices.
                set emptyDrafts to {{}}
                repeat with aDraft in draftMessages
                    try
                        set draftSubject to subject of aDraft
                        set draftBody to ""
                        try
                            set draftBody to content of aDraft
                        end try
                        set AppleScript's text item delimiters to {{return, linefeed, tab, space}}
                        set bodyParts to text items of draftBody
                        set AppleScript's text item delimiters to ""
                        set bodyStripped to bodyParts as string
                        if draftSubject is "" and bodyStripped is "" then
                            set end of emptyDrafts to aDraft
                        end if
                    end try
                end repeat

                set emptyCount to count of emptyDrafts
                repeat with aDraft in emptyDrafts
                    if actedCount >= maxDeletes then exit repeat
                    try
                        set draftId to (id of aDraft) as string
                        if isDryRun then
                            set reportLines to reportLines & "   • would delete blank draft id " & draftId & return
                        else
                            delete aDraft
                            set reportLines to reportLines & "   • deleted blank draft id " & draftId & return
                        end if
                        set actedCount to actedCount + 1
                    end try
                end repeat

            on error errMsg
                return "Error: " & errMsg
            end try

            set reportHeader to "DRAFT CLEANUP - {safe_account} ({mode_label})" & return & return
            set reportSummary to "Found " & emptyCount & " blank draft(s); "
            if isDryRun then
                set reportSummary to reportSummary & "would remove " & actedCount & " (cap " & maxDeletes & "). Re-run with dry_run=False to delete."
            else
                set reportSummary to reportSummary & "deleted " & actedCount & " (cap " & maxDeletes & ")."
            end if
            return reportHeader & reportSummary & return & return & reportLines
        end tell
        '''

    else:
        return f"Error: Invalid action '{action}'. Use: list, create, send, open, delete, cleanup_empty"

    try:
        result = run_applescript(script) if timeout is None else run_applescript(script, timeout=timeout)
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out for manage_drafts action {action!r} on "
            f"account {account!r}. Try again or pass a larger `timeout`."
        )
    return result
