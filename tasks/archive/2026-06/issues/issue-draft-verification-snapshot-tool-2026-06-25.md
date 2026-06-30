# Enhancement: add a single `verify_draft` snapshot tool for body, recipients, signature, and attachments

Date: 2026-06-25
Reporter: live institutional Mail draft workflow
Severity: medium
Status: open

## Summary

The current draft workflow requires several imperfect checks after
`reply_to_email`:

- `reply_to_email` may not return `draft_id`.
- `manage_drafts(action="list")` gives a snippet but not a full body boundary,
  signature state, or attachment state.
- `get_email_by_id(mailbox="Drafts")` can return metadata and preview without a
  full enough body for send-readiness verification.
- `list_email_attachments` is inbox-oriented and does not provide a clear
  Drafts-scoped attachment check.

Agents need one Drafts-scoped verification tool that can answer, "Is this exact
draft ready for the user to review and send?"

## Proposed tool

```text
verify_draft(
  account,
  draft_id,
  expected_to=None,
  expected_cc=None,
  expected_subject=None,
  expected_body_contains=None,
  expected_attachments=None,
  expected_signature=None,
  require_quoted_original=None
)
```

## Suggested response

Return structured JSON:

```json
{
  "draft_id": "12345",
  "found": true,
  "recipients": {"to": [], "cc": [], "bcc": []},
  "subject": "Re: Example",
  "body_preview": "Hi...",
  "body_contains_expected": true,
  "signature": {"requested": true, "detected_above_quote": false},
  "attachments": {
    "expected": ["file.docx"],
    "found": [],
    "status": "missing"
  },
  "quoted_original": {"detected": true},
  "warnings": []
}
```

## Acceptance

- The tool is bounded to a single Drafts message id and does not scan the whole
  mailbox.
- It reports body, recipients, signature, attachment, and quoted-thread status
  for the same draft.
- It returns clear mismatch warnings suitable for agent final responses and for
  blocking automated "draft ready" claims.
