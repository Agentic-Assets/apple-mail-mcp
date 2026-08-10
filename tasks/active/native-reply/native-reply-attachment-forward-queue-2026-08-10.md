# Forward queue after native reply attachment fix (2026-08-10)

Candidate work surfaced during the session. This is a menu, not a roadmap.

## Evaluation

- **Create a disposable Mail fixture account and message** (priority: high; confidence: verified gap)
  Add a non-founder fixture account with a reusable inbound message so native reply, reply-all, signature, attachment, and guarded cleanup can be tested without touching real correspondence.
- **Run the native attachment acceptance matrix** (priority: high; confidence: verified gap)
  On the fixture, test reply and reply-all with one and two attachments. Require body above quote, exact attachment names/count, signature/logo preservation, exact `In-Reply-To`, and identity-guarded cleanup.

## Robustness

- **Replace the English-only quote sentinel** (priority: medium; confidence: verified limitation)
  Derive a locale-independent quote proof from the source message or rendered reply without allowing authored `wrote:` text to satisfy the invariant.
- **Design verified direct send as a transaction** (priority: medium; confidence: verified gap)
  If direct attachment sends must be supported, implement save, persisted-identity verification, exact-draft send, and post-send confirmation rather than bypassing the draft verifier.

## Simplification

- **Give verifier statuses a typed internal enum** (priority: low; confidence: passing improvement)
  Consolidate the AppleScript status strings and Python parsing branches after the new quote and attachment failure states have live fixture evidence.
