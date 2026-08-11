# Forward queue after native reply attachment fix (2026-08-10)

Candidate work surfaced during the session. This is a menu, not a roadmap.

## Evaluation

- **Create a disposable Mail fixture account and message** (priority: high; confidence: verified gap)
  Add a non-founder fixture account with a reusable inbound message so native reply, reply-all, signature, attachment, and guarded cleanup can be tested without touching real correspondence.
- **Run the native attachment acceptance matrix** (priority: high; confidence: verified gap)
  On the fixture, test reply and reply-all with one and two attachments. Require body above quote, exact attachment names/count, signature/logo preservation, source-linked `In-Reply-To` when RFC identity is available, and identity-guarded cleanup only for revalidated RFC-backed drafts.

## Robustness

- **Exercise source-attributed quote verification across Mail locales** (priority: medium; confidence: verified limitation)
  Confirm that the source sender attribution remains present in rendered native quotes across supported locales without allowing authored or signature text to satisfy the invariant. For iCloud transaction-only identity, record the same-operation result but do not perform guarded cleanup; cleanup testing requires RFC-backed identity revalidated at the current Drafts row.
- **Keep attachment sends draft-first** (priority: medium; confidence: deliberate contract)
  Attachment-bearing compose, reply, and forward calls now refuse direct send. Reconsider only with a full save, persisted-identity verification, exact-draft send, and post-send confirmation transaction.

## Simplification

- **Give verifier statuses a typed internal enum** (priority: low; confidence: passing improvement)
  Consolidate the AppleScript status strings and Python parsing branches after the new quote and attachment failure states have live fixture evidence.
