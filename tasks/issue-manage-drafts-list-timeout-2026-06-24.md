# Issue: `manage_drafts(action="list")` times out on large Exchange accounts

Date: 2026-06-24
Reporter: live institutional Mail draft workflow (plugin v3.7.1)
Severity: medium-high (blocks the exact-id Drafts verification path)
Status: open (recurrence of 2026-06-15 report)

## Summary

`manage_drafts(action="list")` repeatedly times out on a large institutional
Exchange account. The `subject_contains` filter does not help, because the scan
still walks the Drafts mailbox message-by-message over AppleScript and applies
the substring inside the loop; the substring only filters output, it does not
bound iteration. This is the same failure noted in earlier live operations.

## Live observation (2026-06-24)

- Three parallel `manage_drafts(action="list", subject_contains=...)` calls with
  different subject filters all returned `AppleScript timed out`.
- A subsequent single `action="list"` with `timeout=240` was on track to be slow
  as well and was aborted by the operator.

Impact: after `reply_to_email` (which does not return a draft id, see
`issue-reply-to-email-return-draft-id-2026-06-24.md`), the list scan is the only
content-based way to locate the new draft. With it timing out, there is no
programmatic verification path on this account; verification fell back to the
operator eyeballing Mail.app.

## Expected behavior

`manage_drafts(action="list")` should be bounded and fast by default:

- Accept a `limit` / `max_drafts` parameter and scan newest-first with an early
  exit once the limit (or a filtered match count) is reached.
- Read only lightweight properties per draft (id, subject, recipients, date);
  defer body reads unless explicitly requested.
- Keep `subject_contains` as an in-loop filter but stop after `limit` matches so a
  narrow lookup returns quickly even on a large Drafts mailbox.

## Relationship to other work

- The primary fix is the companion issue: if `reply_to_email` returns the
  `draft_id`, the list scan is no longer needed for post-draft verification.
- A header-based lookup (`issue-find-draft-by-in-reply-to-2026-06-24.md`) would
  also avoid the scan when only the source message id is known.

## Likely area to inspect

- `plugin/apple_mail_mcp/tools/manage.py` — `manage_drafts` `list` branch and its
  AppleScript enumeration of the Drafts mailbox.

## Acceptance

- `manage_drafts(action="list", limit=10)` returns within the default timeout on a
  Drafts mailbox with hundreds of drafts on TU Exchange.
- Newest-first ordering is guaranteed; `subject_contains` short-circuits after the
  match limit.
