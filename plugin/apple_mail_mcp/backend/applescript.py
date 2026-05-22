"""AppleScript-backed implementation of the Mail backend Protocol.

Dormant in Phase A: existing tools continue to call ``core.run_applescript``
directly. Wave-2 migrations will route their I/O through this backend so
the capability-token check (``ScanWindow._issued_by``) is enforced at the
edge of every Mail.app call.
"""

from __future__ import annotations

from typing import Any

from apple_mail_mcp import core
from apple_mail_mcp.bounded_scan import build_bounded_message_scan
from apple_mail_mcp.backend.base import (
    InvalidationScope,
    MailBackend,
    ScanWindow,
    ToolError,
    WriteResult,
)


_ISSUER = "core.bounded_inbox_scan"


def _check_window(window: ScanWindow) -> None:
    """Enforce the ScanWindow capability token at every backend entry."""
    if window._issued_by != _ISSUER:
        raise ToolError(
            code="INVALID_SCAN_WINDOW",
            message=(
                "ScanWindow was not produced by core.bounded_inbox_scan; "
                "refusing to issue Mail.app scan."
            ),
            remediation={
                "preferred": (
                    "Construct ScanWindow via "
                    "apple_mail_mcp.bounded_scan.bounded_inbox_scan(...)"
                ),
            },
        )


