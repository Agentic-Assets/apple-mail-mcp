"""``reply_to_email`` tool: native or object-model replies with optional windowless fallback."""

import json
from contextlib import suppress
from pathlib import Path

from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inject_preferences,
)
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import (
    _MESSAGE_ID_REQUIRED_ERROR,
)
from apple_mail_mcp.tools.compose.helpers import (
    _check_open_compose_window_cap,
    _clean_applescript_error,
    _resolve_account,
    _resolve_signature_name,
    _validate_from_address,
    _validate_signature_name,
)
from apple_mail_mcp.tools.compose.lookup_scripts import (
    _build_found_message_lookup,
)
from apple_mail_mcp.tools.compose.payload import (
    _build_recipient_loops,
    _compose_sender_script,
    _strip_cdata_wrappers,
)
from apple_mail_mcp.tools.compose.reply_scripts import (
    _build_reply_native_window_applescript,
    _build_reply_objectmodel_applescript,
    _reply_command_options,
    _reply_mode_plan,
    _reply_signature_script,
)
from apple_mail_mcp.tools.compose.saved_draft_checks import _verify_saved_reply_draft
from apple_mail_mcp.tools.compose.verification import (
    _extract_output_field,
    _format_reply_verification_lines,
    _reply_draft_verification_error,
    _reply_success_payload,
)


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
    output_format: str = "text",
    native_format: bool = True,
    allow_windowless_fallback: bool = False,
) -> str:
    """
    Reply to an email by exact ``message_id`` using Mail's native reply window.
    Native drafting (``native_format=True``, the default) is the only supported
    path; the windowless ``native_format=False`` fallback is gated and returns
    ``WINDOWLESS_FALLBACK_DISABLED`` unless ``allow_windowless_fallback=True`` is
    passed. Agents must never use the fallback.

    ``subject_keyword`` is a deprecated selector retained for v3.x schema
    compatibility. Use ``search_emails(...)`` or ``list_inbox_emails(...)`` to
    discover candidate ids, then pass ``message_id``. Passing ``subject_keyword``
    without ``message_id`` returns ``TARGET_SELECTOR_DEPRECATED``.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to `DEFAULT_MAIL_ACCOUNT` env var if `account` is omitted.
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_id`` is omitted.
        reply_body: The body text of the reply
        reply_to_all: If True, reply to all recipients; if False, reply only to sender (default: False)
        cc: Optional CC recipients, comma-separated for multiple
        bcc: Optional BCC recipients, comma-separated for multiple
        send: If True, send immediately; if False (default), save as draft. Ignored if mode is set.
        mode: Delivery mode — "draft" (default, save quietly to Drafts and close), "open" (save first, then leave compose window open for review), or "send" (send immediately). Overrides send parameter when set.
        attachments: Optional file paths to attach, comma-separated for multiple (e.g., "/path/to/file1.png,/path/to/file2.pdf")
        body_html: Accepted for backwards compatibility but ignored. Replies use Mail's native reply composer and
            insert reply_body as plain text above Mail's native quoted thread.
        from_address: Optional sender address to use for this reply. Must be one of the account's configured email addresses. When omitted, Mail uses the account's default "Send new messages from" setting.
        message_id: Required. Exact numeric Apple Mail message id from
            ``search_emails`` or ``list_inbox_emails``.
        recent_days: Schema-compat parameter for deprecated subject_keyword path
            (default: 2.0 / 48h). Ignored when ``message_id`` is set.
        timeout: Optional per-AppleScript timeout in seconds. Defaults to 120s for the main reply script and up to 30s for alias validation.
        include_signature: Whether to apply the configured/default Mail signature (default: True).
        signature_name: Optional Mail signature name; falls back to DEFAULT_MAIL_SIGNATURE when omitted.
        output_format: "text" (default) preserves the existing success output.
            "json" returns machine-readable draft/open success metadata after
            saved-draft verification succeeds.
        native_format: When True (default), compose the reply in Mail's native reply
            window so the draft keeps Mail's colored quote bar and the account's
            default reply signature (with logo), inserting reply_body with a typed
            keystroke above the quote. This needs the Mail window to take focus and
            Accessibility permission for the host process. When False, compose the
            reply through the object model with no window (headless/bulk-safe, no
            Accessibility needed); the quote and signature are flattened to plain
            text. ``native_format=False`` is gated: it returns
            ``WINDOWLESS_FALLBACK_DISABLED`` unless ``allow_windowless_fallback=True``
            is also passed, so agents cannot drift into the fallback path.
        allow_windowless_fallback: Explicit ack required to use the windowless
            ``native_format=False`` path. Defaults to False; agents must never set
            it. Exists for deliberate headless/bulk/CI reply runs only.

    Returns:
        Confirmation message with details of the reply sent, saved draft, or opened draft
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if not native_format and not allow_windowless_fallback:
        return serialize_tool_error(
            ToolError(
                code="WINDOWLESS_FALLBACK_DISABLED",
                message=(
                    "The windowless reply path (native_format=False) is disabled by "
                    "default; native reply drafting (native_format=True) is the only "
                    "supported path for normal use because it preserves Mail's rich "
                    "quote bar and logo signature."
                ),
                remediation={
                    "preferred": (
                        "Retry with native_format=True (the default) and Mail visible so "
                        "the reply window can take focus. On REPLY_WINDOW_FOCUS_FAILED no "
                        "draft was saved: retry with Mail visible and not being clicked."
                    ),
                    "headless_only": (
                        "The windowless path is reserved for deliberate headless/bulk/CI "
                        "runs with no GUI focus. If that is actually your situation, pass "
                        "allow_windowless_fallback=True with native_format=False. Agents "
                        "must never set this flag on their own."
                    ),
                },
            )
        )

    if not message_id and not subject_keyword:
        return _MESSAGE_ID_REQUIRED_ERROR
    if not message_id and subject_keyword:
        return target_selector_deprecated_error(
            "reply_to_email",
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
        "inboxMailbox",
        message_id=message_id,
        subject_keyword=None,
        recent_days=recent_days,
        messages_var="inboxMessages",
        tool_name="reply_to_email",
    )
    if lookup_error:
        if isinstance(lookup_error, ToolError):
            return serialize_tool_error(lookup_error)
        return lookup_error

    reply_body = _strip_cdata_wrappers(reply_body) or ""
    # body_html is accepted for backwards compatibility only and is ignored:
    # replies use Mail's native reply composer so quoted chains preserve Mail's
    # normal formatting; reply_body is inserted as plain text above that quote.

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
    signature_error = _validate_signature_name(resolved_signature_name, timeout=timeout)
    if signature_error:
        return signature_error

    # Resolve delivery mode before creating temp files so early contract errors
    # leave no local artifacts behind.
    if mode is not None:
        if mode not in ("send", "draft", "open"):
            return f"Error: Invalid mode '{mode}'. Use: send, draft, open"
        effective_mode = mode
    else:
        effective_mode = "send" if send else "draft"

    blocked = compose._send_blocked(effective_mode)
    if blocked:
        return blocked
    if output_format == "json" and effective_mode == "send":
        return "Error: output_format='json' is only supported for mode='draft' or mode='open'."

    if effective_mode == "open":
        cap_err = _check_open_compose_window_cap()
        if cap_err:
            return cap_err

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    not_found_message = f"Error: No email found for message_id={message_id}"

    # Write reply body to a temp file to avoid AppleScript string escaping
    # issues with special characters (em dashes, curly quotes, colons, etc.)
    with compose.tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="mail_reply_",
        delete=False,
        encoding="utf-8",
    ) as body_tmp:
        body_tmp.write(reply_body)
        body_temp_path = body_tmp.name

    cc_script, bcc_script, _, _ = _build_recipient_loops(cc, bcc, message_var="replyMessage")

    # Build attachment script if provided (object model: attach to replyMessage)
    attachment_script = ""
    attachment_info = ""
    validated_paths: list[str] = []
    if attachments:
        validated_paths, error = compose._validate_attachment_paths(attachments)
        if error:
            with suppress(OSError):
                Path(body_temp_path).unlink(missing_ok=True)
            return error
        for path in validated_paths:
            safe_path = escape_applescript(path)
            attachment_script += f'''
                set theFile to POSIX file "{safe_path}"
                tell replyMessage
                    make new attachment with properties {{file name:theFile}} at after the last paragraph of content
                end tell
                delay 1
            '''
            attachment_info += f"  {path}\n"

    safe_cc = escape_applescript(cc) if cc else ""
    safe_bcc = escape_applescript(bcc) if bcc else ""
    safe_attachment_info = escape_applescript(attachment_info) if attachment_info else ""

    mode_plan = _reply_mode_plan(effective_mode)

    cleanup_script = f'do shell script "rm -f " & quoted form of "{body_temp_path}"'

    signature_script = _reply_signature_script(resolved_signature_name, include_signature=include_signature)

    if native_format:
        # Native reply: let Mail own the reply identity and its default signature
        # (with logo). Only set the sender when the caller explicitly overrode it;
        # never pin the account's single alias here — changing the From on the open
        # reply window makes Mail re-insert a text-only signature and drop the
        # embedded logo image (the saved draft loses the logo).
        native_sender_script = (
            f'set sender of replyMessage to "{escape_applescript(sender_override)}"' if sender_override else ""
        )
        # Always open the reply window so Mail renders its native rich quote +
        # signature; the body is typed in. Reuse the "open" option string only for
        # the "with opening window [and reply to all]" wording, independent of mode.
        native_reply_options, _ = _reply_command_options("open", reply_to_all)
        script = _build_reply_native_window_applescript(
            header_text=mode_plan.header_text,
            success_text=mode_plan.success_text,
            safe_account=safe_account,
            lookup_script=lookup_script,
            not_found_message=not_found_message,
            body_temp_path=body_temp_path,
            reply_options=native_reply_options,
            sender_script=native_sender_script,
            signature_script=signature_script,
            cc_script=cc_script,
            bcc_script=bcc_script,
            attachment_script=attachment_script,
            mode=effective_mode,
            cleanup_script=cleanup_script,
            safe_cc=safe_cc,
            safe_bcc=safe_bcc,
            safe_attachment_info=safe_attachment_info,
            has_cc=bool(cc),
            has_bcc=bool(bcc),
            has_attachments=bool(attachments),
        )
    else:
        # Object-model path (no window): pin the single alias so the headless draft
        # still sends from the account's own address, since there is no native reply
        # window to inherit the identity from.
        objectmodel_sender_script = _compose_sender_script("replyMessage", "targetAccount", sender_override)
        reply_options, reply_settle_delay = _reply_command_options(effective_mode, reply_to_all)
        script = _build_reply_objectmodel_applescript(
            header_text=mode_plan.header_text,
            success_text=mode_plan.success_text,
            safe_account=safe_account,
            lookup_script=lookup_script,
            not_found_message=not_found_message,
            body_temp_path=body_temp_path,
            reply_options=reply_options,
            reply_settle_delay=reply_settle_delay,
            sender_script=objectmodel_sender_script,
            signature_script=signature_script,
            cc_script=cc_script,
            bcc_script=bcc_script,
            attachment_script=attachment_script,
            post_action=mode_plan.post_action,
            cleanup_script=cleanup_script,
            safe_cc=safe_cc,
            safe_bcc=safe_bcc,
            safe_attachment_info=safe_attachment_info,
            has_cc=bool(cc),
            has_bcc=bool(bcc),
            has_attachments=bool(attachments),
        )

    try:
        result = (
            compose.run_applescript(script) if timeout is None else compose.run_applescript(script, timeout=timeout)
        )
        if result.startswith("GUARD_ABORT"):
            guard_reply_subject = _extract_output_field(result, "Subject") or ""
            derived_reply_subject = _extract_output_field(result, "DerivedSubject") or ""
            guard_verification = _verify_saved_reply_draft(
                account,
                guard_reply_subject or derived_reply_subject,
                reply_body,
                draft_id=None,
                quoted_needle="wrote:",
                signature_requested=None,
                timeout=timeout,
            )
            suspected_artifact_id = (
                guard_verification.matched_artifact_id
                or guard_verification.body_missing_artifact_id
                or guard_verification.error_artifact_id
            )
            artifact_status = guard_verification.status
            subject_mismatch = result.startswith("GUARD_ABORT_SUBJECT")
            if subject_mismatch:
                return serialize_tool_error(
                    ToolError(
                        code="REPLY_SUBJECT_GUARD_MISMATCH",
                        message=(
                            "Native reply opened a compose window, but the window title did not "
                            "match the expected reply subject after Mail subject normalization, "
                            "so the body was not typed and no email was sent."
                        ),
                        remediation={
                            "preferred": (
                                "Retry once with Mail visible. If this persists, report the "
                                "Subject / DerivedSubject / mailFront values from detail; Mail "
                                "may have normalized the subject differently than expected."
                            ),
                            "alternative": (
                                "Do not switch off native formatting. Inspect or delete any "
                                "empty compose window left open, then retry native_format=True."
                            ),
                            "expected_subject": guard_reply_subject or derived_reply_subject,
                            "derived_subject": derived_reply_subject or None,
                            "draft_artifact_status": artifact_status,
                            "suspected_draft_id": suspected_artifact_id,
                            "cleanup": (
                                "If suspected_draft_id is present, inspect or delete that exact "
                                "Drafts artifact with verify_draft or "
                                "manage_drafts(action='delete', draft_id=...)."
                            ),
                            "detail": result,
                        },
                    )
                )
            return serialize_tool_error(
                ToolError(
                    code="REPLY_WINDOW_FOCUS_FAILED",
                    message=(
                        "Native reply could not bring the reply window into focus to type the "
                        "body, so the intended reply body was not safely saved and no email was sent."
                    ),
                    remediation={
                        "preferred": (
                            "Retry with Mail visible and not being clicked; native replies type "
                            "into the reply window and need it to hold focus for a moment."
                        ),
                        "alternative": (
                            "Do not switch off native formatting. Retry with native_format=True "
                            "(the default) once Mail can take focus. If focus still cannot be "
                            "acquired, stop and report the blocker."
                        ),
                        "draft_artifact_status": artifact_status,
                        "suspected_draft_id": suspected_artifact_id,
                        "cleanup": (
                            "If suspected_draft_id is present, inspect or delete that exact Drafts "
                            "artifact with verify_draft or manage_drafts(action='delete', draft_id=...)."
                        ),
                        "detail": result,
                    },
                )
            )
        if effective_mode in ("draft", "open") and mode_plan.success_text in result:
            reply_subject = _extract_output_field(result, "Subject")
            draft_id = _extract_output_field(result, "Draft ID")
            quoted_needle = _extract_output_field(result, "Quote Needle")
            # The native window inherits Mail's own default reply signature (with logo),
            # whose rich text we never set and cannot reliably substring-match. Only
            # assert a signature when one was explicitly requested by name; otherwise
            # skip the check so the native default signature is not flagged "missing".
            signature_requested_for_verify: bool | None = include_signature
            if native_format and include_signature and not resolved_signature_name:
                signature_requested_for_verify = None
            verification = _verify_saved_reply_draft(
                account,
                reply_subject or "",
                reply_body,
                draft_id=draft_id,
                quoted_needle=quoted_needle,
                expected_attachment_count=len(validated_paths) if validated_paths else None,
                expected_attachment_names=[Path(path).name for path in validated_paths],
                signature_requested=signature_requested_for_verify,
                expected_signature_name=resolved_signature_name,
                timeout=timeout,
            )
            if not verification.ok:
                mode_text = "opened" if effective_mode == "open" else "created"
                return _reply_draft_verification_error(
                    verification,
                    mode_text=mode_text,
                    reply_body=reply_body,
                )
            if output_format == "json":
                return json.dumps(
                    _reply_success_payload(
                        mode=effective_mode,
                        reply_subject=reply_subject,
                        draft_id=draft_id,
                        verification=verification,
                    )
                )
            result += _format_reply_verification_lines(verification, draft_id)
        return result
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while replying on account {account!r}. Try again or pass a larger `timeout`."
        )
    except Exception as e:
        return f"Error: Reply failed: {_clean_applescript_error(e)}"
    finally:
        # Belt-and-suspenders cleanup in case AppleScript didn't run
        with suppress(OSError):
            Path(body_temp_path).unlink(missing_ok=True)
