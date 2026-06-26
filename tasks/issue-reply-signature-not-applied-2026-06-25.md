# Issue: `reply_to_email(include_signature=true)` can save a reply without the expected Mail signature

Date: 2026-06-25
Reporter: live institutional Mail draft workflow
Severity: medium
Status: open

## Summary

`reply_to_email` was called with `include_signature=true` while drafting a native
reply in Apple Mail. The saved draft contained the requested reply body and the
quoted thread, but the new reply portion did not include the configured Mail
signature. The visible new reply ended with a short manual closing only.

This creates false confidence for agents because the tool accepts
`include_signature=true` and defaults to signature inclusion, but the resulting
draft may still need manual signature repair before sending.

## Expected behavior

When `include_signature=true`, `reply_to_email` should either:

1. apply the configured/default Mail signature to the newly-created reply, or
2. return an explicit warning that no signature was applied, including the
   requested `signature_name` when one was supplied.

## Suggested fixes

- Return structured signature state from reply/compose/forward tools:
  `signature_requested`, `signature_name_requested`, `signature_applied`, and
  `signature_detected_in_new_body`.
- Verify signature presence only above the quoted-original boundary, not anywhere
  in the full thread text.
- Add a `list_signatures(account=...)` or `get_default_signature(account=...)`
  helper so agents can request a concrete signature by name before drafting.

## Acceptance

- A regression test covers `reply_to_email(include_signature=true)` and asserts
  the response distinguishes `applied` from `requested but missing`.
- A live draft verification path reports whether the new-body section includes a
  signature before the quoted original starts.
