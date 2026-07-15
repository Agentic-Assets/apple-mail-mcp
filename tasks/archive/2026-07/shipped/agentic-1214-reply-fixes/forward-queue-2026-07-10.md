# Forward queue: agentic-1214-reply-fixes (2026-07-10)

Deferred follow-ups from the v3.11.1 ship. None block the release; every item
below fails closed today (structured error, no silent bad draft, no send).

1. **Native typing fidelity for accented/composed characters.** Live check 4:
   "Renée" typed as "Renae" (smart quotes, em dash, ellipsis typed correctly).
   Suspects: System Events keystroke Unicode handling vs Mail autocorrect
   ("Capitalize words" was not read; substitutions state unrecorded).
   Instrument with a controlled probe (type into a scratch compose window,
   read back per character), then either fix the typing layer or add a
   pre-flight typeability check. Today the verifier returns
   `REPLY_BODY_MISMATCH` and the skills tell agents to prefer ASCII spellings.
2. **Retype-once rarely engages on Exchange.** The id-equality safety gate
   (delete only what Mail provably created) plus flaky post-save
   `id of replyMessage` capture means the delete-and-retype path usually
   defers to the manual `REPLY_BODY_MISMATCH` flow. Improve post-save id
   capture (bounded retry around the id read before window close, or resolve
   through the verifier's exact-id path) so the net fires when it can.
   The live agent's static analysis of the silent `try` around the id read is
   in `reports/phase7-live-verification-2026-07-10.md` (check 4, secondary
   finding).
3. **Focus-steal race hardening.** Live check 5: a Finder activation ~6s into
   a 2500-char typing pass produced a corrupted 2572-char draft caught by
   `REPLY_BODY_MISMATCH`, not the clean `REPLY_BODY_TYPING_INTERRUPTED`
   abort. One uninstrumented run; root cause unconfirmed. Instrument the
   per-chunk guard (log which chunk saw what front window) and consider a
   post-typing pre-save readback so corruption aborts before save.
4. **`verify_draft` `body_preview` 5000-char cap** (pre-existing) makes tail
   needles false-fail on very long replies; documented in CHANGELOG and
   skills. Consider raising the cap or adding a tail window.
5. **Localized Drafts mailbox resolution** in `_delete_reply_artifact` and
   `_verify_saved_reply_draft` (`mailbox "Drafts"` literal): adopt a resolver
   like `core/reply_state.py`'s ("Brouillons", "Entwürfe", "Borradores").
   Today non-English accounts degrade to `stale_artifact_id` warnings and
   NOT_FOUND verification, never destructive mistakes.
6. **Verifier 20-attempt loop early exit.** After the O(sentences) fold fix
   the loop fits its budget, but a deterministic `body_missing` result could
   exit after 2-3 stable attempts instead of 20 to cut mismatch latency
   (live check 5 spent 68.68s, mostly in this loop).
7. **AGENTIC-1192 item 3 (Archive-reply lookup gap)** stays with the
   id-first-search-retirement lane per the triage report; `reply_to_email`
   lookup is Inbox-only today.

## Update 2026-07-11: v3.11.2 review remediation

1. **Bounded persisted-identity availability** (robustness, confidence:
   verified limitation). The safe native resolver requires the complete Drafts
   mailbox to fit the 75-message cap. The live Exchange account had 233
   Drafts, so it correctly withheld the identity capsule and automatic retry.
   Evaluate a separately reviewed bounded-recent-slice protocol or a higher
   dedicated snapshot cap only if it can retain the same uniqueness proof and
   does not reintroduce destructive fallback.
2. **Identity-resolution observability** (hardening, confidence: verified
   gap). Add a privacy-safe status reason for cap, count drift, no new draft,
   ambiguous candidate, missing RFC ID, and header mismatch. Operator-facing
   output should reveal why automatic cleanup was unavailable without exposing
   message content or raw header values.
