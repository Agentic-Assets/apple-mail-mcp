"""Pure AppleScript builders for the ``manage_drafts`` list/find actions.

No ``run_applescript`` call lives here; ``manage_drafts`` in ``compose.py``
builds these scripts and executes them.
"""

from apple_mail_mcp.applescript_snippets import recipient_addresses_block, thread_headers_block
from apple_mail_mcp.core import escape_applescript


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
                            set draftDate to "(unsent)"
                            try
                                set draftDate to (date sent of aDraft) as string
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