class AppleScriptBackend(MailBackend):
    """Thin wrapper that builds AppleScript snippets and runs them via core."""

    # ------------------------------------------------------------------ read

    def list_messages(
        self,
        window: ScanWindow,
        *,
        fields: tuple[str, ...] = (
            "id",
            "subject",
            "sender",
            "date",
            "read",
        ),
        include_read: bool = True,
    ) -> list[dict[str, Any]]:
        _check_window(window)

        # Resolve a numeric upper bound from the ScanWindow. We prefer
        # explicit ``limit``, otherwise fall back to a conservative cap
        # derived from ``recent_days`` (callers should use
        # ``compute_scan_upper_bound`` for finer control).
        from apple_mail_mcp.bounded_scan import (
            MAX_SCAN_LIMIT,
            compute_scan_upper_bound,
        )

        if window.limit is not None:
            limit = min(int(window.limit), MAX_SCAN_LIMIT)
        elif window.recent_days is not None:
            limit = compute_scan_upper_bound(window.recent_days)
        else:
            # Defensive: bounded_inbox_scan should have rejected this,
            # but keep a sane upper bound just in case.
            limit = 200

        whose_condition = None if include_read else "read status is false"
        escaped_mailbox = core.escape_applescript(window.mailbox)

        # Build a self-contained script that resolves the mailbox by
        # iterating every account, then runs the bounded slice helper.
        slice_snippet = build_bounded_message_scan(
            "targetMailbox", limit, whose_condition
        )

        script = f'''
        tell application "Mail"
            set outputText to ""
            set targetMailbox to missing value
            repeat with anAccount in (every account)
                try
                    set targetMailbox to mailbox "{escaped_mailbox}" of anAccount
                    exit repeat
                end try
            end repeat
            if targetMailbox is missing value then
                return ""
            end if
            {slice_snippet}
            repeat with aMessage in candidateMessages
                try
                    set msgId to id of aMessage as string
                    set msgSubject to subject of aMessage
                    set msgSender to sender of aMessage
                    set msgDate to (date received of aMessage) as string
                    set msgRead to read status of aMessage
                    set outputText to outputText & msgId & "\t" & msgSubject & "\t" & msgSender & "\t" & msgDate & "\t" & (msgRead as string) & linefeed
                end try
            end repeat
            return outputText
        end tell
        '''
        raw = core.run_applescript(script)
        results: list[dict[str, Any]] = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            results.append(
                {
                    "id": parts[0],
                    "subject": parts[1],
                    "sender": parts[2],
                    "date": parts[3],
                    "read": parts[4].lower() == "true",
                }
            )
        return results

    def count_messages(
        self,
        window: ScanWindow,
        *,
        include_read: bool = True,
    ) -> int:
        _check_window(window)
        # TODO(wave-2): implement with bounded slice + count.
        raise NotImplementedError(
            "AppleScriptBackend.count_messages — wave-2 migration."
        )

    def search_messages(
        self,
        window: ScanWindow,
        *,
        query: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
    ) -> list[dict[str, Any]]:
        _check_window(window)
        # TODO(wave-2): implement using build_bounded_message_scan + filter snippet.
        raise NotImplementedError(
            "AppleScriptBackend.search_messages — wave-2 migration."
        )

    def get_message_by_id(
        self,
        *,
        mailbox: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        escaped_mailbox = core.escape_applescript(mailbox)
        # Validate message_id is numeric to prevent injection.
        clean_ids = core.normalize_message_ids([message_id])
        if not clean_ids:
            return None
        mid = clean_ids[0]
        script = f'''
        tell application "Mail"
            set targetMailbox to missing value
            repeat with anAccount in (every account)
                try
                    set targetMailbox to mailbox "{escaped_mailbox}" of anAccount
                    exit repeat
                end try
            end repeat
            if targetMailbox is missing value then
                return ""
            end if
            try
                set aMessage to (first message of targetMailbox whose id is {mid})
                set msgSubject to subject of aMessage
                set msgSender to sender of aMessage
                set msgDate to (date received of aMessage) as string
                set msgRead to read status of aMessage
                return "{mid}" & "\t" & msgSubject & "\t" & msgSender & "\t" & msgDate & "\t" & (msgRead as string)
            on error
                return ""
            end try
        end tell
        '''
        raw = core.run_applescript(script).strip()
        if not raw:
            return None
        parts = raw.split("\t")
        if len(parts) < 5:
            return None
        return {
            "id": parts[0],
            "subject": parts[1],
            "sender": parts[2],
            "date": parts[3],
            "read": parts[4].lower() == "true",
        }

    def list_mailboxes(
        self,
        *,
        account: str | None = None,
    ) -> list[dict[str, Any]]:
        if account is None:
            script = '''
            tell application "Mail"
                set outputText to ""
                repeat with anAccount in (every account)
                    set acctName to name of anAccount
                    repeat with mb in (every mailbox of anAccount)
                        try
                            set outputText to outputText & acctName & "\t" & (name of mb) & linefeed
                        end try
                    end repeat
                end repeat
                return outputText
            end tell
            '''
        else:
            escaped = core.escape_applescript(account)
            script = f'''
            tell application "Mail"
                set outputText to ""
                try
                    set anAccount to account "{escaped}"
                    set acctName to name of anAccount
                    repeat with mb in (every mailbox of anAccount)
                        try
                            set outputText to outputText & acctName & "\t" & (name of mb) & linefeed
                        end try
                    end repeat
                end try
                return outputText
            end tell
            '''
        raw = core.run_applescript(script)
        results: list[dict[str, Any]] = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            results.append({"account": parts[0], "name": parts[1]})
        return results

    def list_accounts(self) -> list[dict[str, Any]]:
        names = core.list_mail_account_names()
        return [{"name": n} for n in names]

    # ----------------------------------------------------------------- write

    def move_messages(
        self,
        *,
        source_mailbox: str,
        target_mailbox: str,
        message_ids: list[str],
    ) -> WriteResult:
        # TODO(wave-2): migrate manage.py:move_email through this seam.
        raise NotImplementedError(
            "AppleScriptBackend.move_messages — wave-2 migration."
        )

    def update_status(
        self,
        *,
        mailbox: str,
        message_ids: list[str],
        read: bool | None = None,
        flagged: bool | None = None,
    ) -> WriteResult:
        # TODO(wave-2): migrate manage.py:update_email_status through this seam.
        raise NotImplementedError(
            "AppleScriptBackend.update_status — wave-2 migration."
        )

    def empty_trash(
        self,
        *,
        account: str,
        older_than_days: int | None = None,
    ) -> WriteResult:
        # TODO(wave-2): migrate manage.py:manage_trash through this seam.
        raise NotImplementedError(
            "AppleScriptBackend.empty_trash — wave-2 migration."
        )

    def invalidate(self, scope: InvalidationScope) -> None:
        # Dormant: no in-process cache yet. Wave-2 will wire this to the
        # envelope-index cache once it lands.
        return None
