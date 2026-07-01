"""``create_rich_email_draft`` tool: build a standalone rich (HTML) draft via the Mail object model."""

import os
from email.message import EmailMessage
from pathlib import Path

from apple_mail_mcp.core import SENSITIVE_DIRS, AppleScriptTimeout, inject_preferences
from apple_mail_mcp.server import WRITE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.helpers import (
    _account_default_alias_if_single,
    _list_outgoing_message_ids,
    _resolve_account,
    _save_new_compose_window_as_draft,
    _validate_from_address,
)
from apple_mail_mcp.tools.compose.payload import (
    _default_rich_draft_path,
    _prepare_rich_bodies,
    _split_addresses,
    _standalone_compose_thread_warning,
    _strip_cdata_wrappers,
)


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
        open_in_mail: If True and the subject is nonblank, open the generated `.eml` in Mail and save the newly-opened compose window to Drafts (identified by an id diff against the compose windows open before this call, so a pre-existing draft window is never touched). Default: True. Blank-subject drafts are written as `.eml` only by default to avoid opening incomplete drafts. Pass False to only create the `.eml` file.
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
        # Snapshot the open compose windows BEFORE opening this draft so the
        # post-open save targets only the window we are about to create, never a
        # pre-existing unrelated compose window (the blind ``item 1 of outgoing
        # messages`` save reported false "Saved: yes" against the wrong window).
        prior_outgoing_ids = set(_list_outgoing_message_ids(timeout=timeout))
        try:
            compose.subprocess.run(["open", "-a", "Mail", str(draft_path)], check=True)
        except (compose.subprocess.CalledProcessError, FileNotFoundError) as exc:
            return (
                f"Error: Failed to open draft in Mail.app: {exc}. The .eml file was written but Mail could not open it."
            )
        opened = True
        try:
            saved = _save_new_compose_window_as_draft(
                prior_outgoing_ids=prior_outgoing_ids,
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
