# Issue: `reply_to_email` success output does not return the saved draft id

Date: 2026-06-24
Reporter: live institutional Mail draft workflow (plugin v3.7.1)
Severity: medium (blocks programmatic verification; high friction on large accounts)
Status: open

## Summary

`reply_to_email(mode="draft")` reports success but the returned payload contains
only `To` and `Subject` text. It does not return the saved draft's Apple Mail
`message_id` (nor its `internet_message_id`). Because the id is not surfaced, the
caller has no constant-cost handle to verify the draft afterward and must fall
back to `manage_drafts(action="list")`, which times out on large Exchange
accounts (see companion issue `issue-manage-drafts-list-timeout-2026-06-24.md`).

This is a standing ask: prior ops logs already requested "return draft ids from
`reply_to_email` success output." The verification hardening in
`tasks/reply-draft-verification-hardening-2026-06-19.md` added
`_verify_saved_reply_draft(draft_id=...)`, which means an exact draft id is
already computed internally. It is simply discarded before the response is built.

## Live observation (2026-06-24)

Multiple `reply_to_email(mode="draft")` calls on a large institutional Exchange
account reported success with only recipient and subject details in the tool
output. The drafts were later confirmed in Mail.app, but no draft id was returned
by any call, so programmatic verification was not possible from the tool output
alone.

Note (positive): the 2026-06-18 signature-only / body-drop failure did **not**
reproduce on v3.7.1 in this session. All three bodies inserted correctly,
including the two reply-all variants.

## Expected behavior

On `mode="draft"` and `mode="open"`, `reply_to_email` success payload should
include:

- `draft_id`: numeric Apple Mail id of the saved draft (the value already passed
  to `_verify_saved_reply_draft`).
- `internet_message_id`: the draft's Message-ID header when available (enables
  later header-based lookup; see `issue-find-draft-by-in-reply-to-2026-06-24.md`).
- `body_present` / `verification_status`: the structured verifier outcome
  (`found`, `body_missing`, `not_found`, `applescript_error`) from
  `_ReplyDraftVerification.status`, so callers can trust success without a second
  round-trip.

Apply the same change to `compose_email`, `forward_email`,
`create_rich_email_draft`, and `manage_drafts(action="create")` for consistency.

## Why this is the highest-impact fix

With `draft_id` in the response, post-draft verification becomes a single
constant-cost `get_email_by_id(mailbox="Drafts", message_id=...)`. That removes
the dependency on the `manage_drafts list` scan entirely, which is the slow path
on large Exchange accounts.

## Likely area to inspect

- `plugin/apple_mail_mcp/tools/compose.py` — the `reply_to_email` success-path
  response builder, downstream of `_verify_saved_reply_draft`.
- The success string assembly that currently emits only To/Subject.

## Acceptance

- `reply_to_email(mode="draft")` returns `draft_id` and `verification_status` in
  both text and JSON output formats.
- A regression test asserts the returned `draft_id` resolves via
  `get_email_by_id(mailbox="Drafts", message_id=draft_id)` and that the body
  sentinel is present above the quoted original.
