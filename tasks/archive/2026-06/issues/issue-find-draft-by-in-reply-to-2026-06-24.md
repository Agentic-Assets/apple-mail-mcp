# Enhancement: find a draft by its source message via In-Reply-To / References

Date: 2026-06-24
Reporter: live institutional Mail draft workflow (plugin v3.7.1)
Severity: low-medium (enhancement; deterministic alternative to subject scans)
Status: open

## Summary

When an agent creates a reply draft, it knows the source message's *internet*
Message-ID (from `list_inbox_emails` / `search_emails` / `get_email_by_id`). A
reply draft embeds that value in its `In-Reply-To` (and `References`) headers.
There is currently no way to look a draft up by that linkage, so callers fall
back to subject-substring scans of the Drafts mailbox, which are slow and
ambiguous when multiple drafts share a subject.

Add a deterministic lookup: given a source message (by its internet Message-ID,
or by the inbox `message_id` from which the id can be read), return the matching
draft(s).

Possible shapes:

- `manage_drafts(action="find", in_reply_to="<internet-message-id>")`, or
- `find_draft_for_message(account=..., source_message_id=...)`.

## This answers an existing open question

`tasks/reference/id-first-refactor-spec.md` Open Question #1 asks whether Mail.app exposes a
Message-ID header accessor in AppleScript. **It does, and the plugin already uses
it**: `get_email_by_id` parses `in_reply_to` and `references` from the raw
`all headers` of a message and returns them (and `has_quoted_original`). So the
data needed for header matching is already accessible through the same path; this
enhancement reuses it on the Drafts mailbox.

Note: this is for exact draft-to-source linkage, not general thread grouping. The
spec's recommendation to keep subject-based grouping for `get_email_thread` can
stand; this is a narrower, deterministic draft locator.

## Why it helps

- Exact, unambiguous match even when several drafts share a subject (the recurring
  "do not delete by subject" hazard documented in
  `reply-body-insertion-failure-2026-06-18.md`).
- Avoids the `manage_drafts list` scan timeout on large Exchange accounts
  (`issue-manage-drafts-list-timeout-2026-06-24.md`).
- Lets an agent verify "the draft for inbox message X exists and has a body"
  using only the id it already holds.

## Priority relative to siblings

Lower than `issue-reply-to-email-return-draft-id-2026-06-24.md`. If `reply_to_email`
returns the `draft_id` directly, header lookup is rarely needed for the
immediately-created draft. It remains useful for drafts created in an earlier
session, or by another client, where only the source message id is known.

## Likely area to inspect

- `plugin/apple_mail_mcp/tools/search.py` — header parsing already implemented for
  `get_email_by_id` (`in_reply_to` / `references` from `all headers`).
- `plugin/apple_mail_mcp/tools/manage.py` — add a Drafts-scoped `find` branch that
  matches on the header value, bounded newest-first.

## Acceptance

- A `find`/`find_draft_for_message` call returns the exact draft id whose
  `In-Reply-To` equals the supplied source internet Message-ID, scanning
  newest-first and bounded, with a clear empty result when none matches.
