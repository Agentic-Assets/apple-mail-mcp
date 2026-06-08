# Native Reply and Draft Lifecycle Resolution - 2026-06-08

## Summary

`reply_to_email` now uses Mail's native reply composer instead of building a
synthetic quoted block. This makes prior messages automatic by default: Mail owns
the reply recipients, subject, and quoted thread body exactly as it does when the
user presses Reply in Mail.app.

The fix also corrects the Drafts lifecycle issue found during live testing:
`manage_drafts(action="list", subject_contains=...)` now checks the bounded
front Drafts window where Mail placed freshly created native reply drafts. No
Drafts lookup or list path uses `every message` or an unbounded folder scan.

## Root Cause

Two separate behaviors combined into the visible failure:

1. `reply_to_email` had been rewritten to create a hidden `outgoing message` and
   manually prepend a plain-text quoted original. That avoided GUI races, but it
   did not match native Mail reply behavior.
2. `manage_drafts` assumed Mail returned Drafts oldest-first and scanned the tail
   of the mailbox. Live Mail showed newly created native reply drafts at positions
   3 and 4 out of 975, so the bounded tail scan missed them.

## Implemented Behavior

- `reply_to_email` calls `reply foundMessage with opening window` by default.
- `reply_to_all=True` uses Mail's native `reply to all` option.
- The requested `reply_body` is inserted above Mail's native quoted thread.
- `mode="draft"` saves the reply and closes the compose window with saving
  enabled.
- The reply subject is captured before the draft window is saved/closed, avoiding
  the invalid-object error seen in live testing.
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

## Remaining Caveat

Native reply drafting opens a Mail compose window briefly, so the open-compose
window cap now applies to `reply_to_email` in draft/send/open modes. If Mail
already has too many compose windows open, the tool returns the existing
`TOO_MANY_OPEN_DRAFTS` structured error instead of creating another native reply.
