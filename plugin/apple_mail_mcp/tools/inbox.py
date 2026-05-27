"""Inbox tools: listing, counting, and overview."""

import asyncio
import json
from typing import Optional, List, Dict, Any, Tuple, Union

from apple_mail_mcp import server as _server
from apple_mail_mcp.server import mcp, READ_ONLY_TOOL_ANNOTATIONS
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    inject_preferences,
    escape_applescript,
    fetch_replied_ids as _core_fetch_replied_ids,
    run_applescript,
    inbox_mailbox_script,
    content_preview_script,
    sanitize_pipe_delimited_field,
    validate_account_name,
    account_not_found_json,
)
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.bounded_scan import (
    build_bounded_message_scan,
    build_bounded_filtered_scan,
)
from apple_mail_mcp.constants import SCAN_BOUNDS


_VALID_READ_FILTERS = ("all", "read", "unread")


def _resolve_read_filter(
    read_status: Optional[str],
    include_read: bool,
) -> str:
    """Map the public ``read_status``/``include_read`` pair to an internal filter.

    Returns one of ``"all"``, ``"read"``, ``"unread"``. ``read_status``
    wins when provided; otherwise ``include_read`` is interpreted as
    ``True → "all"``, ``False → "unread"`` (the legacy bool semantics).
    Raises ``ValueError`` for an unknown ``read_status``.
    """
    if read_status is not None:
        if read_status not in _VALID_READ_FILTERS:
            raise ValueError(
                f"read_status must be one of {_VALID_READ_FILTERS}; got {read_status!r}"
            )
        return read_status
    return "all" if include_read else "unread"


def _read_filter_condition(read_filter: str) -> Optional[str]:
    """Return the per-message AppleScript predicate for *read_filter*.

    Returns ``None`` for the no-filter case (``"all"``). The predicate
    references ``aMessage`` and is intended for use inside an in-loop
    ``if`` — never as the body of a ``whose`` clause over a bound slice
    (which crashes on Gmail; see ``bounded_scan.build_bounded_filtered_scan``).
    """
    if read_filter == "unread":
        return "read status of aMessage is false"
    if read_filter == "read":
        return "read status of aMessage is true"
    return None


def fetch_replied_ids(account: str, sent_cap: int = 200, timeout: Optional[int] = 60) -> set:
    """Fetch replied Message-ID set using this module's ``run_applescript``.

    Wraps the core helper so tests that patch
    ``apple_mail_mcp.tools.inbox.run_applescript`` also cover the
    Sent-mailbox probe.
    """
    return _core_fetch_replied_ids(account, sent_cap=sent_cap, timeout=timeout, runner=run_applescript)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _list_accounts_script() -> str:
    """Tiny AppleScript that returns one Mail account name per line."""
    return '''
    tell application "Mail"
        set acctNames to {}
        repeat with anAccount in (every account)
            set end of acctNames to (name of anAccount)
        end repeat
        set AppleScript's text item delimiters to linefeed
        return acctNames as string
    end tell
    '''


def _list_mail_accounts(timeout: Optional[int] = 30) -> List[str]:
    """Return the list of Mail account names (cheap; under 1s)."""
    raw = run_applescript(_list_accounts_script(), timeout=timeout)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _parse_pipe_delimited_emails(
    raw: str, *, has_message_id: bool = False
) -> List[Dict[str, Any]]:
    """Parse '|||'-delimited AppleScript output into a list of email dicts.

    Current schema (6 or 7 or 8 fields):
        subject|||sender|||date|||read|||account|||mail_app_id[|||internet_message_id][|||content_preview]

    ``mail_app_id`` (the integer Mail.app ``id`` property) is always present
    and emitted as ``"message_id"`` to match the ``search_emails`` record shape.

    Extended schema when *has_message_id* is True (7 or 8 fields):
        subject|||sender|||date|||read|||account|||mail_app_id|||internet_message_id[|||content_preview]

    **Field-count validation:** the AppleScript side runs
    ``sanitize_pipe_delimited_field`` on ``messageSubject`` and
    ``messageSender`` to strip the ``|||`` sequence before emission. This
    parser ALSO defensively checks the field count — if a row produces
    too few fields (e.g. a hand-crafted test fixture or a Mail return
    we didn't anticipate), the ``mail_app_id`` would land on the wrong
    column and a subsequent ``manage_trash(action="delete_permanent")``
    could delete the wrong message. Rows that don't validate are dropped.
    """
    emails: List[Dict[str, Any]] = []
    if not raw:
        return emails
    # Fields: subject(0) sender(1) date(2) read(3) account(4) mail_app_id(5)
    #         [internet_message_id(6)] [content_preview(7)]
    # maxsplit = field_count - 1 so the LAST field (content_preview, or
    # internet_message_id when content is absent) keeps any literal ||| inside
    # it intact (content previews legitimately contain pipe sequences). The
    # *earlier* user-controlled fields (subject, sender) are sanitized on the
    # AppleScript side via `sanitize_pipe_delimited_field` before emission;
    # any sanitizer escape that slipped through would land a non-numeric
    # value in the mail_app_id slot (parts[5]) — the isdigit() check below
    # rejects those rows rather than risk mapping the wrong id onto a
    # downstream destructive op.
    maxsplit = 7 if has_message_id else 6
    for line in raw.split("\n"):
        if "|||" not in line:
            continue
        parts = line.split("|||", maxsplit)
        if len(parts) < 6:
            continue
        mail_app_id = parts[5].strip()
        # mail_app_id must be a Mail integer id. If it's empty (transient sync
        # failure) or not all digits (parser corruption from leaked ||| in a
        # subject — sanitizer escape hatch), drop the row rather than risk
        # mapping the wrong id onto a downstream destructive op.
        if not mail_app_id or not mail_app_id.isdigit():
            continue
        item: Dict[str, Any] = {
            "subject": parts[0].strip(),
            "sender": parts[1].strip(),
            "date": parts[2].strip(),
            "is_read": parts[3].strip().lower() == "true",
            "account": parts[4].strip(),
            "message_id": mail_app_id,
        }
        if has_message_id:
            if len(parts) >= 7 and parts[6].strip():
                item["internet_message_id"] = parts[6].strip()
            if len(parts) >= 8 and parts[7].strip():
                item["content_preview"] = parts[7].strip()
        else:
            if len(parts) >= 7 and parts[6].strip():
                item["content_preview"] = parts[6].strip()
        emails.append(item)
    return emails


# ---------------------------------------------------------------------------
# list_inbox_emails — async, per-account dispatch
# ---------------------------------------------------------------------------

