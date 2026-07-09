"""``manage_trash`` tool: move-to-trash, delete-permanent, and empty-trash via id-direct or filter-scan paths.

Patched names (``run_applescript``, ``_search_mail_records``, ``validate_account_name``)
are referenced via the ``manage`` facade so existing ``patch('...tools.manage.<name>')``
seams keep working."""

from apple_mail_mcp import server as _server
from apple_mail_mcp.backend.base import ToolError, serialize_tool_error, target_selector_deprecated_error
from apple_mail_mcp.bounded_scan import build_whose_id_list
from apple_mail_mcp.core import (
    AppleScriptTimeout,
    build_mailbox_ref,
    contains_any_condition,
    escape_applescript,
    inject_preferences,
    normalize_message_ids,
    normalize_search_terms,
)
from apple_mail_mcp.server import DESTRUCTIVE_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import manage
from apple_mail_mcp.tools.manage.helpers import (
    _check_message_ids_cap,
    _date_from_for_recent_days,
    _date_to_for_older_than,
    _deprecated_target_selectors,
    _filter_scan_disabled_error,
    _format_dry_run_records,
    _search_message_ids,
    _with_filter_scan_warning,
)


@mcp.tool(annotations=DESTRUCTIVE_TOOL_ANNOTATIONS)
@inject_preferences
def manage_trash(
    account: str | None = None,
    action: str = "move_to_trash",
    message_ids: list[str] | None = None,
    subject_keyword: str | None = None,
    subject_keywords: list[str] | None = None,
    sender: str | None = None,
    mailbox: str = "INBOX",
    max_deletes: int = 5,
    confirm_empty: bool = False,
    apply_to_all: bool = False,
    older_than_days: int | None = None,
    dry_run: bool = True,
    recent_days: float = 2.0,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
    """
    Manage trash operations - delete emails or empty trash.

    Preferred: pass ``message_ids`` from a prior list/search call.
    ``subject_keyword``, ``subject_keywords``, and ``sender`` are deprecated target
    selectors and return ``TARGET_SELECTOR_DEPRECATED`` even when
    ``allow_filter_scan=True``. Date/bulk paths (``older_than_days``,
    ``apply_to_all``) require ``allow_filter_scan=True`` (slow on large mailboxes).
    When dry_run=True (default), previews without acting; fast with message_ids.

    When ``message_ids`` is provided for ``move_to_trash`` or ``delete_permanent``,
    targets exact IDs and ignores keyword/sender filters.

    When ``account`` is None the configured ``DEFAULT_MAIL_ACCOUNT`` is used.

    Args:
        account: Account name (e.g., "Gmail", "Work"). Defaults to DEFAULT_MAIL_ACCOUNT.
        action: Action to perform: "move_to_trash", "delete_permanent", "empty_trash"
        message_ids: List of exact Mail message ids (preferred path)
        subject_keyword: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        subject_keywords: Deprecated schema-compat selector (same as subject_keyword).
        sender: Deprecated schema-compat selector. Returns
            ``TARGET_SELECTOR_DEPRECATED`` when ``message_ids`` is omitted.
        mailbox: Source mailbox (default: "INBOX", not used for empty_trash or delete_permanent)
        max_deletes: Maximum number of emails to delete (safety limit, default: 5)
        confirm_empty: Must be True to execute "empty_trash" action (safety confirmation)
        apply_to_all: Bulk trash without filters (requires allow_filter_scan=True)
        older_than_days: Optional age filter - only affect emails older than N days
            (requires ``allow_filter_scan=True`` when ``message_ids`` is omitted)
        dry_run: If True (default), preview what would be affected without acting
        recent_days: Recent window when using date/bulk filter scan (default: 2.0).
        allow_filter_scan: Opt in to slow date/bulk filter scans only (default: False).
            Does not enable subject/sender selectors.
        timeout: Optional AppleScript timeout in seconds (default: 300s).

    Returns:
        Confirmation message with details of deleted emails
    """
    if account is None:
        account = _server.DEFAULT_MAIL_ACCOUNT
    if not account:
        return "Error: account is required (and no DEFAULT_MAIL_ACCOUNT configured)."
    deprecated_selectors = _deprecated_target_selectors(
        subject_keyword=subject_keyword,
        subject_keywords=subject_keywords,
        sender=sender,
    )
    if message_ids is None and action != "empty_trash" and deprecated_selectors:
        return target_selector_deprecated_error(
            "manage_trash",
            deprecated_selectors,
            preferred="Call search_emails(...) or list_inbox_emails(...) first, then pass message_ids=[...].",
            discovery="search_emails(..., recent_days=..., limit=...) or list_inbox_emails(...)",
            exact_selector="message_ids",
        )

    validation_timeout = 30 if timeout is None else min(timeout, 30)
    account_err = manage.validate_account_name(account, timeout=validation_timeout)
    if account_err:
        return account_err

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)
    effective_timeout = timeout if timeout is not None else 300
    effective_recent_days = recent_days if older_than_days is None else 0

    if message_ids is not None:
        if action == "empty_trash":
            return "Error: message_ids cannot be used with empty_trash"

        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        cap_error = _check_message_ids_cap(normalized_ids, "manage_trash")
        if cap_error:
            return cap_error

        id_condition = build_whose_id_list(normalized_ids)

        if action == "move_to_trash":
            mode_label = "DRY RUN - PREVIEW TRASH BY IDS" if dry_run else "MOVING EMAILS TO TRASH BY IDS"
            move_script = "" if dry_run else "move aMessage to trashMailbox"
            result_verb = "Would trash" if dry_run else "Moved to trash"
            trash_setup = (
                ""
                if dry_run
                else """
                    set trashMailbox to mailbox "Trash" of targetAccount"""
            )
            mailbox_ref = build_mailbox_ref(mailbox, var_name="sourceMailbox")
        elif action == "delete_permanent":
            mode_label = (
                "DRY RUN - PREVIEW PERMANENT DELETE BY IDS" if dry_run else "PERMANENTLY DELETING EMAILS BY IDS"
            )
            move_script = "" if dry_run else "delete aMessage"
            result_verb = "Would permanently delete" if dry_run else "Permanently deleted"
            trash_setup = ""
            mailbox_ref = 'set sourceMailbox to mailbox "Trash" of targetAccount'
        else:
            return f"Error: Invalid action '{action}'. Use: move_to_trash, delete_permanent, empty_trash"

        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "{mode_label}" & return & return
                set deleteCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    {mailbox_ref}
                    {trash_setup}

                    set matchingMessages to every message of sourceMailbox whose {id_condition}
                    if (count of matchingMessages) > {max_deletes} then
                        set matchingMessages to items 1 thru {max_deletes} of matchingMessages
                    end if

                    repeat with aMessage in matchingMessages
                        try
                            set messageSubject to subject of aMessage
                            set messageSender to sender of aMessage
                            set messageDate to date received of aMessage

                            {move_script}

                            set outputText to outputText & "{result_verb}: " & messageSubject & return
                            set outputText to outputText & "   From: " & messageSender & return
                            set outputText to outputText & "   Date: " & (messageDate as string) & return & return
                            set deleteCount to deleteCount + 1
                        end try
                    end repeat

                    set outputText to outputText & "========================================" & return
                    set outputText to outputText & "REQUESTED IDS: {len(normalized_ids)}" & return
                    set outputText to outputText & "TOTAL: " & deleteCount & " email(s) {result_verb.lower()}" & return
                    set outputText to outputText & "========================================" & return

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''

        try:
            return manage.run_applescript(script, timeout=effective_timeout)
        except AppleScriptTimeout:
            return f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."

    # Refuse unbounded scans on destructive filter paths (move_to_trash and
    # delete_permanent). The id-direct path and empty_trash are exempt.
    # older_than_days provides a date_to bound so it is safe even when
    # recent_days=0; only refuse when BOTH are absent.
    if action != "empty_trash" and older_than_days is None and recent_days <= 0:
        tool_error = ToolError(
            code="UNBOUNDED_SCAN_REQUIRED",
            message=(
                "manage_trash refuses to scan without a date window; "
                "pass recent_days=7 or older_than_days=30 or message_ids=[...]"
            ),
            remediation={
                "preferred": "Pass recent_days=7 or older_than_days=30 or message_ids=[...]",
                "note": "Full-mailbox scans are disabled; bound this call.",
            },
        )
        return serialize_tool_error(tool_error)

    if action == "empty_trash":
        if not confirm_empty:
            return (
                "Error: empty_trash permanently deletes ALL messages in the trash. Set confirm_empty=True to proceed."
            )
        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "EMPTYING TRASH" & return & return

                try
                    set targetAccount to account "{safe_account}"
                    set trashMailbox to mailbox "Trash" of targetAccount
                    set messageCount to count of messages of trashMailbox
                    set deleteCount to 0

                    if messageCount > {max_deletes} then
                        set trashMessages to messages 1 thru {max_deletes} of trashMailbox
                    else
                        set trashMessages to messages of trashMailbox
                    end if

                    repeat with aMessage in trashMessages
                        delete aMessage
                        set deleteCount to deleteCount + 1
                    end repeat

                    set outputText to outputText & "✓ Emptied trash for account: {safe_account}" & return
                    set outputText to outputText & "   Deleted " & deleteCount & " of " & messageCount & " message(s)" & return
                    if deleteCount < messageCount then
                        set outputText to outputText & "   (limited by max_deletes=" & {max_deletes} & ")" & return
                    end if

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''
    elif action == "delete_permanent":
        # Safety check: require at least one filter or explicit apply_to_all
        if not subject_terms and not sender and not apply_to_all:
            return (
                "Error: Pass message_ids=[...] (preferred) or apply_to_all=True with "
                "allow_filter_scan=True. subject_keyword and sender are deprecated "
                "(TARGET_SELECTOR_DEPRECATED)."
            )

        if not allow_filter_scan:
            return _filter_scan_disabled_error("manage_trash")

        # Build search condition with escaped inputs
        conditions = []
        if subject_terms:
            conditions.append(contains_any_condition("subject", subject_terms))
        if sender:
            conditions.append(f'sender contains "{escape_applescript(sender)}"')

        if conditions:
            search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
            try:
                resolved_ids = _search_message_ids(
                    account=account,
                    mailbox="Trash",
                    subject_terms=subject_terms or None,
                    sender=sender,
                    limit=max_deletes,
                    timeout=search_timeout,
                    recent_days=effective_recent_days,
                )
            except AppleScriptTimeout:
                return _with_filter_scan_warning(
                    f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
                )
            if not resolved_ids:
                return _with_filter_scan_warning(f"No matching emails found in Trash for account '{account}'.")
            return _with_filter_scan_warning(
                manage_trash(
                    account=account,
                    action="delete_permanent",
                    message_ids=resolved_ids,
                    max_deletes=max_deletes,
                    dry_run=dry_run,
                    timeout=timeout,
                )
            )

        matching_messages_script = (
            f"if (count of messages of trashMailbox) > {max_deletes} then\n"
            f"                        set matchingMessages to messages 1 thru {max_deletes} of trashMailbox\n"
            f"                    else\n"
            f"                        set matchingMessages to messages of trashMailbox\n"
            f"                    end if"
        )

        mode_label = "DRY RUN - PREVIEW PERMANENT DELETE" if dry_run else "PERMANENTLY DELETING EMAILS"
        delete_script = "" if dry_run else "delete aMessage"
        result_verb = "Would permanently delete" if dry_run else "Permanently deleted"
        total_label = "TOTAL WOULD DELETE" if dry_run else "TOTAL DELETED"

        script = f'''
        tell application "Mail"
            with timeout of {effective_timeout} seconds
                set outputText to "{mode_label}" & return & return
                set deleteCount to 0

                try
                    set targetAccount to account "{safe_account}"
                    set trashMailbox to mailbox "Trash" of targetAccount
                    {matching_messages_script}
                    set matchingCount to count of matchingMessages

                    if matchingCount is 0 then
                        set targetMessages to {{}}
                    else if matchingCount > {max_deletes} then
                        set targetMessages to items 1 thru {max_deletes} of matchingMessages
                    else
                        set targetMessages to matchingMessages
                    end if

                    repeat with aMessage in targetMessages
                        try
                            set messageSubject to subject of aMessage
                            set messageSender to sender of aMessage

                            set outputText to outputText & "✓ {result_verb}: " & messageSubject & return
                            set outputText to outputText & "   From: " & messageSender & return & return

                            {delete_script}
                            set deleteCount to deleteCount + 1
                        end try
                    end repeat

                    set outputText to outputText & "========================================" & return
                    set outputText to outputText & "{total_label}: " & deleteCount & " email(s)" & return
                    set outputText to outputText & "========================================" & return

                on error errMsg
                    return "Error: " & errMsg
                end try

                return outputText
            end timeout
        end tell
        '''
    else:  # move_to_trash
        # Safety check: require at least one filter or explicit apply_to_all
        has_filter = bool(subject_terms) or bool(sender) or (older_than_days is not None and older_than_days > 0)
        if not has_filter and not apply_to_all:
            return (
                "Error: Pass message_ids=[...] (preferred), older_than_days, or "
                "apply_to_all=True with allow_filter_scan=True. subject_keyword and sender "
                "are deprecated (TARGET_SELECTOR_DEPRECATED)."
            )

        if not allow_filter_scan:
            return _filter_scan_disabled_error("manage_trash")

        if dry_run:
            try:
                records = manage._search_mail_records(
                    account=account,
                    mailbox=mailbox,
                    subject_terms=subject_terms or None,
                    sender=sender,
                    date_from=_date_from_for_recent_days(effective_recent_days),
                    date_to=_date_to_for_older_than(older_than_days),
                    include_content=False,
                    offset=0,
                    limit=max_deletes + 1,
                    timeout=timeout if timeout is not None else 45,
                    recent_days=effective_recent_days,
                )
            except AppleScriptTimeout:
                return _with_filter_scan_warning(
                    f"Error: manage_trash dry-run timed out on account '{account}'. "
                    "Prefer message_ids=[...] or a larger timeout."
                )
            return _with_filter_scan_warning(
                _format_dry_run_records(
                    "DRY RUN - PREVIEW TRASH",
                    records,
                    "Would trash",
                    max_deletes,
                )
            )

        search_timeout = timeout if timeout is not None else min(effective_timeout, 120)
        try:
            resolved_ids = _search_message_ids(
                account=account,
                mailbox=mailbox,
                subject_terms=subject_terms or None,
                sender=sender,
                date_from=_date_from_for_recent_days(effective_recent_days),
                date_to=_date_to_for_older_than(older_than_days),
                limit=max_deletes,
                timeout=search_timeout,
                recent_days=effective_recent_days,
            )
        except AppleScriptTimeout:
            return _with_filter_scan_warning(
                f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
            )
        if not resolved_ids:
            return _with_filter_scan_warning(f"No matching emails found in {mailbox} for account '{account}'.")
        return _with_filter_scan_warning(
            manage_trash(
                account=account,
                action="move_to_trash",
                message_ids=resolved_ids,
                mailbox=mailbox,
                max_deletes=max_deletes,
                dry_run=False,
                timeout=timeout,
            )
        )

    try:
        result = manage.run_applescript(script, timeout=effective_timeout)
        if action == "empty_trash":
            return result
        return _with_filter_scan_warning(result)
    except AppleScriptTimeout:
        if action == "empty_trash":
            return f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
        return _with_filter_scan_warning(
            f"Error: manage_trash timed out after {effective_timeout}s on account '{account}'."
        )
