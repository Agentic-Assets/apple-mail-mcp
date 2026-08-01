# Forward queue from the v3.11.3 branch review (2026-07-11)

Deferred items from [`review-synthesis-2026-07-11.md`](review-synthesis-2026-07-11.md). None block the branch; each needs its own lane or a maintainer decision.

## Product decisions (Needs Cayman)

1. **Generalize the identity-guarded delete beyond the smoke path.** `manage_drafts(action="delete")` still does a bare exact-id lookup and delete while its own docstring documents Exchange Drafts id reassignment; the recommended re-list before delete is a TOCTOU race. The mechanism now exists (`delete_draft_if_identity_matches_script`). Options: optional `expected_subject`/`expected_to`/`expected_body_contains` identity params on `manage_drafts` delete, or a public identity-guarded delete tool. Tool-surface change, so it needs sign-off.
2. **Exact-match mode for `verify_draft`.** `checks.to_matches_expected` is subset-only, so extra unexpected recipients pass silently; the smoke CLI compensates with its own exact-set gate. An explicit `to_matches_exact` check (plus a `to_unexpected` warning) would let any caller ask for binding recipient identity. JSON-contract change.
3. **Retention vs safe-attempt cleanup for unverified smoke candidates.** `--cleanup` deliberately retains a created-but-unverified draft (tests lock this in). Routing the single candidate through the identity-guarded delete would prevent orphan accumulation with no deletion risk (it refuses atomically on mismatch). Reverses a decision made in this branch, so maintainer call.

## Hardening backlog (small, low risk)

4. **Localized Drafts mailbox resolution in cleanup.** `delete_draft_if_identity_matches_script` hardcodes `mailbox "Drafts"`, consistent with `verify_draft` and `manage_drafts` today; a shared localized/Exchange-aware resolver (like `drafts_mailbox_block()` in core/reply_state.py) would cover renamed Drafts mailboxes for all three.
5. **Timeout headroom convention.** The same value feeds AppleScript `with timeout` and the Python subprocess timeout in cleanup (and verify_tools/status/trash), so the in-script clean error path is unreachable; search/script.py subtracts 10s. Decide one convention and apply it.
6. **Shared pipe-sentinel parser.** `PREFIX|||payload` parsing is hand-rolled in cleanup.py, verify_tools.py, and core/reply_state.py; a small shared helper would collapse three copies.
7. **Recipient AppleScript loop reuse.** cleanup.py hand-writes the to-recipients collection loop that `recipient_addresses_block()` (applescript_snippets.py) already builds for verify_tools and drafts_scripts; adapting it needs a list-shaped output variant.
8. **Envelope count helper for CLI.** `_mailbox_count` keeps a dead bare-list branch (the JSON path always returns the capped dict envelope); several tools share the `{total, returned, truncated}` shape, so a shared `_envelope_field` helper in cli/formatting.py would prevent the next isinstance dance.

## Docs (optional)

9. **`email-drafting/SKILL.md` length.** Roughly 2,900 words against the ~1,500-2,000 convention; the reply-threading note duplicates reference material and could spill into `references/` (pre-existing, flagged by skill-reviewer).