def _build_inbox_collection_block(max_emails: int, read_filter: str) -> str:
    """Build the AppleScript that sets ``inboxMessages`` to a bounded slice.

    For the filtered modes (``read_filter`` ∈ ``"read"`` / ``"unread"``)
    bind a bounded newest-first window via ``messages 1 thru N``, then
    iterate in AppleScript with an ``if`` predicate via
    ``build_bounded_filtered_scan``. The historical ``whose read status
    is false`` over the bound slice crashed on Gmail (the slice's message
    refs point at ``[Gmail]/All Mail``, which ``whose`` can't resolve as
    a list query) — the in-loop pattern is the only safe form.
    """
    condition = _read_filter_condition(read_filter)
    if condition is not None:
        scan_cap = min(
            max(max_emails * 10, SCAN_BOUNDS["INBOX_DEFAULT_CAP"] // 2),
            SCAN_BOUNDS["INBOX_MAX_CAP"],
        )
        return build_bounded_filtered_scan(
            mailbox_var="inboxMailbox",
            scan_cap=scan_cap,
            target_max=max_emails,
            condition_expr=condition,
            output_var="inboxMessages",
        )
    bounded = build_bounded_message_scan("inboxMailbox", max_emails)
    return (
        f'{bounded}\n'
        f'            set inboxMessages to candidateMessages'
    )


def _build_list_inbox_text_script(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool,
    include_message_id: bool = False,
) -> str:
    """Build a text-format inbox script for one account.

    *read_filter* selects ``"all"`` (no filter), ``"unread"``, or
    ``"read"``. The filtered modes bind a bounded newest-first window
    (``scan_cap = min(max(max_emails*10, 100), 1000)``) and apply the
    predicate via an in-loop ``if`` (``build_bounded_filtered_scan``) —
    safe on Gmail and on 24K-message Exchange inboxes alike.
    """
    assert max_emails > 0, "caller must enforce bounded slice (max_emails > 0)"
    escaped_account = escape_applescript(account)
    message_id_text_block = ""
    if include_message_id:
        message_id_text_block = (
            'set internetMessageId to ""\n'
            "                        try\n"
            "                            set internetMessageId to message id of aMessage\n"
            "                        end try\n"
            '                        set outputText to outputText & "__MSG_ID__|||" & internetMessageId & return'
        )

    collection = _build_inbox_collection_block(max_emails, read_filter)

    return f"""
    tell application "Mail"
        set outputText to ""
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount
            {inbox_mailbox_script("inboxMailbox", "anAccount")}
            {collection}
            set messageCount to count of inboxMessages

            if messageCount > 0 then
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return
                set outputText to outputText & "📧 ACCOUNT: " & accountName & " (" & messageCount & " messages)" & return
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return & return

                set currentIndex to 0
                set sentCount to 0
                repeat with aMessage in inboxMessages
                    set currentIndex to currentIndex + 1
                    if currentIndex > {max_emails} then exit repeat

                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        set messageRead to read status of aMessage
                        -- Strip ||| and embedded newlines from subject/sender so
                        -- text-format parser markers (__MSG_ID__|||, __COUNT__|||)
                        -- and the replied-detection line walk stay aligned.
                        {sanitize_pipe_delimited_field("messageSubject")}
                        {sanitize_pipe_delimited_field("messageSender")}
                        {message_id_text_block}

                        if messageRead then
                            set readIndicator to "✓"
                        else
                            set readIndicator to "✉"
                        end if

                        set outputText to outputText & readIndicator & " " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return

                        {content_preview_script(200) if include_content else ""}

                        set outputText to outputText & return
                        set sentCount to sentCount + 1
                    end try
                end repeat
                set outputText to outputText & "__COUNT__|||" & sentCount & return
            end if
        on error errMsg
            set outputText to outputText & "⚠ Error accessing inbox for account {escaped_account}" & return & "   " & errMsg & return & return
        end try

        return outputText
    end tell
    """


def _build_list_inbox_json_script(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool = False,
    include_message_id: bool = False,
) -> str:
    """Build a JSON-format inbox script for one account.

    Each emitted line always includes the integer Mail.app ``id`` of the
    message (field index 5, exposed as ``"message_id"`` by the parser) so
    callers can pass it directly to ``get_email_by_id``.

    When *include_message_id* is True, an extra
    ``|||<internet-message-id>`` field (RFC 2822 Message-ID) is appended
    after the integer id for replied-detection.

    *read_filter* selects ``"all"`` / ``"read"`` / ``"unread"``; the
    filtered modes use the in-loop ``if`` pattern from
    ``build_bounded_filtered_scan`` (safe on Gmail and large Exchange).
    """
    assert max_emails > 0, "caller must enforce bounded slice (max_emails > 0)"
    escaped_account = escape_applescript(account)

    collection = _build_inbox_collection_block(max_emails, read_filter)

    if include_content:
        content_field = (
            "set contentPreview to \"\"\n"
            "                    try\n"
            "                        set msgContent to content of aMessage\n"
            "                        set AppleScript's text item delimiters to {return, linefeed, tab}\n"
            "                        set contentParts to text items of msgContent\n"
            "                        set AppleScript's text item delimiters to \" \"\n"
            "                        set contentPreview to contentParts as string\n"
            "                        set AppleScript's text item delimiters to \"\"\n"
            "                        if length of contentPreview > 200 then\n"
            "                            set contentPreview to text 1 thru 200 of contentPreview\n"
            "                        end if\n"
            "                    end try"
        )
        content_suffix = ' & "|||" & contentPreview'
    else:
        content_field = ""
        content_suffix = ""

    if include_message_id:
        message_id_field = (
            "set internetMessageId to \"\"\n"
            "                    try\n"
            "                        set internetMessageId to message id of aMessage\n"
            "                    end try"
        )
        message_id_suffix = ' & "|||" & internetMessageId'
    else:
        message_id_field = ""
        message_id_suffix = ""

    return f"""
    tell application "Mail"
        set resultLines to {{}}
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount
            {inbox_mailbox_script("inboxMailbox", "anAccount")}
            {collection}
            set currentIndex to 0
            repeat with aMessage in inboxMessages
                set currentIndex to currentIndex + 1
                if currentIndex > {max_emails} then exit repeat
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage
                    -- Wrap id read in its own try so a transient failure during
                    -- sync doesn't drop the entire row (matches search_emails
                    -- sanitize_field fallback behaviour).
                    set mailAppId to ""
                    try
                        set mailAppId to id of aMessage
                    end try
                    -- Strip ||| and embedded newlines from subject/sender so the
                    -- Python parser can't be confused into mapping the wrong
                    -- message_id onto an email (would lose data on delete).
                    {sanitize_pipe_delimited_field("messageSubject")}
                    {sanitize_pipe_delimited_field("messageSender")}
                    {content_field}
                    {message_id_field}
                    set end of resultLines to messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & accountName & "|||" & mailAppId{message_id_suffix}{content_suffix}
                end try
            end repeat
        end try
        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    """


def _strip_count_marker(raw: str) -> Tuple[str, int]:
    """Split out the `__COUNT__|||N` marker line if present.

    Returns (clean_text_without_marker, count). Count defaults to 0 when
    no marker is present (e.g. an empty-inbox account).
    """
    if not raw:
        return "", 0
    lines = raw.splitlines()
    count = 0
    kept: List[str] = []
    for line in lines:
        if line.startswith("__COUNT__|||"):
            try:
                count = int(line.split("|||", 1)[1])
            except (IndexError, ValueError):
                count = 0
        else:
            kept.append(line)
    return "\n".join(kept), count


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def list_inbox_emails(
    account: Optional[str] = None,
    all_accounts: bool = False,
    max_emails: int = 50,
    read_status: Optional[str] = None,
    include_read: bool = True,
    include_content: bool = False,
    output_format: str = "text",
    exclude_replied: bool = False,
    flag_replied: bool = False,
    timeout: Optional[int] = None,
    limit: Optional[int] = None,
    unread_only: Optional[bool] = None,
) -> Union[str, Dict[str, Any]]:
    """Defaults to 50 most-recent emails from the default account.

    List all emails from inbox across all accounts or a specific account.

    If you need every message in the inbox, use ``full_inbox_export`` instead.

    Smart defaults:
        - When `account` is None and `all_accounts` is False, the tool falls
          back to the ``DEFAULT_MAIL_ACCOUNT`` env-configured account if one
          is set. Pass `all_accounts=True` to opt back into multi-account
          dispatch even when a default is configured.
        - `max_emails` defaults to 50. `max_emails=0` is rejected with
          ``UNBOUNDED_SCAN_REQUIRED`` — unbounded inbox walks belong in
          ``full_inbox_export``.

    Performance guidance:
        - On multi-account setups with a 10K+ Exchange/Gmail inbox, prefer
          passing an explicit `account` plus a small `max_emails` (e.g. 20)
          — multi-account calls now fan out in parallel, but the slowest
          account still bounds the wall time.
        - Read-status filtering binds a bounded newest-first slice and
          applies the predicate in an AppleScript ``repeat`` loop (the
          ``build_bounded_filtered_scan`` helper). This is the only safe
          form on Gmail/IMAP accounts; the historical ``whose read status
          is false`` clause crashed on Gmail because the slice's message
          refs span ``[Gmail]/All Mail``.
        - When one account times out, the call returns partial data for the
          other accounts plus an `errors` field listing the slow account(s).

    Args:
        account: Optional account name to filter (e.g., "Gmail", "Work"). If None, shows all accounts.
        max_emails: Maximum number of emails to return per account.
        read_status: ``"all"`` (default), ``"unread"``, or ``"read"`` — matches
            the same parameter on ``search_emails``. Prefer this over the
            legacy ``include_read`` bool.
        include_read: Deprecated bool form of *read_status*. ``True`` ⇒
            ``read_status="all"``, ``False`` ⇒ ``read_status="unread"``.
            Kept for back-compat; emits a DeprecationWarning when passed
            explicitly. Prefer ``read_status``.
        include_content: Whether to include a content preview for each email (slower, default: False)
        output_format: "text" (default, human-readable) or "json" (structured list of email dicts)
        exclude_replied: When True, filter out emails the user has already
            replied to (detected via Message-ID matching against the Sent
            mailbox). Default False keeps the legacy unfiltered behavior.
            When True, ``flag_replied`` has no visible effect because
            replied emails are removed before formatting.
        flag_replied: When True (opt-in; default False) AND
            ``exclude_replied=False``, already-replied emails are
            annotated — text mode prefixes the subject with ``[REPLIED] ``;
            JSON mode adds an ``already_replied: true`` field per email
            entry. Default False keeps the per-call cost low (no extra
            Sent-mailbox AppleScript probe); set True for safer agent
            workflows. Only matters when ``exclude_replied=False``.
        timeout: Optional per-account AppleScript timeout in seconds (default: 120s).
            Raise this for known-slow accounts (large Exchange inboxes) when
            the default budget is too tight.
        limit: Deprecated alias for `max_emails`. Accepted for backward
            compatibility with agents that misremember the param name; emits
            a warning in the response. Prefer `max_emails`.
        unread_only: Deprecated alias mapping to ``read_status="unread"``.
            Accepted for backward compatibility; emits a warning. Prefer
            ``read_status="unread"``.

    Returns:
        Text mode: formatted list of emails with subject, sender, date, and
        read status (always a ``str``).

        JSON mode (``output_format='json'``): a Python ``dict`` with stable
        shape ``{"emails": [...], "errors": [...]}``. ``errors`` is the list
        of account names whose AppleScript probe timed out (empty list when
        nothing timed out). When deprecated aliases (`limit`, `unread_only`)
        are used a ``warnings`` key is also present.

        **Breaking change (v3.2.x):** the JSON path previously returned a
        JSON-encoded ``str`` (sometimes a raw list, sometimes an object). It
        now always returns a ``dict``. Callers that previously did
        ``json.loads(result)`` should drop the ``json.loads`` call.

        Refusal errors (``UNBOUNDED_SCAN_REQUIRED``) continue to return a
        JSON-encoded ``str`` so text-mode and JSON-mode callers see the same
        shape for that one error path.

        When multi-account dispatch encounters per-account timeouts, the
        text response includes ``PARTIAL: ... timed out`` and the JSON
        response surfaces the slow accounts in ``errors``.
    """

    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    # Tolerant alias handling: agents frequently misremember the param names
    # as `limit` / `unread_only` / `include_read`. Accept them, map to the
    # canonical `read_status`, and surface a warning so the agent learns
    # the right names.
    import warnings as _warnings_module

    warnings: List[str] = []
    if limit is not None:
        if max_emails != 50:
            return (
                "Error: pass either `max_emails` or `limit`, not both. "
                "`limit` is a deprecated alias for `max_emails`."
            )
        max_emails = limit
        warnings.append(
            "WARNING: 'limit' is a deprecated alias for 'max_emails' — please use 'max_emails' going forward."
        )

    # Reconcile read_status / include_read / unread_only into a single
    # 3-state read_filter that the script-builder layer understands.
    explicit_include_read = include_read is not True  # was passed as False
    if unread_only is not None:
        if read_status is not None or explicit_include_read:
            return (
                "Error: pass only one of `read_status`, `include_read`, or "
                "`unread_only`. `unread_only` is a deprecated alias for "
                "`read_status='unread'`."
            )
        read_status = "unread" if bool(unread_only) else "all"
        warnings.append(
            "WARNING: 'unread_only' is a deprecated alias — please use read_status='unread'."
        )
        _warnings_module.warn(
            "list_inbox_emails: 'unread_only' is deprecated; use read_status='unread'.",
            DeprecationWarning,
            stacklevel=2,
        )
    elif explicit_include_read:
        if read_status is not None:
            return (
                "Error: pass either `read_status` or `include_read`, not both. "
                "`include_read` is a deprecated alias."
            )
        read_status = "all" if include_read else "unread"
        warnings.append(
            "WARNING: 'include_read' is a deprecated alias — please use "
            "read_status='all' or read_status='unread'."
        )
        _warnings_module.warn(
            "list_inbox_emails: 'include_read' is deprecated; use read_status.",
            DeprecationWarning,
            stacklevel=2,
        )

    try:
        read_filter = _resolve_read_filter(read_status, include_read)
    except ValueError as exc:
        return f"Error: {exc}"

    if max_emails <= 0:
        err = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "list_inbox_emails refuses to walk the full inbox; "
                "use full_inbox_export instead"
            ),
            remediation={
                "preferred": "Pass max_emails=50 or 200",
                "fallback_tool": "full_inbox_export",
                "fallback_tool_args": {
                    "account": account or "<your account>",
                },
            },
        )
        return json.dumps(err.to_dict(), indent=2)

    # Smart default: fall back to the configured default account when neither
    # `account` nor `all_accounts` is set. Lazy attribute read so tests can
    # monkeypatch `apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT` after import.
    if account is None and not all_accounts and _server.DEFAULT_MAIL_ACCOUNT:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = validate_account_name(account, timeout=validation_timeout)
        if account_err:
            if output_format == "json":
                # ``account_not_found_json`` returns a JSON-encoded string for
                # back-compat with other tools; parse to a dict for JSON-mode
                # callers so they receive the same shape as success paths.
                return json.loads(
                    account_not_found_json(account, timeout=validation_timeout)
                )
            return account_err

    # When replied-detection is requested we need the Message-ID per row.
    want_message_id = bool(exclude_replied or flag_replied)

    if output_format == "json":
        body = await _list_inbox_emails_json(
            account,
            max_emails,
            read_filter,
            include_content,
            timeout,
            exclude_replied=exclude_replied,
            flag_replied=flag_replied,
            include_message_id=want_message_id,
        )
        return _attach_warnings_to_json(body, warnings)

    text_body = await _list_inbox_emails_text(
        account,
        max_emails,
        read_filter,
        include_content,
        timeout,
        exclude_replied=exclude_replied,
        flag_replied=flag_replied,
        include_message_id=want_message_id,
    )
    if warnings:
        return "\n".join(warnings) + "\n" + text_body
    return text_body


