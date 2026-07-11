"""``get_inbox_overview`` tool plus its per-account script builder, parser, and formatters.

``run_applescript``, ``validate_account_name``, and ``_list_mail_accounts`` route
through the ``inbox`` facade so the existing test patch seams keep firing."""

import asyncio
from typing import Any

from apple_mail_mcp.core import (
    AppleScriptTimeout,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
)
from apple_mail_mcp.core.reply_state import DraftsSnapshot, reply_state_tags, was_replied_fragment
from apple_mail_mcp.server import READ_ONLY_TOOL_ANNOTATIONS, mcp
from apple_mail_mcp.tools import inbox
from apple_mail_mcp.tools.reply_state_wiring import annotate_rows_with_reply_state, build_draft_scan_status


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
        RECENT|||subject|||sender|||date|||read|||wasRepliedToken
        MAILBOX_CAPPED|||accountName|||cap
        ...

    ``wasRepliedToken`` is Mail's native ``was replied to`` property, read
    unconditionally in the same per-message pass (no new AppleScript round
    trip; see ``core.reply_state.was_replied_fragment``).

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
                        {was_replied_fragment()}
                        set end of resultLines to "RECENT|||" & messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & wasRepliedToken
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
    timeout: int | None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
) -> str:
    effective_timeout = timeout if timeout is not None else 180
    return inbox.run_applescript(
        _build_overview_one_account_script(
            account,
            include_mailboxes=include_mailboxes,
            include_recent=include_recent,
            max_recent=max_recent,
            max_mailboxes=max_mailboxes,
        ),
        timeout=effective_timeout,
    )


def _parse_overview_account(raw: str) -> dict[str, Any]:
    """Parse one account's overview payload."""
    result: dict[str, Any] = {
        "account": None,
        "unread": None,
        "total": None,
        "error": None,
        "mailboxes": [],  # list of (name, unread_count) tuples
        "recent": [],  # list of dicts
        "mailboxes_truncated": False,
    }
    parse_errors: list[str] = []
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
                    parse_errors.append(f"Invalid HEADER counts for {parts[1]!r}: {parts[2]!r}, {parts[3]!r}")
        elif tag in ("MAILBOX", "SUBMAILBOX") and len(parts) >= 3:
            try:
                result["mailboxes"].append((parts[1], int(parts[2])))
            except ValueError:
                parse_errors.append(f"Invalid {tag} unread count for {parts[1]!r}: {parts[2]!r}")
        elif tag == "MAILBOX_CAPPED" and len(parts) >= 2:
            result["mailboxes_truncated"] = True
        elif tag == "RECENT" and len(parts) >= 5:
            result["recent"].append(
                {
                    "subject": parts[1],
                    "sender": parts[2],
                    "date": parts[3],
                    "is_read": parts[4].strip().lower() == "true",
                    "was_replied_to": len(parts) > 5 and parts[5].strip().lower() == "true",
                }
            )
        elif tag == "FATAL" and len(parts) >= 2:
            result["error"] = parts[1]
    if parse_errors:
        result["parse_errors"] = parse_errors
    return result


def _format_overview(
    accounts: list[dict[str, Any]],
    errors: list[str],
    *,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    compact: bool = False,
) -> str:
    """Format combined per-account overview payloads into the legacy text shape."""
    lines: list[str] = []
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
                lines.append("  ⚠ Mailbox list truncated — account has more mailboxes than the cap allows.")

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
            tags = reply_state_tags(r.get("was_replied_to"), r.get("has_draft"))
            tag_text = f" {' '.join(tags)}" if tags else ""
            lines.append("")
            lines.append(f"{indicator}{tag_text} {r['subject']}")
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
            lines.append(
                "2. 🔍 Search for action items - Look for keywords like 'urgent', 'action required', 'deadline'"
            )
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


def _overview_suggestions(total_unread: int) -> list[str]:
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


_SKIPPED_DRAFT_SCAN: dict[str, Any] = {"status": "skipped", "scanned": 0, "accounts": []}


def _overview_json_error(
    error: str,
    *,
    account: str | None = None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    message: str | None = None,
    errors: list[str] | None = None,
    draft_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
        "draft_scan": draft_scan if draft_scan is not None else dict(_SKIPPED_DRAFT_SCAN),
    }
    if account is not None:
        payload["account"] = account
    if message is not None:
        payload["message"] = message
    return payload


