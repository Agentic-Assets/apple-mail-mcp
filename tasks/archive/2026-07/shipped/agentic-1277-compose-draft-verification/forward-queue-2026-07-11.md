# Forward queue after AGENTIC-1277 and AGENTIC-1191, 2026-07-11

Candidate follow-up work, not a committed roadmap. Re-verify each item before
acting.

## Correctness

- **Archive exact-ID replies** (AGENTIC-1192, verified gap, high priority)
  `reply_to_email` still resolves source messages from Inbox only. Implement
  the discrete Archive lookup using the id-first lane's approved boundaries.

- **Native reply-all proof** (AGENTIC-781, verified field report, high priority)
  Use a safe human-operated draft test to establish whether Mail's native
  reply-all path preserves all direct recipients and CC recipients. Do not
  use a business draft as the test artifact.

## Hardening

- **Formalize smoke cleanup identity** (confidence: passing idea, medium)
  The current generated subject, exact To set, and body sentinel are sufficient
  for a bounded smoke. Consider a durable RFC Message-ID capsule only if a
  standalone compose smoke needs cross-session cleanup.

- **Add a live performance history surface** (confidence: verified need,
  medium) Store sanitized quick-perf durations and Drafts scan truncation in a
  durable benchmark record so threshold changes are evidence-based.

## Features

- **Deterministic paginated export** (AGENTIC-995, verified gap, medium)
  Wire or remove the accepted `sort` parameter, then prove pagination is
  deterministic under bounded export constraints.

- **EML export and attachment bundles** (AGENTIC-996, proposal, low)
  Design size limits and one-message live verification before implementation.