def _attach_warnings_to_json(
    body: Dict[str, Any], warnings: List[str]
) -> Dict[str, Any]:
    """Attach a ``warnings`` list to the JSON-mode inbox response dict.

    Returns *body* unchanged when *warnings* is empty so the stable shape
    ``{"emails": [...], "errors": [...]}`` is preserved for the common case.
    Otherwise appends to or sets the ``warnings`` key in place and returns
    *body*.
    """
    if not warnings:
        return body
    existing = body.get("warnings")
    if isinstance(existing, list):
        existing.extend(warnings)
    else:
        body["warnings"] = list(warnings)
    return body


def _run_text_one(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool,
    timeout: Optional[int],
    include_message_id: bool = False,
) -> str:
    """Synchronously run one account's text inbox script."""
    script = _build_list_inbox_text_script(
        account, max_emails, read_filter, include_content, include_message_id
    )
    return run_applescript(script, timeout=timeout if timeout is not None else 120)


def _normalize_message_id_token(raw: str) -> str:
    """Return a Message-ID wrapped in angle brackets for set lookups."""
    token = (raw or "").strip()
    if not token:
        return ""
    if not token.startswith("<"):
        token = "<" + token
    if not token.endswith(">"):
        token = token + ">"
    return token


def _filter_text_body_by_replied(
    body: str,
    replied_ids: set,
    *,
    exclude_replied: bool,
    flag_replied: bool,
) -> tuple[str, int]:
    """Apply replied detection to a text-format inbox body.

    Each email block starts with a ``__MSG_ID__|||<id>`` marker line emitted
    by the text script when message-id capture is enabled. The marker line
    is stripped on the way out; when *exclude_replied* is True replied
    emails (subject + From/Date lines through the next blank line) are
    dropped; when *flag_replied* is True the ✓/✉ line is prefixed with
    ``[REPLIED] ``.
    """
    lines = body.splitlines()
    out: List[str] = []
    skipped = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("__MSG_ID__|||"):
            raw_id = line.split("|||", 1)[1].strip()
            token = _normalize_message_id_token(raw_id)
            is_replied = bool(token) and token in replied_ids
            # Look ahead for the next non-empty line — the rendered email block.
            j = i + 1
            # First non-empty content line is the indicator + subject.
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines):
                i = j
                continue
            indicator_line = lines[j]
            # Find the trailing blank that ends this email block, so we can
            # cleanly skip it when exclude_replied is True.
            k = j + 1
            while k < len(lines) and lines[k].strip() != "":
                k += 1
            block_end = k  # 'k' points at the first blank or end-of-list.

            if is_replied and exclude_replied:
                skipped += 1
                # Advance past block; also drop the trailing blank if present.
                i = block_end + 1 if block_end < len(lines) else block_end
                continue

            if is_replied and flag_replied:
                # Inject [REPLIED] after the indicator and single space.
                # Indicator line shape: "<sym> <subject>"
                if " " in indicator_line:
                    sym, rest = indicator_line.split(" ", 1)
                    indicator_line = f"{sym} [REPLIED] {rest}"
                else:
                    indicator_line = f"{indicator_line} [REPLIED]"

            # Emit any blank lines between marker and indicator, then block.
            out.extend(lines[i + 1 : j])
            out.append(indicator_line)
            out.extend(lines[j + 1 : block_end])
            if block_end < len(lines):
                out.append(lines[block_end])
            i = block_end + 1 if block_end < len(lines) else block_end
            continue
        out.append(line)
        i += 1
    return "\n".join(out), skipped


