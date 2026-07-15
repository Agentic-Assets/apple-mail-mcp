# Orchestrator adversarial review (Fable, main thread), 2026-07-10

Scope: full `git diff` of `fix/agentic-1214-reply-body-truncation` against `origin/main` after merging main (v3.11.0) into the branch. Hand-traced the generated AppleScript for the chunked typing handler, the rewritten saved-draft verifier, the abort dispatch, the retry loop, and the manage_drafts contract. This pass ran because the workflow returned early at its gates stage, so its own Fable diff-review and live stages never executed.

## What holds up

- `typing_scripts.py`: chunk loop has guaranteed progress (boundary scan floor at `chunkStart + 1`), conservative double focus guard (Mail front window AND System Events process focus, abort on any uncertainty), modifier clears before the loop and around every chunk, newline-then-space-then-hard boundary selection.
- Abort plumbing: `TYPING_INTERRUPTED` sentinel routes to `REPLY_BODY_TYPING_INTERRUPTED` with an artifact probe; pre-typing aborts keep their existing codes; every abort path closes the compose window `saving no` and removes the temp file.
- Verifier dispatch honors design amendment 3: only `body_missing` maps to `REPLY_BODY_MISMATCH`; `body_after_quote`, `not_found`, `verification_timeout`, `applescript_error` keep pre-existing codes.
- `replyBodyAboveQuoteStatus` locates the body first under `considering case`, so a body containing "wrote:" cannot false-fail (amendment 4). `flattenForCompare` resets text item delimiters correctly (amendment 1 landed; the whitespace strip is real).
- `manage_drafts(action="create")` refuses `in_reply_to` with `CREATE_CANNOT_THREAD` before any AppleScript; guard names the calling tool; draft-id drift documented.
- Retry loop is bounded (one retype), rewrites the same temp path, routes second-run aborts through the same dispatcher (amendment 10).

## Confirmed defects (dispatched to the Sonnet fix squad)

**F1, blocking. Timeout projection ignores per-chunk overhead.** `_native_reply_effective_timeout` projects `chunk_count * TYPING_INTER_CHUNK_DELAY` only. Each chunk also pays a System Events focus round trip (~0.3-0.5s) plus the keystroke itself. A ~10k-char body projects 44s, floors to 120s, and can exceed it live, letting `AppleScriptTimeout` kill osascript mid-typing and strand a partially typed compose window that a retry could then type into on top of (the exact hazard amendment 8 targeted). Fix: model per-chunk overhead with a new `TYPING_PER_CHUNK_OVERHEAD_SECONDS` constant in the projection.

**F2, blocking. `foldSentenceStarts` is O(n^2) over the whole draft.** Per-character `repeat` with a handler call and string concatenation per char, run by `flattenForCompare` over the FULL draft content (which includes the quoted thread history, tens of KB on real Exchange threads), inside a verifier that loops up to 20 eventual-consistency attempts under a 60s default timeout. Long-thread replies would false-fail as `verification_timeout`, and real truncation mismatches would surface as timeouts instead of `body_missing`, disabling the delete-and-retype contract. Fix: delimiter-split rewrite, O(sentences), identical fold semantics.

**F3, blocking, safety. Retry delete can destroy a user's own draft.** The verifier's subject-scan fallback records the FIRST mismatching same-subject draft id (first-wins across all 20 attempts). Under Exchange materialization lag, that id can belong to a pre-existing draft the user wrote on the same thread; the old code only reported that id, but the new retry DELETES it before retyping. Fix: gate deletion on `artifact_id == draft_id` (the id Mail itself returned from this compose call); when they differ or no compose id exists, skip the retry and return `REPLY_BODY_MISMATCH` naming the suspect id without deleting anything.

**F4, blocking. Tab neutralization (design amendment 7) was dropped.** A literal tab in `reply_body` typed via keystroke is a field-navigation key and can move focus out of the body field mid-draft. Fix: convert tabs to spaces on the native path before the body temp file is written; the verifier flattens tabs and spaces on both sides, so the compare is unaffected.

**F5, scope decision B2: INCLUDE.** Public `verify_draft`/`verify_drafts` still match `expected_body_contains` against the full body including the quoted original (AGENTIC-1192 item 2), a false-pass the new `REPLY_BODY_MISMATCH` remediation text points agents at. Fix: scope the needle to the above-quote region, add an only-in-quote signal field, keep the response schema additive.

## Advisory (forward queue, not fixed on this branch)

- `_delete_reply_artifact` and the saved-draft verifier address `mailbox "Drafts" of targetAccount` unlocalized; non-English accounts degrade gracefully (unconfirmed delete surfaces `stale_artifact_id`; verification falls to NOT_FOUND) but should adopt a localized Drafts resolver like `core/reply_state.py`'s in a follow-up.
- The verifier's 20-attempt eventual-consistency loop re-flattens on every attempt for mismatch cases; acceptable after F2, but a cheaper early-exit heuristic could cut worst-case latency.
- `body_verified` in the JSON payload is the constant string `"full_above_quote"` on success; harmless, but a future version could carry the verification mode enum explicitly.

## Fix squad

Three Sonnet agents on disjoint files: (1) F1+F3+F4 in `reply_runner.py`/`reply.py`/`constants.py` + tests; (2) F2 in `saved_draft_checks.py` + tests; (3) F5 in `draft_verification.py`/`verify_tools.py` + tests. CHANGELOG bullets collected by the orchestrator at version-bump time.
