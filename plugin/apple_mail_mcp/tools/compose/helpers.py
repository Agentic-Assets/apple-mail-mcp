"""Impure compose helpers: outgoing-window probes, account/signature/from resolution, send gating, and save-as-draft.

Patched names (run_applescript, subprocess, validate_account_name, the ``server`` module) are referenced via the ``compose`` facade so existing ``patch('...compose.run_applescript')`` test seams keep working."""

import time

from apple_mail_mcp.core import AppleScriptTimeout, escape_applescript
from apple_mail_mcp.tools import compose
from apple_mail_mcp.tools.compose.constants import MAX_OPEN_COMPOSE_WINDOWS
from apple_mail_mcp.tools.compose.lookup_scripts import _applescript_id_list_literal


def _clean_applescript_error(exc: Exception) -> str:
    """Return an exception message with the AppleScript wrapper prefix removed."""
    err = str(exc)
    for prefix in ("AppleScript error: ", "AppleScript execution failed: "):
        if err.startswith(prefix):
            return err[len(prefix) :]
    return err


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
        raw = compose.run_applescript(script, timeout=timeout).strip()
        return int(raw) if raw.lstrip("-").isdigit() else -1
    except Exception:  # noqa: BLE001 — probe must never propagate; fail-open
        return -1


def _list_outgoing_message_ids(timeout: int | None = None) -> list[str]:
    """Return the ids of every currently-open outgoing message (compose window).

    Snapshot the open compose windows *before* opening a new draft so the
    post-open save can target only the newly-created window via an id diff.
    Returns ``[]`` when the probe fails (fail-open), degrading the save to
    best-effort rather than blocking the draft.
    """
    script = """
    tell application "Mail"
        set idList to {}
        try
            repeat with composeMessage in outgoing messages
                set end of idList to ((id of composeMessage) as string)
            end repeat
        end try
        set AppleScript's text item delimiters to linefeed
        set idText to idList as string
        set AppleScript's text item delimiters to ""
        return idText
    end tell
    """
    try:
        raw = (
            compose.run_applescript(script) if timeout is None else compose.run_applescript(script, timeout=timeout)
        ).strip()
    except Exception:  # noqa: BLE001 — snapshot probe must fail-open
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


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


def _resolve_account(account: str | None, timeout: int | None = None) -> tuple[str | None, str | None]:
    """Resolve an account argument against ``DEFAULT_MAIL_ACCOUNT``.

    Returns ``(resolved_account, error_message)``. Tools call this at the top
    of their body so callers can omit ``account`` when a default is configured
    via the ``DEFAULT_MAIL_ACCOUNT`` env var. The attribute is read lazily off
    ``apple_mail_mcp.server`` so tests can monkeypatch it after import.
    """
    if account is None or account == "":
        account = compose._server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return None, ("Error: No account specified and no DEFAULT_MAIL_ACCOUNT env var set.")
    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = compose.validate_account_name(account, timeout=validation_timeout)
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
    default_signature = compose._server.DEFAULT_MAIL_SIGNATURE
    return default_signature.strip() if default_signature else None


def _validate_signature_name(signature_name: str | None, timeout: int | None = None) -> str | None:
    """Return an error string when a requested Mail signature does not exist."""
    if not signature_name:
        return None
    safe_signature = escape_applescript(signature_name)
    validation_timeout = 30 if timeout is None else min(timeout, 30)
    script = f'''
    tell application "Mail"
        set availableSignatures to {{}}
        repeat with sig in signatures
            set sigName to name of sig as string
            if sigName is "{safe_signature}" then
                return ""
            end if
            set end of availableSignatures to sigName
        end repeat

        set oldDelimiters to AppleScript's text item delimiters
        set AppleScript's text item delimiters to ", "
        set availableText to availableSignatures as string
        set AppleScript's text item delimiters to oldDelimiters

        if availableText is "" then
            return "Error: Mail signature \\"{safe_signature}\\" not found."
        end if
        return "Error: Mail signature \\"{safe_signature}\\" not found. Available signatures: " & availableText
    end tell
    '''
    try:
        result = compose.run_applescript(script, timeout=validation_timeout).strip()
    except AppleScriptTimeout:
        return (
            f"Error: AppleScript timed out while validating Mail signature {signature_name!r}. "
            "Try again or pass include_signature=False."
        )
    except Exception as e:  # noqa: BLE001 - return a tool-facing error instead of creating a partial draft
        return f"Error: Could not validate Mail signature {signature_name!r}: {_clean_applescript_error(e)}"
    return result or None


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
        result = (compose.run_applescript(script) or "").strip()
    else:
        result = (compose.run_applescript(script, timeout=timeout) or "").strip()
    return result or None


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
    raw = (
        compose.run_applescript(script) if timeout is None else compose.run_applescript(script, timeout=timeout)
    ) or ""
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


def _send_blocked(mode: str | None) -> str | None:
    """Return an error when the active server mode disallows sending."""
    if mode != "send":
        return None
    if compose._server.READ_ONLY:
        return "Error: Sending is disabled in read-only mode."
    if compose._server.DRAFT_SAFE:
        return "Error: Sending is disabled in draft-safe mode. Use mode='draft' or mode='open'."
    return None


def _save_new_compose_window_as_draft(
    *,
    prior_outgoing_ids: "set[str] | list[str] | None" = None,
    close_after_save: bool = False,
    retries: int = 10,
    delay_seconds: float = 0.5,
    timeout: int | None = None,
) -> bool:
    """Save the compose window opened after ``prior_outgoing_ids`` was captured.

    ``prior_outgoing_ids`` is the set of ``outgoing message`` ids that existed
    *before* the new draft window opened (via ``open -a Mail``). The save targets
    the first outgoing message whose id is **not** in that set, so a pre-existing,
    unrelated compose window is never saved or closed by mistake. When the set is
    empty/None (no other window can be open, or the snapshot probe failed) the
    first outgoing message is used as a best-effort fallback.

    The diff also drives the per-retry wait: a freshly ``open``-ed ``.eml`` may
    take a moment to materialize as an outgoing message, so "no new window yet"
    returns ``not-found`` and retries instead of grabbing the wrong window.
    """
    prior_list_literal = _applescript_id_list_literal(prior_outgoing_ids)
    close_script = ""
    if close_after_save:
        close_script = """
            delay 0.2
            try
                close (window of targetMessage) saving no
            end try
        """
    script = f"""
    tell application "Mail"
        try
            set priorIds to {prior_list_literal}
            set targetMessage to missing value
            repeat with candidateMessage in outgoing messages
                set candidateId to (id of candidateMessage) as string
                if priorIds does not contain candidateId then
                    set targetMessage to candidateMessage
                    exit repeat
                end if
            end repeat
            if targetMessage is missing value then
                return "not-found"
            end if
            save targetMessage
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
            result = compose.run_applescript(script).strip().lower()
        else:
            result = compose.run_applescript(script, timeout=timeout).strip().lower()
        if result == "saved":
            return True
        if result.startswith("error:"):
            break
        time.sleep(delay_seconds)
    return False