async def _list_inbox_emails_text(
    account: Optional[str],
    max_emails: int,
    read_filter: str,
    include_content: bool,
    timeout: Optional[int],
    *,
    exclude_replied: bool = False,
    flag_replied: bool = False,
    include_message_id: bool = False,
) -> str:
    """Async text-format implementation, dispatching one script per account."""
    header = "INBOX EMAILS - ALL ACCOUNTS\n\n"
    footer_template = (
        "========================================\n"
        "TOTAL EMAILS: {total}\n"
        "========================================\n"
    )

    if account:
        try:
            body = await asyncio.to_thread(
                _run_text_one,
                account,
                max_emails,
                read_filter,
                include_content,
                timeout,
                include_message_id,
            )
        except AppleScriptTimeout:
            return (
                header
                + footer_template.format(total=0)
                + f"\nPARTIAL: 1 account(s) timed out: {account}\n"
            )
        clean, count = _strip_count_marker(body)
        if include_message_id and (exclude_replied or flag_replied):
            replied = await asyncio.to_thread(fetch_replied_ids, account, 200, timeout)
            clean, _skipped = _filter_text_body_by_replied(
                clean,
                replied,
                exclude_replied=exclude_replied,
                flag_replied=flag_replied,
            )
        return header + clean + "\n" + footer_template.format(total=count)

    # Multi-account: probe account list, then dispatch in parallel.
    try:
        accounts = await asyncio.to_thread(_list_mail_accounts, timeout)
    except AppleScriptTimeout:
        return header + footer_template.format(total=0) + "\nPARTIAL: account listing timed out\n"

    if not accounts:
        return header + footer_template.format(total=0)

    async def run_one(acct: str):
        try:
            return acct, await asyncio.to_thread(
                _run_text_one,
                acct,
                max_emails,
                read_filter,
                include_content,
                timeout,
                include_message_id,
            )
        except AppleScriptTimeout:
            return acct, AppleScriptTimeout(acct)

    results = await asyncio.gather(*(run_one(a) for a in accounts))

    # Pre-fetch per-account replied sets in parallel when needed.
    replied_sets: Dict[str, set] = {}
    if include_message_id and (exclude_replied or flag_replied):
        replied_results = await asyncio.gather(
            *(asyncio.to_thread(fetch_replied_ids, a, 200, timeout) for a in accounts)
        )
        replied_sets = dict(zip(accounts, replied_results))

    pieces: List[str] = [header]
    total = 0
    errors: List[str] = []
    for acct, outcome in results:
        if isinstance(outcome, AppleScriptTimeout):
            errors.append(acct)
            continue
        clean, count = _strip_count_marker(outcome)
        if include_message_id and (exclude_replied or flag_replied):
            clean, _skipped = _filter_text_body_by_replied(
                clean,
                replied_sets.get(acct, set()),
                exclude_replied=exclude_replied,
                flag_replied=flag_replied,
            )
        if clean:
            pieces.append(clean)
            pieces.append("\n")
        total += count
    pieces.append(footer_template.format(total=total))
    if errors:
        pieces.append(f"\nPARTIAL: {len(errors)} account(s) timed out: {', '.join(errors)}\n")
    return "".join(pieces)


def _run_json_one(
    account: str,
    max_emails: int,
    read_filter: str,
    include_content: bool | int | None = False,
    timeout: Optional[int] = None,
    include_message_id: bool = False,
) -> str:
    """Synchronously run one account's JSON inbox script."""
    # Backward compatibility for older call sites that passed
    # (account, max_emails, read_filter, timeout) before content previews
    # were added to the JSON path.
    if timeout is None and not isinstance(include_content, bool):
        timeout = include_content
        include_content = False
    script = _build_list_inbox_json_script(
        account,
        max_emails,
        read_filter,
        bool(include_content),
        include_message_id=include_message_id,
    )
    return run_applescript(script, timeout=timeout if timeout is not None else 120)


def _apply_replied_to_emails(
    emails: List[Dict[str, Any]],
    replied_set: set,
    *,
    exclude_replied: bool,
    flag_replied: bool,
) -> List[Dict[str, Any]]:
    """Filter or flag email dicts based on a replied Message-ID set."""
    if not (exclude_replied or flag_replied):
        return emails
    out: List[Dict[str, Any]] = []
    for em in emails:
        token = _normalize_message_id_token(em.get("internet_message_id", ""))
        is_replied = bool(token) and token in replied_set
        if is_replied and exclude_replied:
            continue
        if is_replied and flag_replied and not exclude_replied:
            em = dict(em)
            em["already_replied"] = True
        out.append(em)
    return out


async def _list_inbox_emails_json(
    account: Optional[str],
    max_emails: int,
    read_filter: str,
    include_content: bool,
    timeout: Optional[int],
    *,
    exclude_replied: bool = False,
    flag_replied: bool = False,
    include_message_id: bool = False,
) -> Dict[str, Any]:
    """Return inbox emails as a structured dict.

    Stable shape: ``{"emails": [...], "errors": [...]}`` for both the
    single-account and multi-account paths. ``errors`` is the list of
    account names whose probe timed out (empty list when nothing timed
    out). Account-listing timeouts surface as
    ``{"emails": [], "errors": ["__account_listing__"]}``.

    When ``include_content`` is True each record gains a ``content_preview``
    field; when replied detection is requested, records may carry an
    ``already_replied`` field or be filtered out entirely depending on the
    flags.

    **Breaking change (v3.2.x):** previously returned a JSON-encoded
    ``str`` (sometimes a raw list, sometimes a dict). Callers that did
    ``json.loads(result)`` should drop the ``json.loads``.
    """

    if account:
        try:
            raw = await asyncio.to_thread(
                _run_json_one,
                account,
                max_emails,
                read_filter,
                include_content,
                timeout,
                include_message_id,
            )
        except AppleScriptTimeout:
            return {"emails": [], "errors": [account]}
        emails = _parse_pipe_delimited_emails(raw, has_message_id=include_message_id)
        if include_message_id and (exclude_replied or flag_replied):
            replied = await asyncio.to_thread(fetch_replied_ids, account, 200, timeout)
            emails = _apply_replied_to_emails(
                emails,
                replied,
                exclude_replied=exclude_replied,
                flag_replied=flag_replied,
            )
        return {"emails": emails, "errors": []}

    try:
        accounts = await asyncio.to_thread(_list_mail_accounts, timeout)
    except AppleScriptTimeout:
        return {"emails": [], "errors": ["__account_listing__"]}

    if not accounts:
        return {"emails": [], "errors": []}

    async def run_one(acct: str):
        try:
            return acct, await asyncio.to_thread(
                _run_json_one,
                acct,
                max_emails,
                read_filter,
                include_content,
                timeout,
                include_message_id,
            )
        except AppleScriptTimeout:
            return acct, AppleScriptTimeout(acct)

    results = await asyncio.gather(*(run_one(a) for a in accounts))

    # Pre-fetch per-account replied sets in parallel when needed.
    replied_sets: Dict[str, set] = {}
    if include_message_id and (exclude_replied or flag_replied):
        replied_results = await asyncio.gather(
            *(asyncio.to_thread(fetch_replied_ids, a, 200, timeout) for a in accounts)
        )
        replied_sets = dict(zip(accounts, replied_results))

    combined: List[Dict[str, Any]] = []
    errors: List[str] = []
    for acct, outcome in results:
        if isinstance(outcome, AppleScriptTimeout):
            errors.append(acct)
            continue
        parsed = _parse_pipe_delimited_emails(outcome, has_message_id=include_message_id)
        if include_message_id and (exclude_replied or flag_replied):
            parsed = _apply_replied_to_emails(
                parsed,
                replied_sets.get(acct, set()),
                exclude_replied=exclude_replied,
                flag_replied=flag_replied,
            )
        combined.extend(parsed)

    return {"emails": combined, "errors": errors}


