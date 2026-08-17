"""Pure AppleScript builders for the ``manage_drafts`` list/find/cleanup actions.

No ``run_applescript`` call lives here; ``manage_drafts`` in ``manage.py``
builds these scripts and executes them.
"""

from apple_mail_mcp.applescript_snippets import recipient_addresses_block, thread_headers_block
from apple_mail_mcp.core import escape_applescript
from apple_mail_mcp.tools.compose.constants import DRAFT_LIST_CAP


def _indent_applescript_block(block: str, spaces: int) -> str:
    """Indent a generated AppleScript block for readable f-string insertion."""
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in block.splitlines())


def _build_manage_drafts_subject_filter_script(subject_contains: str | None, *, indent: int) -> str:
    """Build the in-loop subject filter shared by Drafts list and find actions."""
    if not subject_contains:
        return ""
    safe_subject_contains = escape_applescript(subject_contains)
    block = f'''ignoring case
    if draftSubject does not contain "{safe_subject_contains}" then
        set skipThisDraft to true
    end if
end ignoring'''
    return _indent_applescript_block(block, indent)


def _build_manage_drafts_list_script(
    *,
    safe_account: str,
    list_limit: int,
    hide_empty: bool,
    subject_contains: str | None,
) -> str:
    """Build AppleScript for bounded newest-first Drafts listing."""
    hide_empty_flag = "true" if hide_empty else "false"
    subject_filter_script = _build_manage_drafts_subject_filter_script(subject_contains, indent=24)
    to_recipients_script = recipient_addresses_block(
        message_var="aDraft",
        recipient_kind="to",
        output_var="draftTo",
        sanitize_fn=None,
    )
    return f'''
        tell application "Mail"
            set hideEmpty to {hide_empty_flag}
            set draftLines to ""
            set shownCount to 0

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount

                -- Bounded newest-first window. Real Mail Drafts accounts have
                -- shown just-created native replies near the front; never use
                -- `every message` or an unbounded folder scan here.
                set totalDrafts to count of messages of draftsMailbox
                set headEnd to totalDrafts
                if headEnd > {list_limit} then set headEnd to {list_limit}
                if totalDrafts is 0 then
                    set draftMessages to {{}}
                else
                    set draftMessages to messages 1 thru headEnd of draftsMailbox
                end if

                repeat with aDraft in draftMessages
                    if shownCount >= {list_limit} then exit repeat
                    try
                        set skipThisDraft to false
                        set draftSubject to subject of aDraft
                        set draftId to (id of aDraft) as string
                        {subject_filter_script}

                        if skipThisDraft then
                            -- filtered out by subject_contains
                        else
                            -- Drafts that have never been sent leave `date sent`
                            -- unset, which previously fell back to the literal
                            -- "(unsent)" string here. `date received` is always
                            -- populated on Mail's message class (it is set when
                            -- the draft is created/saved), so use that instead
                            -- to report a real drafted/received date.
                            set draftDate to "(unknown)"
                            try
                                set draftDate to (date received of aDraft) as string
                            end try

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
                                {to_recipients_script}

                                set shownCount to shownCount + 1
                                set draftLines to draftLines & "✉ " & draftSubject & return
                                set draftLines to draftLines & "   Id: " & draftId & "   To: " & draftTo & return
                                set draftLines to draftLines & "   Created: " & (draftDate as string) & return
                                if bodySnippet is not "" then
                                    set draftLines to draftLines & "   " & bodySnippet & return
                                end if
                                set draftLines to draftLines & return
                            end if
                        end if
                    end try
                end repeat

            on error errMsg
                return "Error: " & errMsg
            end try

            return "DRAFT EMAILS - {safe_account}" & return & return & "Found " & shownCount & " draft(s)" & return & return & draftLines
        end tell
        '''


