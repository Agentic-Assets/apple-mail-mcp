# Issue: Draft attachment state is not reliably applied or verifiable after `reply_to_email`

Date: 2026-06-25
Reporter: live institutional Mail draft workflow
Severity: medium-high
Status: open

## Summary

In a native reply draft workflow, `reply_to_email` accepted an `attachments` path
and created a draft, but the tool returned a verification warning that the newest
Drafts window could not be verified. During user review, the attachment was not
visibly present.

The current MCP surface does not provide a reliable Drafts-scoped attachment
check, so an agent cannot distinguish "attachment was not added" from
"attachment exists but the verification tool cannot inspect Drafts."

## Relationship to existing issue

This is related to GitHub issue #29, which covers `reply_to_email` body and
attachment fragility. This note adds a narrower verification requirement: even
when a draft id is known, the MCP surface should provide Drafts-aware attachment
inspection and a repair path.

## Expected behavior

For any draft-creating tool that accepts `attachments`, the response should
include structured attachment verification:

- `attachments_requested`: absolute paths passed by the caller.
- `attachments_applied`: filenames, sizes, and count found on the saved draft.
- `attachment_verification_status`: `verified`, `missing`, `draft_not_found`, or
  `unsupported`.
- `draft_id`: exact Apple Mail Drafts message id, so follow-up tools can inspect
  or repair the same draft.

## Suggested tool additions

- `list_draft_attachments(account=..., draft_id=...)`
- `add_draft_attachment(account=..., draft_id=..., path=...)`
- `remove_draft_attachment(account=..., draft_id=..., filename=...)`
- `verify_draft(account=..., draft_id=..., expected_body_contains=..., expected_attachments=..., expected_signature=true)`

## Acceptance

- `reply_to_email(..., attachments="/path/file.docx", mode="draft")` returns the
  saved `draft_id` and a verified attachment count for that exact draft.
- A follow-up `list_draft_attachments(draft_id=...)` returns the same attachment
  filename and nonzero size.
- If Mail fails to attach the file, the tool returns an actionable warning before
  the user opens the draft.