# ---------------------------------------------------------------------------
# get_mailbox_unread_counts and other tools (unchanged)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def get_mailbox_unread_counts(
    account: Optional[str] = None,
    include_zero: bool = False,
    summary_only: bool = False,
    max_mailboxes: int = 100,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Get unread counts per mailbox for one account or all accounts.

    When summary_only=True, returns only per-account inbox unread totals
    (replaces the former get_unread_count tool).

    Args:
        account: Optional account name filter
        include_zero: Whether to include mailboxes with zero unread messages
        summary_only: If True, return only per-account inbox unread totals
                      (flat dict of account name -> unread count)
        max_mailboxes: Maximum number of top-level mailboxes to enumerate per
            account (default: 100). When the cap fires, the account's result
            includes a ``truncated: true`` field. On Exchange accounts with
            deep nested folder trees, or Gmail accounts with 200+ labels,
            exceeding this cap can trigger the 120s timeout from sheer
            property-read volume.
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        If summary_only=False: nested dict keyed by account name then mailbox path
        If summary_only=True: flat dict mapping account names to inbox unread counts
    """
    if account is None and _server.DEFAULT_MAIL_ACCOUNT:
        account = _server.DEFAULT_MAIL_ACCOUNT

    if account:
        account_err = validate_account_name(
            account, timeout=30 if timeout is None else min(timeout, 30)
        )
        if account_err:
            return {"error": "account_not_found", "account": account}

    escaped_account = escape_applescript(account) if account else None
    effective_timeout = timeout if timeout is not None else 120

    # Fast path: summary_only returns just per-account inbox unread totals
    if summary_only:
        summary_account_filter = (
            f'''
                if accountName is not "{escaped_account}" then
                    set shouldIncludeAccount to false
                end if
        '''
            if account
            else ""
        )
        script = f"""
        tell application "Mail"
            set resultList to {{}}
            set allAccounts to every account

            repeat with anAccount in allAccounts
                set accountName to name of anAccount
                set shouldIncludeAccount to true
                {summary_account_filter}

                if shouldIncludeAccount then
                    try
                        {inbox_mailbox_script("inboxMailbox", "anAccount")}
                        set unreadCount to unread count of inboxMailbox
                        set end of resultList to accountName & ":" & unreadCount
                    on error
                        set end of resultList to accountName & ":ERROR"
                    end try
                end if
            end repeat

            set AppleScript's text item delimiters to "|"
            return resultList as string
        end tell
        """
        try:
            result = run_applescript(script, timeout=effective_timeout)
        except AppleScriptTimeout:
            return {
                "error": "timed_out",
                "message": (
                    "AppleScript timed out while fetching inbox unread counts. "
                    "Try again or pass a larger `timeout`."
                ),
            }
        flat_counts: Dict[str, int] = {}
        for item in result.split("|"):
            if ":" in item:
                acct_name, count_str = item.split(":", 1)
                if count_str != "ERROR":
                    flat_counts[acct_name] = int(count_str)
                else:
                    flat_counts[acct_name] = -1
        return flat_counts

    account_filter = (
        f'''
            if accountName is not "{escaped_account}" then
                set shouldIncludeAccount to false
            end if
    '''
        if account
        else ""
    )

    script = f"""
    tell application "Mail"
        set resultList to {{}}
        set allAccounts to every account

        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            set shouldIncludeAccount to true
            {account_filter}

            if shouldIncludeAccount then
                try
                    set accountMailboxes to every mailbox of anAccount
                    set mailboxIndex to 0
                    set accountTruncated to false

                    repeat with aMailbox in accountMailboxes
                        set mailboxIndex to mailboxIndex + 1
                        if mailboxIndex > {max_mailboxes} then
                            set accountTruncated to true
                            exit repeat
                        end if
                        try
                            set mailboxName to name of aMailbox
                            -- Always emit the parent row with its own unread count
                            -- (bare name as key, NOT prefixed).  Exchange INBOX has
                            -- messages AND children — skipping the parent silently
                            -- drops its own unread count.
                            set unreadCount to unread count of aMailbox
                            if {str(include_zero).lower()} or unreadCount > 0 then
                                set end of resultList to accountName & "|||" & mailboxName & "|||" & unreadCount
                            end if
                            -- Also emit child mailboxes under parent/child paths so
                            -- each child's own count is visible without double-counting
                            -- the parent (different keys: "Inbox" vs "Inbox/Sub").
                            set subMailboxes to {{}}
                            try
                                set subMailboxes to every mailbox of aMailbox
                            end try
                            repeat with subBox in subMailboxes
                                set subName to name of subBox
                                set subUnread to unread count of subBox
                                if {str(include_zero).lower()} or subUnread > 0 then
                                    set end of resultList to accountName & "|||" & mailboxName & "/" & subName & "|||" & subUnread
                                end if
                            end repeat
                        end try
                    end repeat

                    if accountTruncated then
                        set end of resultList to accountName & "|||__TRUNCATED__|||{max_mailboxes}"
                    end if
                end try
            end if
        end repeat

        if (count of resultList) is 0 then
            return ""
        end if

        set AppleScript's text item delimiters to linefeed
        set outputText to resultList as string
        set AppleScript's text item delimiters to ""
        return outputText
    end tell
    """

    try:
        result = run_applescript(script, timeout=effective_timeout)
    except AppleScriptTimeout:
        return {
            "error": "timed_out",
            "message": (
                "AppleScript timed out while fetching mailbox unread counts. "
                "Try again or pass a larger `timeout`."
            ),
        }
    nested_counts: Dict[str, Dict[str, int]] = {}
    truncated_accounts: set = set()
    if not result:
        return nested_counts

    for line in result.splitlines():
        parts = line.split("|||", 2)
        if len(parts) != 3:
            continue
        account_name, mailbox_name, unread_value = parts
        if mailbox_name == "__TRUNCATED__":
            truncated_accounts.add(account_name)
            continue
        nested_counts.setdefault(account_name, {})[mailbox_name] = int(unread_value)

    # Attach truncation marker to offending account records.
    for acct in truncated_accounts:
        if acct not in nested_counts:
            nested_counts[acct] = {}
        nested_counts[acct]["__truncated__"] = True  # type: ignore[assignment]

    return nested_counts


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def list_accounts(timeout: Optional[int] = 30) -> List[str]:
    """
    List all available Mail accounts.

    Args:
        timeout: Optional AppleScript timeout in seconds (default: 30s).

    Returns:
        List of account names
    """

    script = """
    tell application "Mail"
        set accountNames to {}
        set allAccounts to every account

        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            set end of accountNames to accountName
        end repeat

        set AppleScript's text item delimiters to "|"
        return accountNames as string
    end tell
    """

    result = run_applescript(script, timeout=timeout)
    return result.split("|") if result else []


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def list_account_addresses(timeout: Optional[int] = 30) -> Dict[str, List[str]]:
    """
    List all configured email addresses for each Mail account.

    Useful for mapping a Mail.app account name (e.g. "Gmail", "Work") to the
    actual email address(es) it receives mail at — handy when an integration
    needs to know which inbox a message landed in by address rather than by
    Mail.app's display name.

    Args:
        timeout: Optional AppleScript timeout in seconds (default: 30s).

    Returns:
        Dict mapping account name -> list of email addresses configured on
        that account. Accounts with no addresses configured map to [].
    """

    script = """
    tell application "Mail"
        set outLines to {}
        set allAccounts to every account

        repeat with anAccount in allAccounts
            set acctName to name of anAccount
            try
                set emailAddrs to email addresses of anAccount
            on error
                set emailAddrs to {}
            end try
            if emailAddrs is missing value then
                set emailAddrs to {}
            end if
            set AppleScript's text item delimiters to ","
            set addrStr to emailAddrs as string
            set AppleScript's text item delimiters to ""
            set end of outLines to acctName & "|" & addrStr
        end repeat

        set AppleScript's text item delimiters to linefeed
        set joined to outLines as string
        set AppleScript's text item delimiters to ""
        return joined
    end tell
    """

    result = run_applescript(script, timeout=timeout)
    out: Dict[str, List[str]] = {}
    if not result:
        return out
    for line in result.splitlines():
        if "|" not in line:
            continue
        name, addrs = line.split("|", 1)
        out[name] = [a.strip() for a in addrs.split(",") if a.strip()]
    return out


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
def list_mailboxes(
    account: Optional[str] = None,
    include_counts: bool = False,
    output_format: str = "text",
    max_mailboxes: Optional[int] = None,
    timeout: Optional[int] = None,
) -> str:
    """
    List all mailboxes (folders) for a specific account or all accounts.

    Args:
        account: Optional account name to filter (e.g., "Gmail", "Work"). If None, shows all accounts.
        include_counts: Whether to include message counts for each mailbox (default: False).
            Counts are expensive on large accounts — pass True only for folder audits.
        output_format: "text" (default, human-readable) or "json" (structured list of mailbox dicts)
        max_mailboxes: Cap on mailboxes returned per account. Defaults to 100. When the cap
            fires, text mode appends a truncation banner and JSON mode includes
            ``total``, ``returned``, and ``truncated`` fields.
        timeout: Optional AppleScript timeout in seconds (default: 120s).

    Returns:
        Formatted list of mailboxes with optional message counts.
        For nested mailboxes, shows both indented format and path format (e.g., "Projects/Amplify Impact")
    """
    # Apply the default cap for both modes.
    effective_max_mailboxes = max_mailboxes if max_mailboxes is not None else 100

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = validate_account_name(account, timeout=validation_timeout)
        if account_err:
            if output_format == "json":
                return account_not_found_json(account, timeout=validation_timeout)
            return account_err

    if output_format == "json":
        return _list_mailboxes_json(
            account,
            include_counts,
            max_mailboxes=effective_max_mailboxes,
            timeout=timeout,
        )

    count_script = (
        """
        try
            set msgCount to count of messages of aMailbox
            set unreadCount to unread count of aMailbox
            set outputText to outputText & " (" & msgCount & " total, " & unreadCount & " unread)"
        on error
            set outputText to outputText & " (count unavailable)"
        end try
    """
        if include_counts
        else ""
    )

    # Escape user inputs for AppleScript
    escaped_account = escape_applescript(account) if account else None

    account_filter = (
        f'''
        if accountName is "{escaped_account}" then
    '''
        if account
        else ""
    )

    account_filter_end = "end if" if account else ""

    script = f"""
    tell application "Mail"
        set outputText to "MAILBOXES" & return & return
        set allAccounts to every account
        set wasCapped to false

        repeat with anAccount in allAccounts
            set accountName to name of anAccount

            {account_filter}
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return
                set outputText to outputText & "📁 ACCOUNT: " & accountName & return
                set outputText to outputText & "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" & return & return

                try
                    set accountMailboxes to every mailbox of anAccount
                    set mailboxCount to 0

                    repeat with aMailbox in accountMailboxes
                        set mailboxCount to mailboxCount + 1
                        if mailboxCount > {effective_max_mailboxes} then
                            set wasCapped to true
                            exit repeat
                        end if
                        set mailboxName to name of aMailbox
                        set outputText to outputText & "  📂 " & mailboxName

                        {count_script}

                        set outputText to outputText & return

                        -- List sub-mailboxes with path notation
                        try
                            set subMailboxes to every mailbox of aMailbox
                            repeat with subBox in subMailboxes
                                set subName to name of subBox
                                set outputText to outputText & "    └─ " & subName & " [Path: " & mailboxName & "/" & subName & "]"

                                {count_script.replace("aMailbox", "subBox") if include_counts else ""}

                                set outputText to outputText & return
                            end repeat
                        end try
                    end repeat

                    set outputText to outputText & return
                on error errMsg
                    set outputText to outputText & "  ⚠ Error accessing mailboxes: " & errMsg & return & return
                end try
            {account_filter_end}
        end repeat

        if wasCapped then
            set outputText to outputText & "⚠ Truncated: list_mailboxes capped at {effective_max_mailboxes} mailboxes per account." & return
            set outputText to outputText & "  Pass max_mailboxes=N to adjust the cap." & return
        end if

        return outputText
    end tell
    """

    try:
        result = run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return (
            "Error: list_mailboxes timed out while enumerating mailboxes. "
            "Retry with a specific account, include_counts=False, or a larger `timeout`."
        )
    return result


def _list_mailboxes_json(
    account: Optional[str],
    include_counts: bool = True,
    *,
    max_mailboxes: Optional[int] = None,
    timeout: Optional[int] = None,
) -> str:
    """Return mailboxes as JSON."""
    escaped_account = escape_applescript(account) if account else None
    account_filter = (
        f'if accountName is "{escaped_account}" then'
        if account
        else ""
    )
    account_filter_end = "end if" if account else ""
    cap_check = ""
    if max_mailboxes is not None and max_mailboxes > 0:
        cap_check = f"""
            if mailboxIndex > {max_mailboxes} then exit repeat
        """
    def count_fields(var_name: str) -> str:
        if not include_counts:
            return """
        set msgCount to -1
        set unreadCount to -1
        """
        return f"""
        set msgCount to -1
        set unreadCount to -1
        try
            set msgCount to count of messages of {var_name}
            set unreadCount to unread count of {var_name}
        end try
        """

    script = f"""
    tell application "Mail"
        set resultLines to {{}}
        set allAccounts to every account
        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            {account_filter}
            try
                set accountMailboxes to every mailbox of anAccount
                set mailboxIndex to 0
                repeat with currentMailbox in accountMailboxes
                    set mailboxIndex to mailboxIndex + 1
                    {cap_check}
                    try
                        set mailboxName to name of currentMailbox
                        {count_fields("currentMailbox")}
                        set end of resultLines to accountName & "|||" & mailboxName & "|||" & mailboxName & "|||" & msgCount & "|||" & unreadCount
                        {cap_check}
                        try
                            set childMailboxes to every mailbox of currentMailbox
                            repeat with childMailbox in childMailboxes
                                set mailboxIndex to mailboxIndex + 1
                                {cap_check}
                                set childName to name of childMailbox
                                {count_fields("childMailbox")}
                                set end of resultLines to accountName & "|||" & childName & "|||" & mailboxName & "/" & childName & "|||" & msgCount & "|||" & unreadCount
                            end repeat
                        end try
                    end try
                end repeat
            end try
            {account_filter_end}
        end repeat
        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    """
    try:
        raw = run_applescript(script, timeout=timeout if timeout is not None else 120)
    except AppleScriptTimeout:
        return json.dumps(
            {
                "error": "timed_out",
                "mailboxes": [],
                "message": (
                    "list_mailboxes timed out while enumerating mailboxes. "
                    "Retry with a specific account, include_counts=false, "
                    "or a larger timeout."
                ),
            },
            indent=2,
        )
    mailboxes = []
    for line in raw.splitlines():
        parts = line.split("|||")
        if len(parts) != 5:
            continue
        msg_count = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
        unread_count = int(parts[4]) if parts[4].lstrip("-").isdigit() else -1
        item: Dict[str, Any] = {
            "account": parts[0],
            "name": parts[1],
            "path": parts[2],
        }
        if include_counts:
            item["message_count"] = msg_count
            item["unread_count"] = unread_count
        mailboxes.append(item)

    if max_mailboxes is None:
        return json.dumps(mailboxes, indent=2)

    total = len(mailboxes)
    # If the AppleScript emitted more rows than the cap (fence-post: parent row
    # appended before the post-parent cap_check fires) truncate here so the
    # returned contract (len <= max_mailboxes) is always satisfied.
    truncated = total >= max_mailboxes
    if total > max_mailboxes:
        mailboxes = mailboxes[:max_mailboxes]
    returned = len(mailboxes)
    payload = {
        "mailboxes": mailboxes,
        "total": total,
        "returned": returned,
        "truncated": truncated,
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# get_inbox_overview — async, per-account parallel
# ---------------------------------------------------------------------------

def _build_overview_one_account_script(
    account: str,
    *,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
) -> str:
    """Build a script that returns one account's unread/total/recent slice.

    Returns a structured payload:
        accountName|||unreadCount|||totalCount
        MAILBOX|||name|||unreadCount
        MAILBOX|||name/subName|||subUnread
        RECENT|||subject|||sender|||date|||read
        MAILBOX_CAPPED|||accountName|||cap
        ...

    A1: caps recent-message enumeration to 10 via
    `messages 1 thru 10 of inboxMailbox`.
    A2: caps mailbox enumeration at max_mailboxes (default 100) to prevent
    Exchange deep-folder or Gmail many-labels timeouts.
    """
    escaped_account = escape_applescript(account)
    recent_block = ""
    if include_recent and max_recent > 0:
        recent_block = f"""
                -- Recent messages (cap at {max_recent})
                if (count of messages of inboxMailbox) > {max_recent} then
                    set recentMessages to messages 1 thru {max_recent} of inboxMailbox
                else
                    set recentMessages to messages of inboxMailbox
                end if

                repeat with aMessage in recentMessages
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        set messageRead to read status of aMessage
                        set end of resultLines to "RECENT|||" & messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead
                    end try
                end repeat
        """
    mailbox_block = ""
    if include_mailboxes:
        mailbox_block = f"""
            -- Mailbox structure with unread counts (capped at {max_mailboxes})
            try
                set accountMailboxes to every mailbox of anAccount
                set mailboxIndex to 0
                repeat with aMailbox in accountMailboxes
                    set mailboxIndex to mailboxIndex + 1
                    if mailboxIndex > {max_mailboxes} then
                        set end of resultLines to "MAILBOX_CAPPED|||" & accountName & "|||{max_mailboxes}"
                        exit repeat
                    end if
                    try
                        set mailboxName to name of aMailbox
                        set unreadCount to unread count of aMailbox
                        set end of resultLines to "MAILBOX|||" & mailboxName & "|||" & unreadCount
                        try
                            set subMailboxes to every mailbox of aMailbox
                            repeat with subBox in subMailboxes
                                set subName to name of subBox
                                set subUnread to unread count of subBox
                                set end of resultLines to "SUBMAILBOX|||" & mailboxName & "/" & subName & "|||" & subUnread
                            end repeat
                        end try
                    end try
                end repeat
            end try
        """
    return f"""
    tell application "Mail"
        set resultLines to {{}}
        try
            set anAccount to account "{escaped_account}"
            set accountName to name of anAccount

            try
                {inbox_mailbox_script("inboxMailbox", "anAccount")}
                set unreadCount to unread count of inboxMailbox
                set totalMessages to count of messages of inboxMailbox
                set end of resultLines to "HEADER|||" & accountName & "|||" & unreadCount & "|||" & totalMessages

                {recent_block}
            on error errMsg
                set end of resultLines to "HEADER|||" & accountName & "|||ERROR|||" & errMsg
            end try

            {mailbox_block}
        on error errMsg
            set end of resultLines to "FATAL|||" & errMsg
        end try

        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    """


def _run_overview_one(
    account: str,
    timeout: Optional[int],
    include_mailboxes: bool = True,
    include_recent: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
) -> str:
    effective_timeout = timeout if timeout is not None else 180
    return run_applescript(
        _build_overview_one_account_script(
            account,
            include_mailboxes=include_mailboxes,
            include_recent=include_recent,
            max_recent=max_recent,
            max_mailboxes=max_mailboxes,
        ),
        timeout=effective_timeout,
    )


def _parse_overview_account(raw: str) -> Dict[str, Any]:
    """Parse one account's overview payload."""
    result: Dict[str, Any] = {
        "account": None,
        "unread": None,
        "total": None,
        "error": None,
        "mailboxes": [],  # list of (name, unread_count) tuples
        "recent": [],     # list of dicts
        "mailboxes_truncated": False,
    }
    parse_errors: List[str] = []
    if not raw:
        return result
    for line in raw.splitlines():
        if "|||" not in line:
            continue
        parts = line.split("|||")
        tag = parts[0]
        if tag == "HEADER" and len(parts) >= 4:
            result["account"] = parts[1]
            if parts[2] == "ERROR":
                result["error"] = parts[3] if len(parts) > 3 else "unknown error"
            else:
                try:
                    result["unread"] = int(parts[2])
                    result["total"] = int(parts[3])
                except ValueError:
                    parse_errors.append(
                        f"Invalid HEADER counts for {parts[1]!r}: {parts[2]!r}, {parts[3]!r}"
                    )
        elif tag in ("MAILBOX", "SUBMAILBOX") and len(parts) >= 3:
            try:
                result["mailboxes"].append((parts[1], int(parts[2])))
            except ValueError:
                parse_errors.append(
                    f"Invalid {tag} unread count for {parts[1]!r}: {parts[2]!r}"
                )
        elif tag == "MAILBOX_CAPPED" and len(parts) >= 2:
            result["mailboxes_truncated"] = True
        elif tag == "RECENT" and len(parts) >= 5:
            result["recent"].append({
                "subject": parts[1],
                "sender": parts[2],
                "date": parts[3],
                "is_read": parts[4].strip().lower() == "true",
            })
        elif tag == "FATAL" and len(parts) >= 2:
            result["error"] = parts[1]
    if parse_errors:
        result["parse_errors"] = parse_errors
    return result


