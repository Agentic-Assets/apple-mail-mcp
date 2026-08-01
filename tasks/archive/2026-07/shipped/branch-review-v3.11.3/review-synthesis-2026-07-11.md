# Code review synthesis: `fix/agentic-1277-compose-draft-recipient-verification` vs `main`

**Date:** 2026-07-11
**Review scope:** one commit (`d2edeba`, v3.11.3) touching the compose-draft smoke verification, the new identity-guarded cleanup module, reply-state bounded scans, and the perf mailbox count.
**Method:** /code-review at xhigh effort (recall mode). Ten independent finder subagents (5 correctness angles, 3 cleanup angles, 1 altitude, 1 conventions), then adversarial verification per surviving candidate (in progress at time of writing).

## Baseline (branch as-is, before any review fixes)

| Gate | Result |
|------|--------|
| `pytest tests/` | 1551 passed, 48 subtests passed |
| `ruff check plugin/apple_mail_mcp/` | clean |
| `ruff format --check` | 106 files already formatted |
| `mypy --strict plugin/apple_mail_mcp/` | no issues in 106 files |

## Per-angle results (all 10 finders)

| Angle | Focus | Candidates |
|-------|-------|------------|
| A. Line-by-line diff scan | every hunk + enclosing functions | 1 (cleanup retention design) |
| B. Removed-behavior auditor | deleted guards and invariants | 0 (all seven audited deletions re-established or intentional) |
| C. Cross-file tracer | callers/callees of changed functions | 3 (docs drift, envelope non-uniformity, hardcoded Drafts mailbox) |
| D. Language pitfalls (Python + AppleScript) | classic footguns | 4 (dedup count mismatch, casefold vs lower, timeout headroom, TOTAL fallback) |
| E. Wrapper/proxy correctness | new indirection layers | 1 (account-error JSON contract); circular import and patch targets verified safe |
| F. Reuse | duplicated helpers | 6 (recipient normalization x4, recipient AppleScript loop, sentinel parsing, cap cross-ref) |
| G. Simplification | redundant logic | 3 (second containment loop, redundant verification branches, Optional-total coalescing) |
| H. Efficiency | wasted work | 2 (body re-fetch before delete, second containment loop) |
| I. Altitude | fix depth | 4 (guard scoped to smoke only, subset-vs-exact verify_draft, normalization divergence, envelope sniffing) |
| J. Conventions (CLAUDE.md) | quotable rule violations | 1 (inline AppleScript evades osacompile parse-check) |

Finder B and Finder E also positively cleared several suspected mechanisms: the `_parse_drafts_snapshot_output` 2-to-3-tuple change reached every caller, `DRAFT_LOOKUP` (75) remains alive for `DRAFT_LIST_CAP`, `verify_draft` emits `recipients.to` as a ", "-joined string plus `checks.to_matches_expected` exactly as the new smoke checks expect, the cleanup module's late-bound `compose.run_applescript` lookup avoids the circular-import trap, and the perf `_mailbox_count` change is a real bugfix (the old list branch returned 0 for every account because `list_mailboxes` JSON always returns the capped dict envelope).

## Deduped findings, ranked

### High priority (correctness)

