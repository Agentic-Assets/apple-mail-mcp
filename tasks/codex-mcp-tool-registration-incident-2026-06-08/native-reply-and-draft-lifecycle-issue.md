# Native Reply and Draft Lifecycle Resolution - 2026-06-08

## Summary

The 2026-06-08 fix moved `reply_to_email` from synthetic reply construction to
Mail's native reply command, so Mail owns reply recipients, subject, and quoted
thread context.

The current 2026-06-19 contract is stricter: `reply_to_email` constructs and
assigns `reply_body` above the quoted-original block, verifies exact Drafts id
first when Mail exposes one, and only then falls back to bounded newest-Drafts
verification.

The 2026-06-08 fix also corrected the Drafts lifecycle issue found during live testing:
`manage_drafts(action="list", subject_contains=...)` now checks the bounded
front Drafts window where Mail placed freshly created native reply drafts. No
Drafts lookup or list path uses `every message` or an unbounded folder scan.
At that point, `reply_to_email(mode="draft")` performed post-save verification in
a fresh bounded newest-Drafts read before reporting success.

## Root Cause

Two separate behaviors combined into the visible failure:

1. `reply_to_email` had been rewritten to create a hidden `outgoing message` and
   manually prepend a plain-text quoted original. That avoided GUI races, but it
   did not match native Mail reply behavior.
2. `manage_drafts` assumed Mail returned Drafts oldest-first and scanned the tail
   of the mailbox. Live Mail showed newly created native reply drafts at positions
   3 and 4 out of 975, so the bounded tail scan missed them.

## Historical Implemented Behavior

Superseded 2026-06-19: current draft-mode reply creation omits `with opening
window` unless `mode="open"` is requested. It constructs and assigns the
intended `reply_body` above the quoted-original block before save, captures the
saved Drafts id when Mail exposes one, and verifies that exact artifact before
bounded fallback. Verification now distinguishes missing body from
body-after-quote artifacts. The bullets below describe the 2026-06-08
implementation, not the current contract.

- `reply_to_email` calls `reply foundMessage with opening window` by default.
- `reply_to_all=True` uses Mail's native `reply to all` option.
- The requested `reply_body` is inserted above Mail's native quoted thread.
- `mode="draft"` saves the reply and closes the compose window with saving
  enabled.
- `mode="draft"` skips the stale outgoing-message cap probe; draft creation must
  not be blocked by orphaned Mail `outgoing message` objects when the tool will
  save and close the native reply window.
- After the create/save AppleScript exits, `reply_to_email` verifies the saved
  draft in a separate bounded newest-Drafts read. The verifier matches the reply
  subject plus the first non-empty reply-body line, which is stable across Mail's
  line-ending normalization.
- The reply subject is captured before the draft window is saved/closed, avoiding
  the invalid-object error seen in live testing.
- If the verifier cannot find the saved draft, the tool returns an error instead
  of claiming success. It does not send mail.
- Manual quote assembly (`quoteHeader`, `quotedBody`, `fullBody`) is gone from
  the reply path.
- `manage_drafts(action="list")` reads only the bounded first Drafts window.
- Targeted draft open/send/delete lookup checks bounded head and bounded tail
  windows, so it tolerates Mail ordering differences without ever materializing
  the full Drafts mailbox.

## Verification Evidence

Automated:

- `.venv/bin/python -m pytest tests/test_compose_tools.py tests/test_phase_2_scan_hardening.py tests/test_scalability_24k.py tests/test_bounded_scan_contract.py tests/test_compose_security.py tests/test_compose_none_handling.py tests/test_tier3_hardening.py -q`
  passed.

Live Mail:

- Created a native reply draft with working-tree `reply_to_email(message_id="80833", mode="draft")`.
- Tool returned success with subject `Re: Your monthly AI Companion Basic limit has been reset`.
- `manage_drafts(action="list", subject_contains="Your monthly AI Companion Basic limit has been reset")`
  found the draft through the bounded Drafts list path.
- A bounded first-20 Drafts inspection confirmed the body contained the smoke
  marker and Mail's native quote header, including `Zoom <billing@zoom.us> wrote:`.
- Removed the three uniquely marked smoke drafts from the bounded first-20 Drafts
  window and verified zero remaining smoke-marker matches.

## Follow-Up Fix - Signature and Save Verification

Live testing against the IREI roundtable email showed two additional edge cases:

- Applying a named Mail signature after inserting the reply body could leave
  Mail's signature marker in the wrong place when the user changed signatures
  manually. The tool now applies the native Mail signature before pasting the
  reply body, so the default order is response body, signature, quoted thread.
- A bad signature name could create a partial blank native reply before failing.
  The tool now validates requested signature names before creating the reply.
- Mail/Gmail may not expose the saved draft to the Drafts mailbox until the
  create/save AppleScript exits. Draft verification now happens in a second
  AppleScript process with a bounded retry loop.

Live evidence:

- `reply_to_email(message_id="80650", mode="draft")` with no signature override
  returned `Reply saved as draft!` only after post-save verification passed.
- A bounded newest-30 Drafts inspection found the smoke draft and confirmed
  marker position `1`, signature position `129`, and Taylor's native quote
  position `390`.
- Removed only uniquely marked smoke drafts from the bounded newest Drafts
  window and verified zero smoke-marker matches remained.