def _format_overview(
    accounts: List[Dict[str, Any]],
    errors: List[str],
    *,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    compact: bool = False,
) -> str:
    """Format combined per-account overview payloads into the legacy text shape."""
    lines: List[str] = []
    if not compact:
        lines.append("╔══════════════════════════════════════════╗")
        lines.append("║      EMAIL INBOX OVERVIEW                ║")
        lines.append("╚══════════════════════════════════════════╝")
        lines.append("")
    lines.append("📊 UNREAD EMAILS BY ACCOUNT")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    total_unread = 0
    for acct in accounts:
        name = acct.get("account") or "(unknown)"
        if acct.get("error"):
            lines.append(f"  ❌ {name}: Error accessing inbox")
            continue
        unread = acct.get("unread") or 0
        total = acct.get("total") or 0
        total_unread += unread
        prefix = "⚠️ " if unread > 0 else "✅"
        if compact:
            lines.append(f"  {prefix} {name}: {unread} unread")
        else:
            lines.append(f"  {prefix} {name}: {unread} unread ({total} total)")

    lines.append("")
    lines.append(f"📈 TOTAL UNREAD: {total_unread} across all accounts")

    if include_mailboxes and not compact:
        lines.append("")
        lines.append("")
        lines.append("📁 MAILBOX STRUCTURE")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for acct in accounts:
            name = acct.get("account") or "(unknown)"
            lines.append(f"\nAccount: {name}")
            for mb_name, mb_unread in acct.get("mailboxes", []):
                if "/" in mb_name:
                    if mb_unread > 0:
                        lines.append(f"     └─ {mb_name.split('/', 1)[1]} ({mb_unread} unread)")
                else:
                    if mb_unread > 0:
                        lines.append(f"  📂 {mb_name} ({mb_unread} unread)")
                    else:
                        lines.append(f"  📂 {mb_name}")
            if acct.get("mailboxes_truncated"):
                lines.append(f"  ⚠ Mailbox list truncated — account has more mailboxes than the cap allows.")

    if include_recent:
        lines.append("")
        lines.append("")
        label = f"📬 RECENT EMAILS PREVIEW ({max_recent} Most Recent)"
        lines.append(label)
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        recent_combined = []
        for acct in accounts:
            name = acct.get("account") or "(unknown)"
            for r in acct.get("recent", []):
                recent_combined.append((name, r))
        display_count = 0
        for name, r in recent_combined:
            if display_count >= max_recent:
                break
            display_count += 1
            indicator = "✓" if r["is_read"] else "✉"
            lines.append("")
            lines.append(f"{indicator} {r['subject']}")
            if not compact:
                lines.append(f"   Account: {name}")
            lines.append(f"   From: {r['sender']}")
            lines.append(f"   Date: {r['date']}")

        if display_count == 0:
            lines.append("")
            lines.append("No recent emails found.")

    if include_suggestions and not compact:
        lines.append("")
        lines.append("")
        lines.append("💡 SUGGESTED ACTIONS FOR ASSISTANT")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("Based on this overview, consider suggesting:")
        lines.append("")
        if total_unread > 0:
            lines.append("1. 📧 Review unread emails - Use list_inbox_emails to show recent unread messages")
            lines.append("2. 🔍 Search for action items - Look for keywords like 'urgent', 'action required', 'deadline'")
            lines.append("3. 📤 Move processed emails - Suggest moving read emails to appropriate folders")
        else:
            lines.append("1. ✅ Inbox is clear! No unread emails.")
        lines.append("4. 📋 Organize by topic - Suggest moving emails to project-specific folders")
        lines.append("5. ✉️  Draft replies - Identify emails that need responses")
        lines.append("6. 🗂️  Archive old emails - Move older read emails to archive folders")
        lines.append("7. 🔔 Highlight priority items - Identify emails from important senders or with urgent keywords")
        lines.append("")
        lines.append("═══════════════════════════════════════════════════")
        lines.append("💬 Ask me to drill down into any account or take specific actions!")
        lines.append("═══════════════════════════════════════════════════")

    if errors:
        lines.append("")
        lines.append(f"PARTIAL: {len(errors)} account(s) timed out: {', '.join(errors)}")

    return "\n".join(lines)