1. **Duplicate expected recipients break cleanup (D1).** `_expected_recipient_literal` (tools/compose/cleanup.py) dedupes the expected To set, but the AppleScript count gate compares against the raw, possibly duplicated actual recipient list. `--to "a@x, A@x"` verifies (set semantics) yet refuses deletion (2 actual vs 1 expected), orphaning a verified draft and reporting cleanup failure. Proposed fix: drop the count gate and keep both containment loops (mutual containment equals set equality and is robust to duplicates on either side). Verification in flight.
2. **Missing `TOTAL|||` line fails unsafe (D4/G3).** `_parse_drafts_snapshot_output` coalesces an absent mailbox-wide total to `scanned`, which makes `truncated` False and lets `resolve_has_draft` return a definitive False nonmatch, the exact false-negative class this branch set out to eliminate. Reachability is narrow (the script always emits TOTAL in both branches), but the fallback direction is wrong: parse failure should read as unknown, not as complete. Proposed fix: carry the true `int | None` through and fail open when total is unknown. Verification in flight, including which unit tests feed COUNT-only fixtures.
3. **Skill docs now describe `has_draft: null` wrongly (C1).** Bundled skill references (pre-draft-verification.md copies, email-drafting SKILL.md) still say null means the scan was skipped or errored and to check `draft_scan.status`. After this branch, null is also the answer for a nonmatch from a truncated (status ok) scan of a 50+ draft mailbox. An agent following the current wording would treat null as a fluke, conclude no draft exists, and draft a duplicate reply. Fix: update every copy plus the `draft_scan` envelope docs for the new `total`/`truncated` keys. Verification in flight to enumerate exact locations.
4. **`draft_scan` envelope non-uniform (C2).** `build_draft_scan_status` now emits `total`/`truncated` at both the envelope and per-account level, but `get_needs_response` builds its own `draft_scan` without them and the empty-snapshots early return also omits them. A consumer keying on `draft_scan.truncated` silently loses truncation detection on the actionable-triage surface. Fix: emit the same keys on every `draft_scan` producer.

### Medium priority (robustness/conventions)