def _build_manage_drafts_cleanup_script(
    *,
    safe_account: str,
    dry_run: bool,
    max_deletes: int,
) -> str:
    """Build AppleScript for the bounded blank-Drafts cleanup sweep."""
    dry_run_flag = "true" if dry_run else "false"
    mode_label = "PREVIEW (dry run)" if dry_run else "DELETING"
    return f'''
        tell application "Mail"
            set isDryRun to {dry_run_flag}
            set maxDeletes to {max_deletes}
            set reportLines to ""
            set emptyCount to 0
            set actedCount to 0
            set skippedCount to 0
            set failedCount to 0

            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount

                -- Bounded newest-first window clamped to the live count: an
                -- out-of-range upper bound raises -1719 ("Invalid index.")
                -- instead of clamping, an empty mailbox raises for every slice
                -- form, and `count of messages` can still read stale-high, so
                -- the slice keeps its own guard and degrades to an empty window.
                set totalDrafts to count of messages of draftsMailbox
                set headEnd to totalDrafts
                if headEnd > {DRAFT_LIST_CAP} then set headEnd to {DRAFT_LIST_CAP}
                if totalDrafts is 0 then
                    set draftMessages to {{}}
                else
                    set draftMessages to {{}}
                    try
                        set draftMessages to messages 1 thru headEnd of draftsMailbox
                    end try
                end if

                -- Collect empty drafts first (subject blank AND body empty), then
                -- act on them by reference so deletion does not shift indices.
                --
                -- Emptiness must be positively established before anything is
                -- deleted. A failed `content` read is absence of evidence, not
                -- evidence of emptiness: leaving draftBody as "" on a throw
                -- classified a real draft with real content as blank and
                -- permanently deleted it. bodyReadOk separates the three states
                -- (read-ok-and-empty, read-ok-and-non-empty, read-failed) so
                -- only the first is ever deletable, and every swallowed read is
                -- counted instead of dropped.
                set emptyDrafts to {{}}
                repeat with aDraft in draftMessages
                    try
                        set draftSubject to subject of aDraft
                        set bodyReadOk to false
                        set draftBody to ""
                        try
                            set draftBody to content of aDraft
                            set bodyReadOk to true
                        end try
                        set AppleScript's text item delimiters to {{return, linefeed, tab, space}}
                        set bodyParts to text items of draftBody
                        set AppleScript's text item delimiters to ""
                        set bodyStripped to bodyParts as string
                        if not bodyReadOk then
                            set skippedCount to skippedCount + 1
                            set reportLines to reportLines & "   • skipped draft (body unreadable, not classified)" & return
                        end if
                        if bodyReadOk and draftSubject is "" and bodyStripped is "" then
                            set end of emptyDrafts to aDraft
                        end if
                    on error
                        set skippedCount to skippedCount + 1
                        set reportLines to reportLines & "   • skipped draft (subject or body unreadable)" & return
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
                    on error errDraftAction
                        set failedCount to failedCount + 1
                        set reportLines to reportLines & "   • blank draft not removed: " & errDraftAction & return
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
            if skippedCount > 0 then
                set reportSummary to reportSummary & " Skipped " & skippedCount & " draft(s) whose subject or body could not be read; they were not classified and not removed."
            end if
            if failedCount > 0 then
                set reportSummary to reportSummary & " " & failedCount & " blank draft(s) could not be removed."
            end if
            return reportHeader & reportSummary & return & return & reportLines
        end tell
        '''


def _build_manage_drafts_find_script(
    *,
    safe_account: str,
    list_limit: int,
    in_reply_to: str,
    subject_contains: str | None,
) -> str:
    """Build AppleScript for bounded Drafts header lookup."""
    safe_in_reply_to = escape_applescript(in_reply_to.strip("<> "))
    subject_filter_script = _build_manage_drafts_subject_filter_script(subject_contains, indent=28)
    thread_headers_script = thread_headers_block(
        message_var="aDraft",
        in_reply_to_var="inReplyToValue",
        references_var="referencesValue",
        sanitize_fn=None,
    )
    return f'''
        tell application "Mail"
            set outputText to "FIND DRAFTS BY THREAD HEADER - {safe_account}" & return & return
            set shownCount to 0
            try
                set targetAccount to account "{safe_account}"
                set draftsMailbox to mailbox "Drafts" of targetAccount
                set totalDrafts to count of messages of draftsMailbox
                set headEnd to totalDrafts
                if headEnd > {list_limit} then set headEnd to {list_limit}
                if totalDrafts is 0 then
                    set draftMessages to {{}}
                else
                    set draftMessages to messages 1 thru headEnd of draftsMailbox
                end if

                repeat with aDraft in draftMessages
                    try
                        set skipThisDraft to false
                        set draftSubject to subject of aDraft as string
                        {subject_filter_script}
                        if skipThisDraft then
                            -- subject filter excluded this draft
                        else
                            {thread_headers_script}

                            if inReplyToValue contains "{safe_in_reply_to}" or referencesValue contains "{safe_in_reply_to}" then
                                set draftId to id of aDraft as string
                                set outputText to outputText & "✉ " & draftSubject & return
                                set outputText to outputText & "   Id: " & draftId & return
                                set outputText to outputText & "   In-Reply-To: " & inReplyToValue & return
                                set outputText to outputText & "   References: " & referencesValue & return & return
                                set shownCount to shownCount + 1
                            end if
                        end if
                    end try
                end repeat
            on error errMsg
                return "Error: " & errMsg
            end try
            return outputText & "Found " & shownCount & " matching draft(s)" & return
        end tell
        '''