def _overview_suggestions(total_unread: int) -> List[str]:
    """Action suggestions mirrored from the text-mode overview footer."""
    if total_unread > 0:
        return [
            "Review unread emails - Use list_inbox_emails to show recent unread messages",
            "Search for action items - Look for keywords like 'urgent', 'action required', 'deadline'",
            "Move processed emails - Suggest moving read emails to appropriate folders",
            "Organize by topic - Suggest moving emails to project-specific folders",
            "Draft replies - Identify emails that need responses",
            "Archive old emails - Move older read emails to archive folders",
            "Highlight priority items - Identify emails from important senders or with urgent keywords",
        ]
    return [
        "Inbox is clear! No unread emails.",
        "Organize by topic - Suggest moving emails to project-specific folders",
        "Draft replies - Identify emails that need responses",
        "Archive old emails - Move older read emails to archive folders",
        "Highlight priority items - Identify emails from important senders or with urgent keywords",
    ]


def _overview_json_error(
    error: str,
    *,
    account: Optional[str] = None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    message: Optional[str] = None,
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error": error,
        "output_format": "json",
        "include_mailboxes": include_mailboxes,
        "include_recent": include_recent,
        "include_suggestions": include_suggestions,
        "max_recent": max_recent,
        "total_unread": 0,
        "accounts": [],
        "suggestions": [],
        "errors": errors or [],
    }
    if account is not None:
        payload["account"] = account
    if message is not None:
        payload["message"] = message
    return payload


