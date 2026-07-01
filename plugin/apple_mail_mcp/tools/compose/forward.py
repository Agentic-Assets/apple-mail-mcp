"""``forward_email`` tool: forward an existing message with optional attachments."""

from pathlib import Path

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript, inject_preferences
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import _MESSAGE_ID_REQUIRED_ERROR
from apple_mail_mcp.tools.compose.helpers import (
    _check_open_compose_window_cap,
    _clean_applescript_error,
    _resolve_account,
    _resolve_signature_name,
    _validate_from_address,
)
from apple_mail_mcp.tools.compose.lookup_scripts import _build_found_message_lookup, _compose_signature_script
from apple_mail_mcp.tools.compose.payload import (
    _build_recipient_loops,
    _compose_sender_script,
    _split_addresses,
    _strip_cdata_wrappers,
)
from apple_mail_mcp.tools.compose.saved_draft_checks import _verify_saved_forward_draft
from apple_mail_mcp.tools.compose.verification import _extract_output_field


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
    Forward an email to one or more recipients by exact ``message_id``.

    ``subject_keyword`` is a deprecated selector retained for v3.x schema
    compatibility. Use ``search_emails(...)`` or ``list_inbox_emails(...)`` to
    discover candidate ids, then pass ``message_id``. Passing ``subject_keyword``
    without ``message_id`` returns ``TARGET_SELECTOR_DEPRECATED``.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_id`` is omitted.
        to: Recipient email address(es), comma-separated for multiple
        message: Optional message to add before forwarded content
        mailbox: Mailbox to search in (default: "INBOX")
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        from_address: Optional sender address to use when forwarding. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        mode: Delivery mode — "draft" (default, save quietly to Drafts), "open" (save first, then leave compose window open for review), or "send" (send immediately)
        message_id: Required. Exact numeric Apple Mail message id from
            ``search_emails`` or ``list_inbox_emails``.
        recent_days: Schema-compat parameter for deprecated subject_keyword path
            (default: 2.0 / 48h). Ignored when ``message_id`` is set.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to the standard 120s. Raise this when working with large mailboxes or slow accounts.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.

    Returns:
        Confirmation message with details of forwarded email
    """

    if not message_id and not subject_keyword:
        return _MESSAGE_ID_REQUIRED_ERROR
    if not to:
        return "Error: 'to' is required"
    if not message_id and subject_keyword:
        return target_selector_deprecated_error(
            "forward_email",
            ("subject_keyword",),
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_id.",
            discovery="search_emails(subject_keyword=..., recent_days=..., limit=...)",
            exact_selector="message_id",
        )

    account, account_error = _resolve_account(account, timeout=timeout)
    if account_error:
        return account_error
    assert account is not None  # _resolve_account guarantees non-None when error is None

    lookup_script, lookup_error = _build_found_message_lookup(
        "targetMailbox",
        message_id=message_id,
        subject_keyword=None,
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
    blocked = compose._send_blocked(mode)
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
    safe_to = escape_applescript(to)
    safe_mailbox = escape_applescript(mailbox)
    not_found_message = f"Error: No email found for message_id={message_id}"

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

    # Optional leading message is composed as plain text via the object model
    # (no clipboard, no System Events keystroke). Write it to a temp file so
    # special characters survive without AppleScript escaping headaches.
    fwd_msg_temp_path = None
    fwd_read_script = 'set fwdLeadText to ""'
    fwd_cleanup_script = ""
    if message:
        with compose.tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix="mail_fwd_",
            delete=False,
            encoding="utf-8",
        ) as fwd_msg_tmp:
            fwd_msg_tmp.write(message)
            fwd_msg_temp_path = fwd_msg_tmp.name
        fwd_read_script = (
            f'set fwdLeadText to (do shell script "cat " & quoted form of "{fwd_msg_temp_path}") & return & return'
        )
        fwd_cleanup_script = f'do shell script "rm -f " & quoted form of "{fwd_msg_temp_path}"'

    visible_lower = "true" if mode == "open" else "false"
    if mode == "send":
        header_text = "FORWARDING EMAIL"
        post_forward_action = "send forwardMessage"
        success_text = "Email forwarded successfully."
    elif mode == "open":
        header_text = "OPENING FORWARD FOR REVIEW"
        post_forward_action = "save forwardMessage\n            activate"
        success_text = "Forward opened in Mail for review. Edit and send when ready."
    else:
        header_text = "SAVING FORWARD AS DRAFT"
        post_forward_action = "save forwardMessage"
        success_text = "Forward saved as draft."

    draft_id_capture_script = ""
    draft_id_output_script = ""
    if mode in {"draft", "open"}:
        draft_id_capture_script = """
        set forwardDraftId to ""
        try
            set forwardDraftId to id of forwardMessage as string
        end try
        """
        draft_id_output_script = """
        if forwardDraftId is not "" then set outputText to outputText & "Draft ID: " & forwardDraftId & return
        """

    script = f'''
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

        if foundMessage is missing value then
            return "{not_found_message}"
        end if

        set origSubject to subject of foundMessage
        set origSender to sender of foundMessage
        set origDate to ""
        try
            set origDate to (date received of foundMessage) as string
        end try
        set origContent to ""
        try
            set origContent to content of foundMessage
        end try
        if (count of characters of origContent) > 4000 then
            set origContent to (text 1 thru 4000 of origContent) & return & "[... forwarded original truncated ...]"
        end if

        {fwd_read_script}

        -- Build forwarded body: optional lead message + forwarded header + quoted original
        set fwdHeader to "---------- Forwarded message ----------" & return
        set fwdHeader to fwdHeader & "From: " & origSender & return
        set fwdHeader to fwdHeader & "Subject: " & origSubject & return
        set fwdHeader to fwdHeader & "Date: " & origDate & return & return
        set fullBody to fwdLeadText & fwdHeader & origContent

        set fwdSubject to origSubject
        if fwdSubject does not start with "Fwd:" then set fwdSubject to "Fwd: " & fwdSubject

        -- Object-model draft: NO window, NO clipboard, NO System Events
        set forwardMessage to make new outgoing message with properties {{visible:{visible_lower}, subject:fwdSubject, content:fullBody}}

        {sender_script}
        {signature_script}

        -- Add recipients
        {to_script}

        -- Add CC/BCC recipients
        {cc_script}
        {bcc_script}

        {post_forward_action}

        {draft_id_capture_script}

        -- Clean up temp file
        {fwd_cleanup_script}

        set outputText to outputText & "{success_text}" & return
        set outputText to outputText & "To: {safe_to}" & return
        set outputText to outputText & "Subject: " & fwdSubject & return
        {draft_id_output_script}
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
        return outputText
    on error errMsg
        try
            {fwd_cleanup_script}
        end try
        return "Error: " & errMsg
    end try
    end tell
    """

    try:
        result = (
            compose.run_applescript(script) if timeout is None else compose.run_applescript(script, timeout=timeout)
        )
        if mode in ("draft", "open") and success_text in result:
            draft_id = _extract_output_field(result, "Draft ID")
            forward_subject = _extract_output_field(result, "Subject")
            expected_signature = False if not include_signature else (resolved_signature_name is not None)
            result += _verify_saved_forward_draft(
                account,
                draft_id=draft_id,
                to=to,
                subject=forward_subject,
                lead_message=message,
                expected_signature=expected_signature,
                timeout=timeout,
            )
        return result
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while forwarding email for account "
            f"{account!r}. Try again or pass a larger `timeout`."
        )
    except Exception as e:
        if not message:
            raise
        return f"Error: Forward failed: {_clean_applescript_error(e)}"
    finally:
        if fwd_msg_temp_path:
            fwd_msg_path = Path(fwd_msg_temp_path)
            if fwd_msg_path.exists():
                fwd_msg_path.unlink()