5. **Inline AppleScript evades the osacompile parse gate (J1).** The delete script in `delete_draft_if_identity_matches` is an inline f-string local, so `tests/cross_cutting/test_applescript_builders_compile.py` and the `.claude/hooks/check_applescript_compiles.py` hook (which discover `*_script()` builder functions returning `tell application "Mail"` scripts) never compile it. This is the exact 3.3.0 regression class the suite exists to catch. Fix: extract a module-level `..._script()` builder.
6. **Account-resolution failure breaks the helper's JSON contract (E1).** `delete_draft_if_identity_matches` returns `_resolve_account`'s raw "Error: ..." string on account failure while every other return is `{"deleted": false, "error": ...}` JSON. Masked in the smoke path today (account already validated upstream), but the public return contract is inconsistent. Fix: wrap in the JSON error shape.
7. **Redundant verification branches (G2) and redundant second loop (G1/H2).** In `_draft_verification_passed`, the `checks.to_matches_expected is False` branch and the `to_mismatch` warning entry are subsumed by the raw exact-set comparison (verify_draft's check is subset-only, so the raw comparison is strictly stronger). In the AppleScript, one of {count gate, second loop} is redundant; resolution is coupled to finding 1 (recommended: keep loops, drop count gate).

### Lower priority (reuse/consistency, mostly pre-existing conventions)

8. **Four recipient-CSV normalizations now exist (F1/F2/D2/I3).** `_split_csv_addresses` (draft_verification.py, lower), `_normalized_recipient_set` (draft_smoke.py, casefold), `_expected_recipient_literal` (cleanup.py, lower + dedupe), plus manage.py's inline split. casefold vs lower diverges on non-ASCII (Turkish dotless i, German sharp s), so the verification stage and cleanup stage can disagree on the same draft. Fix: one shared normalization helper (casefold) consumed by all three new/updated sites.
9. **`DRAFT_SNAPSHOT_CAP` (50) vs `DRAFT_LOOKUP` (75) uncross-referenced (F6).** Two different Drafts-scan caps with no comment distinguishing them; a future tuner will likely edit one and miss the other. Fix: one-line comment cross-reference.
10. **Hardcoded `mailbox "Drafts"` (C3/F4).** cleanup.py inlines the literal instead of the localized/Exchange-aware `drafts_mailbox_block()`; consistent with verify_draft and manage_drafts today, so noted rather than fixed here.
11. **Timeout headroom (D3).** The same value feeds both the AppleScript `with timeout` and the Python subprocess timeout, so the Python timer always wins and the clean in-script error path is unreachable. search/script.py subtracts 10s for this reason; verify_tools/status/trash do not. Consistent with siblings, noted.
12. **Sentinel-parse duplication (F5), envelope sniffing (I4).** Fourth hand-rolled `PREFIX|||payload` parser; dead `isinstance(parsed, list)` branch in `_mailbox_count` kept only for legacy-shaped unit tests. Noted for a future consolidation pass, not this branch.

### Design observations (deliberate behavior, forward queue)

13. **Retention of unverified candidates under `--cleanup` (A1).** The branch deliberately retains a created-but-unverified smoke draft (CHANGELOG: "retains the artifact instead of risking deletion of another draft") and tests lock this in. The identity-guarded delete makes attempted cleanup safe even for unverified candidates (it refuses on mismatch atomically), so routing the single candidate through it would prevent orphan accumulation with no deletion risk. Kept as-is because it reverses an explicit product decision made in this same commit; queued for maintainer decision.
14. **Identity guard scoped to the smoke path only (I1).** manage.py's own docstring documents Exchange Drafts id reassignment for every `manage_drafts(action="delete")` caller and recommends a re-list, which is a TOCTOU race, not a fix. This PR built the right mechanism (atomic identity check + delete in one transaction) but wired it only into the CLI harness. Generalizing to an optional identity guard on `manage_drafts` delete (or a public tool) is the deeper fix; that is a tool-surface change needing its own lane.
15. **`verify_draft.checks.to_matches_expected` is subset, not exact (I2).** The CLI had to re-derive exact set equality because the tool check passes with extra unexpected recipients. An explicit exact-match mode in `verify_draft` would let any caller ask for binding identity. Tool-contract change; queued.

### Refuted or cleared

- **Body re-fetch before delete (H1):** proposed dropping the body-sentinel recheck to save one full-body fetch per cleanup. Rejected: the sentinel is a designed part of the atomic identity guard, the path is a test harness, and the cost is one fetch per smoke run.
- **All seven removed-behavior candidates (Finder B):** post-delete confirmation is strictly safer in-transaction; `last_result` had zero consumers; the tuple arity change reached every caller; `manage_drafts` delete production path and tests intact.
- **Circular import / patch-target concerns (Finder E):** late-bound `compose.run_applescript` attribute lookup and in-function imports align with the established test-patching convention.

## Fix plan for this branch

1. cleanup.py: drop the recipient count gate, keep mutual containment loops (fixes finding 1; resolves 7's AppleScript half).
2. reply_state.py: carry true `Optional` total; unknown total fails open (finding 2), with test updates.
3. Skill/reference docs: update every `has_draft: null` explanation and `draft_scan` envelope description (finding 3).
4. reply_state_wiring.py + needs_response.py: uniform `total`/`truncated` keys on all `draft_scan` producers (finding 4).
5. cleanup.py: extract the delete script into a `*_script()` builder so the osacompile gates cover it (finding 5).
6. cleanup.py: JSON-shape the account-resolution error (finding 6).
7. draft_smoke.py: drop the subsumed verification branches (finding 7, Python half).
8. Shared recipient normalization helper, casefold, used by draft_smoke + cleanup (finding 8).
9. constants.py: cross-reference comment for the two Drafts caps (finding 9).
10. Re-run full gates (pytest, ruff, mypy, dev-check release) and the code-simplifier pass before commit.

Items 11 to 15 go to the forward queue, not this branch.

## Verification status

The dedicated verifier wave was cut short by the operator in favor of two inline main-thread checks, which settled the only empirically uncertain items:

- **Finding 1 confirmed directly:** `tools/compose/manage.py:194-198` adds one `to recipient` per comma-split address with no dedup, so duplicated `--to` input really produces duplicate actual recipients while the cleanup literal is deduped; the count gate fires wrongly.
- **Finding 2 blast radius mapped:** ten test files carry drafts-snapshot fixtures with `COUNT|||` and no `TOTAL|||` (core, inbox, search, analytics, smart_inbox; the calendar fixtures are unrelated calendar output). Fixture updates are mechanical.

Findings 3 to 6 rest on direct source reads by at least two independent finders each; all fixes are gated by the full suite plus release gates. Fixes are being applied by three parallel agents (cleanup module; reply-state truncation and envelope; smoke CLI and skill docs) with the shared normalization helper and constants cross-reference applied on the main thread first. This report will be superseded by the branch closeout once fixes land and gates re-run.