def _format_overview_json(
    accounts: List[Dict[str, Any]],
    errors: List[str],
    *,
    account: Optional[str] = None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
) -> Dict[str, Any]:
    """Return structured overview payload for JSON mode."""
    total_unread = 0
    account_rows: List[Dict[str, Any]] = []
    for acct in accounts:
        row: Dict[str, Any] = {"account": acct.get("account")}
        if acct.get("error"):
            row["error"] = acct["error"]
        else:
            row["unread"] = acct.get("unread") or 0
            row["total"] = acct.get("total") or 0
            total_unread += row["unread"]
            if include_mailboxes:
                row["mailboxes"] = [
                    {"path": name, "unread": unread}
                    for name, unread in acct.get("mailboxes", [])
                ]
                if acct.get("mailboxes_truncated"):
                    row["mailboxes_truncated"] = True
            if include_recent:
                row["recent"] = acct.get("recent", [])[:max_recent]
        account_rows.append(row)

    payload: Dict[str, Any] = {
        "output_format": "json",
        "include_mailboxes": include_mailboxes,
        "include_recent": include_recent,
        "include_suggestions": include_suggestions,
        "max_recent": max_recent,
        "total_unread": total_unread,
        "accounts": account_rows,
        "suggestions": _overview_suggestions(total_unread) if include_suggestions else [],
        "errors": errors,
    }
    if account is not None:
        payload["account"] = account
    return payload


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def get_inbox_overview(
    account: Optional[str] = None,
    output_format: str = "text",
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
    timeout: Optional[int] = None,
) -> Union[str, Dict[str, Any]]:
    """
    Get a comprehensive overview of your email inbox status across all accounts.

    Each account is queried in parallel via its own AppleScript call, so a
    single slow account (e.g. a large Exchange inbox) no longer blocks the
    overview — it appears as an entry in a `PARTIAL` line and the rest of
    the data is returned anyway.

    Args:
        account: Optional account name to scope the overview to one account.
        output_format: ``text`` (default), ``compact`` (shorter text), or ``json``.
        include_mailboxes: Include mailbox structure with unread counts (default: True).
        include_recent: Include recent-email preview section (default: True).
        include_suggestions: Include assistant action suggestions (default: True).
        max_recent: Maximum recent emails to show across all accounts (default: 10).
        max_mailboxes: Maximum top-level mailboxes to enumerate per account
            (default: 100). When the cap fires, the affected account's data will
            show ``mailboxes_truncated=True`` in JSON mode and a warning in the
            errors field. On Exchange accounts with deep nested folders or Gmail
            with many labels, uncapped mailbox enumeration can exceed the 120s
            timeout from sheer property-read volume.
        timeout: Optional per-account AppleScript timeout in seconds
            (default: 180s).

    Returns:
        Comprehensive overview including unread counts, optional mailbox
        structure, recent preview, and optional AI suggestions. JSON mode
        returns a structured dict.
    """
    if output_format not in {"text", "compact", "json"}:
        return "Error: Invalid output_format. Use: text, compact, json"

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = validate_account_name(account, timeout=validation_timeout)
        if account_err:
            if output_format == "json":
                return _overview_json_error(
                    "account_not_found",
                    account=account,
                    include_mailboxes=include_mailboxes,
                    include_recent=include_recent,
                    include_suggestions=include_suggestions,
                    max_recent=max_recent,
                )
            return account_err
        accounts_to_query = [account]
    else:
        try:
            accounts_to_query = await asyncio.to_thread(_list_mail_accounts, timeout)
        except AppleScriptTimeout:
            if output_format == "json":
                return _overview_json_error(
                    "account_listing_timeout",
                    account=account,
                    include_mailboxes=include_mailboxes,
                    include_recent=include_recent,
                    include_suggestions=include_suggestions,
                    max_recent=max_recent,
                    message="Error: Mail account listing timed out",
                    errors=["__account_listing__"],
                )
            return "Error: Mail account listing timed out"

    if not accounts_to_query:
        if output_format == "json":
            return _format_overview_json(
                [],
                [],
                account=account,
                include_mailboxes=include_mailboxes,
                include_recent=include_recent,
                include_suggestions=include_suggestions,
                max_recent=max_recent,
            )
        return _format_overview([], [], compact=output_format == "compact")

    async def run_one(acct: str):
        try:
            return acct, await asyncio.to_thread(
                _run_overview_one,
                acct,
                timeout,
                include_mailboxes,
                include_recent,
                max_recent,
                max_mailboxes,
            )
        except AppleScriptTimeout:
            return acct, AppleScriptTimeout(acct)

    results = await asyncio.gather(*(run_one(a) for a in accounts_to_query))

    parsed: List[Dict[str, Any]] = []
    errors: List[str] = []
    for acct, outcome in results:
        if isinstance(outcome, AppleScriptTimeout):
            errors.append(acct)
            continue
        parsed_acct = _parse_overview_account(outcome)
        if parsed_acct.get("parse_errors"):
            errors.extend(parsed_acct["parse_errors"])
        parsed.append(parsed_acct)

    if output_format == "json":
        return _format_overview_json(
            parsed,
            errors,
            account=account,
            include_mailboxes=include_mailboxes,
            include_recent=include_recent,
            include_suggestions=include_suggestions,
            max_recent=max_recent,
        )

    return _format_overview(
        parsed,
        errors,
        include_mailboxes=include_mailboxes,
        include_recent=include_recent,
        include_suggestions=include_suggestions,
        max_recent=max_recent,
        compact=output_format == "compact",
    )
