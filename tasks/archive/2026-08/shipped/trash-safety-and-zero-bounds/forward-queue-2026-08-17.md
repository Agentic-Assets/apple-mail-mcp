# Forward queue after trash-safety-and-zero-bounds (2026-08-17)

A menu, not a roadmap. Confidence is stated per item; verify before acting.

## Blocking a "proven" claim

- **No destructive path was live-verified** (confidence: certain, by design). `manage_trash`, `move_email`, and `cleanup_empty` fixes are source-and-test-verified only. Nothing was deleted or moved to confirm any of them. A disposable-fixture account with a seeded Trash of known age spread is the gate. Until then "fixed" means "the emitted script no longer contains the wrong bound," not "the tool behaves correctly."
- **Live Claude Desktop render of the dashboard** (AGENTIC-2368). `node --check` plus a DOM stub proves the parse and the render logic, not the real MCP-UI host.
- **Four sites may hang rather than throw** (confidence: documented hypothesis). `search/script.py:471-476` records that some Mail reads hang instead of erroring. Where that happens, **no `on error` arm of any shape helps** — only the call timeout bounds it. Affects `awaiting_reply.py:223-232`, `export_helpers.py:426-448`, `manage/attachments.py:279-309`, and `compose/manage.py`'s draft body read. The `cleanup_empty` fail-closed fix covers the **throw** path only. Probe these four live before designing their fixes.

## Correctness, filed

- **AGENTIC-2360 (Urgent, Needs Cayman):** `manage_drafts` `send` and `delete` ignore `dry_run`, which defaults to `True`. `send` collides with the never-auto-send rule. Documented behavior, so it needs a decision, not a unilateral fix. `delete` is guarded by no mode flag.
- **AGENTIC-2361 (High):** `create_rich_email_draft(output_path=…)` overwrites arbitrary files outside the home dir — `SENSITIVE_DIRS` entries are home-relative and cannot match. `export_emails(scope="single_email")` truncates a pre-existing file named after the subject; every other scope writes into an indexed subdirectory.
- **AGENTIC-2363 (Urgent):** ~50 bare-`try` silent-zero sites, all 49 AppleScript-bearing files examined. Worst is `compose/reply_scripts.py:472-474` — a swallowed identity tweak means `from_address` is ignored and the reply goes out from the **wrong identity**, on the default path, with no `sender_override_applied` field. Start with the zero-cost wiring: `_parse_search_records` already returns `mailbox_errors` and 3 of its 4 consumers discard it.
- **AGENTIC-2362 (Medium):** seven accepted-but-unapplied parameters. Includes the `dry_run` default asymmetry (`manage_trash` previews, `move_email` acts) whose docstring never states the default.
- **AGENTIC-2369 (Medium):** `timeout` unvalidated in every tool (`timeout=-5` reaches `with timeout of -5 seconds`); `_search_message_ids` appends before its limit check.
- **`save_email_attachment` fails open on probe failure** (confidence: verified by two independent audits). `manage/attachments.py:200-204` sets sentinels that disable the size cap **and** the ambiguous-filename guard together. The size fail-open has a comment calling it deliberate; the ambiguity one is documented nowhere. **Deserves its own issue** — it is the only case found where a guard silently disables itself.

## Gates that would stop the recurrence

These matter more than any single fix above, because every defect in this batch was individually cheap and collectively recurring.