def _format_overview_json(
    accounts: list[dict[str, Any]],
    errors: list[str],
    *,
    account: str | None = None,
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    draft_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return structured overview payload for JSON mode.

    ``recent`` items already carry ``was_replied_to``/``has_draft`` when the
    caller pre-annotated them via ``annotate_rows_with_reply_state``; this
    only threads the aggregate *draft_scan* onto the top-level payload.
    """
    total_unread = 0
    account_rows: list[dict[str, Any]] = []
    for acct in accounts:
        row: dict[str, Any] = {"account": acct.get("account")}
        if acct.get("error"):
            row["error"] = acct["error"]
        else:
            row["unread"] = acct.get("unread") or 0
            row["total"] = acct.get("total") or 0
            total_unread += row["unread"]
            if include_mailboxes:
                row["mailboxes"] = [{"path": name, "unread": unread} for name, unread in acct.get("mailboxes", [])]
                if acct.get("mailboxes_truncated"):
                    row["mailboxes_truncated"] = True
            if include_recent:
                row["recent"] = acct.get("recent", [])[:max_recent]
        account_rows.append(row)

    payload: dict[str, Any] = {
        "output_format": "json",
        "include_mailboxes": include_mailboxes,
        "include_recent": include_recent,
        "include_suggestions": include_suggestions,
        "max_recent": max_recent,
        "total_unread": total_unread,
        "accounts": account_rows,
        "suggestions": _overview_suggestions(total_unread) if include_suggestions else [],
        "errors": errors,
        "draft_scan": draft_scan if draft_scan is not None else dict(_SKIPPED_DRAFT_SCAN),
    }
    if account is not None:
        payload["account"] = account
    return payload


@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def get_inbox_overview(
    account: str | None = None,
    output_format: str = "text",
    include_mailboxes: bool = True,
    include_recent: bool = True,
    include_suggestions: bool = True,
    max_recent: int = 10,
    max_mailboxes: int = 100,
    timeout: int | None = None,
    include_draft_state: bool = True,
) -> str | dict[str, Any]:
    """
    Get a comprehensive overview of your email inbox status across all accounts.

    Each account is queried sequentially, one AppleScript call at a time
    (Mail.app AppleScript is serialized behind a single-flight lock), so a
    single slow account (e.g. a large Exchange inbox) does not corrupt the
    rest of the overview. It appears as an entry in a `PARTIAL` line and the
    rest of the data is returned anyway.

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
        include_draft_state: When True (default), correlate each recent row
            against a bounded per-account Drafts snapshot and populate
            ``has_draft`` (JSON: true/false/null; text: ``[HAS DRAFT]``).
            ``was_replied_to`` is always present regardless (native
            property, no extra call). False skips the Drafts scan: JSON's
            ``draft_scan.status`` becomes ``"skipped"``, ``has_draft`` null.

    Returns:
        Comprehensive overview including unread counts, optional mailbox
        structure, recent preview, and optional AI suggestions. JSON mode
        returns a structured dict whose recent-email rows always carry
        ``was_replied_to`` (bool) and ``has_draft`` (bool or null), plus a
        top-level ``draft_scan`` object: ``{"status": "ok"|"error"|
        "skipped", "scanned": N, "accounts": [...]}``. Text mode tags
        matching recent lines with ``[REPLIED]``/``[HAS DRAFT]``.
    """
    if output_format not in {"text", "compact", "json"}:
        return "Error: Invalid output_format. Use: text, compact, json"

    if account:
        validation_timeout = 30 if timeout is None else min(timeout, 30)
        account_err = inbox.validate_account_name(account, timeout=validation_timeout)
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
            accounts_to_query = await asyncio.to_thread(inbox._list_mail_accounts, timeout)
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

    async def run_one(acct: str) -> tuple[str, str | AppleScriptTimeout]:
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

    results = [await run_one(a) for a in accounts_to_query]

    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for acct, outcome in results:
        if isinstance(outcome, AppleScriptTimeout):
            errors.append(acct)
            continue
        parsed_acct = _parse_overview_account(outcome)
        if parsed_acct.get("parse_errors"):
            errors.extend(parsed_acct["parse_errors"])
        parsed.append(parsed_acct)

    # has_draft correlation runs one account at a time (each `parsed` entry
    # already scopes its own "recent" rows to one account), sharing a single
    # snapshot cache so a repeated account across calls is never re-scanned.
    draft_timeout = timeout if timeout is not None else 60
    snapshots: dict[str, DraftsSnapshot] = {}
    for parsed_acct_row in parsed:
        if parsed_acct_row.get("error"):
            continue
        snapshots = annotate_rows_with_reply_state(
            parsed_acct_row.get("recent", []),
            runner=inbox.run_applescript,
            timeout=draft_timeout,
            include_draft_state=include_draft_state,
            account=parsed_acct_row.get("account"),
            snapshots=snapshots,
        )
    draft_scan = build_draft_scan_status(snapshots)

    if output_format == "json":
        return _format_overview_json(
            parsed,
            errors,
            account=account,
            include_mailboxes=include_mailboxes,
            include_recent=include_recent,
            include_suggestions=include_suggestions,
            max_recent=max_recent,
            draft_scan=draft_scan,
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