- **A bare-`try` lint.** `tests/core/test_no_unbounded_whose.py` already lints forbidden AppleScript idioms with baseline grandfathering. The infrastructure exists; without the rule the ~50 sites regress one at a time. (confidence: verified the mechanism exists)
- **A bound-provenance lint.** No test asserts an emitted slice bound is `SCAN_BOUNDS`-derived or floored at 1. Both the unclamped-large-N and the zero-bound defect classes are invisible to the current ratchet. (verified)
- **A JS-parse gate for `plugin/ui/`.** `node --check` would have caught a dead dashboard that shipped. Tests on this branch already do it; promoting them to a gate step is what remains. (verified)
- **`plugin/ui/` into `ruff`/`mypy` scope.** Confirmed zero `BuildSource` entries under the gate's mypy invocation; it is the only unchecked Python in the plugin. (verified)
- **A parameter-reaches-the-script test per mutating tool.** Turns AGENTIC-2362's periodic audit into a gate. (passing idea)
- **The lint cannot see `messages <var> thru <var>`.** `export_helpers.py:163-175` uses that spelling and is invisible to both the regex and a `messages 1 thru` grep. The lint also scans `TOOLS_DIR` only, so `bounded_scan.py` and `core/` are unscanned. (verified)

## Blocked on PR #90

- **The commit-msg identity gate.** Full executable spec is on AGENTIC-2358. The load-bearing finding: reusing the identity rules verbatim flags 98 of the last 300 commits, because the mandated `Co-Authored-By:` trailer carries a real address and the ratchet is keyed by file path — a commit message has no path. Strip recognized trailers first. Also: verbose commits embed the whole diff below a scissors line; `$1` is relative and must be resolved before the `local-env-vars` unset loop; `revert` and `cherry-pick` do not fire the hook at all.
- PR bodies and issue comments **cannot** be gated locally. Server-side only, and detection is inherently post-publication, so the honest ceiling is fast redaction plus an alert.

## Headroom

- **`trash.py` is at 553 lines against a hard 600 ceiling** (confidence: measured). It grew 455 → 553 across this branch. The next feature touching it hits the wall, and the budget baseline tracks zero modules so >600 is a hard pytest failure, not a warning. The obvious seam is the one `compose/manage.py` used successfully here: move the script builders into a sibling module in a `manage/` package, leaving the tool function and its guards. `compose/manage.py` went 596 → 522 that way with byte-identical emission.
- **Two clamp disclosures are asymmetric** (verified). `empty_trash` emits `(max_deletes=N requested, clamped to M; valid range 1-…)`; the id and filter paths emit nothing, because their clamp is provably inert (ids hard-capped at 50, so the guard never fires and no message is excluded). `move_email` *does* emit one in the equivalent inert position. Consistency versus noise is a judgment call left open.
- **`empty_trash(max_deletes=0, confirm_empty=False)` now returns the bound refusal rather than the `confirm_empty` error** (verified), because the bound guard sits ahead of the branch. Both refuse and nothing is deleted either way, so there is no safety impact, but the message changed and the ordering was deliberately left unasserted in case it should be the other way.

## Simplification

- **`has_filter` carries a redundant term** post-normalization in `trash.py` (~line 412), deliberately mirroring `move.py`'s equally redundant check. Kept as a second independent barrier. Flag for whoever runs `code-simplifier`. (verified)
- **`trash.py`'s `if subject_terms or sender:` branch is dead for external callers** — the deprecated-selector gate returns first, so the sibling check reduces to `if not apply_to_all`. Kept in case the gate is relaxed. (verified)
- **`compose/lookup_scripts.py:134-139` and `compose/saved_draft_checks.py:25-48` may be dead** (facade exports and tests only). Confirm before fixing anything in them. (unconfirmed)

## Process

- **Do not tell a subagent to "revert your edits" in a shared checkout.** One agent was told exactly that, ran `git status` first, found four modified files belonging to a sibling lane, and refused. Following the instruction literally would have destroyed in-flight work. Scope any revert instruction to named paths.
- **Three of my briefs carried a wrong premise that the subagent caught and corrected**: `search_emails(limit=-1)` was already guarded (the real defect was a non-terminating `has_more` loop); `move_email` defaults to `dry_run=False` not `True` (relabelling its 45 doc examples as previews would have documented a mutation as a no-op); and `older_than_days=0` was **not** already safe in `trash.py`, which gates on `apply_to_all` rather than a falsy value. Briefs should state premises as claims to verify, not as facts.
