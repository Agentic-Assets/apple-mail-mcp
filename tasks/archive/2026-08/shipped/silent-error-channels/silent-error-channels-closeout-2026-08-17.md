# Silent error channels + file clobber closeout (2026-08-17)

**Branch**: `fix/silent-error-channels-and-file-clobber`
**Base**: `origin/main` @ `aa7fe0a`
**Commit**: `833ce61` (1 commit, 43 files, +5154/−256), pushed and matching `origin`
**PR**: https://github.com/Agentic-Assets/apple-mail-mcp/pull/92 (open, non-draft)
**Verification**: `bash tools/gates/dev-check.sh release` exit 0 and `bash tools/gates/source-release-gate.sh` exit 0 (stamped tree `bb4ae8c`), both on `833ce61`. GitHub Actions not consulted. **No live Mail verification.**
**State**: PR open, awaiting review and Cayman's literal merge approval.

## Goal

Close the AGENTIC-2363 family plus AGENTIC-2361, AGENTIC-2369, and the destructive half of AGENTIC-2374 — all instances of one defect class — and add a gate so the next instance cannot land silently.

## The defect class

`run_applescript` raises on a nonzero `osascript` exit. A script with **no** `try` therefore fails loudly and is correct. A `try` with **no `on error` arm** is precisely what converts a loud failure into an exit-0 wrong answer. Compounding it, `core/script_fragments.py` deliberately raises `error "No inbox mailbox found…"` and `"Mailbox name is ambiguous…"`; those intentional loud errors are swallowed by any enclosing script-level bare `try`.

## What shipped

Seven parallel lanes on one shared checkout, then a simplifier pass, then one regression fix.

| Area | Headline |
|------|----------|
| `compose/reply_scripts.py`, `reply_runner.py`, `reply_window_identity_scripts.py` (new) | A native reply with `from_address` could be **saved from an identity the caller never asked for**. Now aborts before `save`, closes the window with `saving no`, returns `REPLY_SENDER_OVERRIDE_FAILED`. |
| `compose/forward.py` | `forward_email` could **send** with the quoted original silently missing. |
| `inbox/list_scripts.py`, `parsing.py`, `list_emails.py`, `__init__.py` | JSON mode returned a payload byte-identical to a genuinely empty inbox on scan failure. Text mode had been correct all along. |
| `analytics/export_helpers.py`, `export_failure_reporting.py` (new) | All four export builders counted **before** the write. The paged `correspondent` scope also consumed an offset slot on failure, so the message was **never** exported. |
| `analytics/export.py`, `core/validation.py`, `compose/rich_draft.py` | `create_rich_email_draft`'s inlined denylist could not match any absolute path; `single_email` export truncated a bare subject-named file. |
| `calendar/helpers.py`, `search/thread.py`, `by_id.py`, `records.py` | Failed calendar enumeration rendered as "nothing on your calendar" across five read tools; thread JSON returned an empty thread on script error. |
| `manage/status.py`, `manage/helpers.py` | `TOTAL UPDATED` counted messages Mail could **describe**, not messages **changed**. Separately, `limit=0` resolved one id, so `manage_trash(max_deletes=0)` deleted a message. |
| `core/applescript.py`, `server.py` | `timeout` was never range-checked; `timeout=0` arrived as `AppleScriptTimeout`. Uncaught `ToolError` then escaped mail tools unserialized. |
| `tests/core/test_no_bare_applescript_try.py` (new, 1092 LOC) | Package-wide ratchet: 236 sites across 50 modules (178 bare, 58 silent). |

## Verification

Green on `833ce61`: 1969 tests, `ruff check` / `ruff format --check` / `mypy --strict` on `plugin/apple_mail_mcp/`, module line budget, manifests (v3.11.7, 41 tools), byte parity between `apple-mail-plugin.zip` and `apple-mail.plugin` (identical SHA-256), all three artifacts rebuilt, tasks layout, repo-root hygiene.

Public-repo leak scan run over the full branch diff and every untracked file: clean.

**Not verified.** Nothing was exercised against real mail. Every claim is source-level, mocked, or `osacompile`-parse-checked. Offline AppleScript probes were used for control-flow semantics only, each grep-confirmed to contain zero `tell application` references. The destructive paths (`manage_trash`, `update_email_status`) were deliberately never executed. For most fixes the strongest honest claim is "the emitted script now reports the failure", not "a failure was observed being reported".

## Decisions made

**Refuse, do not clamp, on bad `timeout`.** Clamping `-5` to the default swaps one misdirection ("Mail was slow") for another (a deadline the caller never asked for). `0` gets no special case: it was measured to expire immediately, and `timeout=None` already owns "use the default". Ceiling `3600` chosen for two measured reasons, not caution — `subprocess` raises a bare unwrapped `OverflowError` above 2,147,483 s, and `_LOCK_WAIT_TIMEOUT = 300` bounds how long a caller *waits* for the Mail lock but nothing bounded how long one *holds* it.

**Tri-state flags, never emptiness tests.** The forward fix initially looked like a one-line guard on `origContent`, but testing `origContent is ""` would reject every legitimately empty original (subject-only mail, meeting invitations), turning "no body" into "error". Every read now records whether it *succeeded* in its own flag. This was nearly shipped wrong and is the single most reusable lesson here.

**Scoped subdirectory over refuse-to-clobber for `single_email` export.** A refusal guard would live in AppleScript, where a stray `try` silently flips it back to clobbering — the exact failure mode this branch exists to remove. The subdirectory also fixes the second half (two messages sharing a subject still collided).

**Fix the shared helper, not the tool boundary, for `limit=0`.** `manage/trash.py` and `move.py` are owned by unmerged PR #91. Fixing `_search_message_ids` covers all four call sites without touching either file. Boundary refusals for those two are deferred to AGENTIC-2374.

**Do not parametrize the export failure-arm builder for reuse.** It hard-codes export-specific counters, halt-vs-continue paging semantics, and wording. Generalizing it would couple analytics to search and manage internals — more configuration than duplication.

**Do not "fix" the row parser's `if len(parts) < 8: continue`.** Reclassifying short lines as errors would fire on any content preview beginning `"Error: "`, inventing spurious errors on the quiet path. A shared recognizer is used at the one call site that needs it.

**`INVALID_TIMEOUT` envelope conversion excludes three tools.** `list_accounts`, `list_account_addresses`, and `get_mailbox_unread_counts` declare container return annotations; FastMCP validates returns against a structured-output schema derived from them, so a JSON string produced a pydantic error with the real code buried in `input_value` — strictly worse than raising. They raise with an accurate message; a test pins the set.

## Claims refuted during the work

Worth recording so they are not re-derived. Three briefs carried premises that investigation overturned:

- AGENTIC-2363 item 6 located the discard in `manage/helpers.py:139-161`. That function receives a plain `list[dict]` — there is nothing to discard. The real site is `search/dispatch.py:255`, owned by PR #90.
- AGENTIC-2363 item 7's `smart_inbox` half claimed a *discarded* error value. The hardcoded `errors: []` sits on a success path reached only after every existing channel returns early. The gap is a *missing* channel — refiled as AGENTIC-2372.
- `by_id.py`'s singular `_fetch_email_record_by_id` discard is provably dead: its script cannot emit an `ERROR_MAILBOX|||` row.

## Things deliberately deferred

Filed as AGENTIC-2370 through 2377. Highest impact: AGENTIC-2372 (`get_awaiting_reply` lists threads already answered), AGENTIC-2373 (thread export prints `Exported: 0` on localized/Exchange accounts), AGENTIC-2376 (mutation tools discard partial-work output on error).

`plugin/skills/email-drafting/` should document `REPLY_SENDER_OVERRIDE_FAILED`; that directory is PR #91-owned and was left untouched.

## Left to the operator

- **Merge approval** for PR #92 (and #90, #91). Never auto-merge.
- **AGENTIC-2375**: whether file-writing tools should refuse paths outside the home directory. This is a policy question affecting all three tools, deliberately not decided by an agent.
- **Live verification**: the disposable-fixture matrix in `tasks/active/native-reply/` remains the only path to a live claim.
- **Whether to cut a `v3.11.7` tag.** `pyproject.toml` is at 3.11.7 with no tag, so the version is still open and was not bumped.

## Expected breakage after #90/#91 merge

The bare-`try` ratchet's staleness test fails when a count comes in *under* baseline. Both PRs remove sites in files the baseline covers, so it will fail until entries are lowered — the ratchet working as designed. Regenerate with `python3 tests/core/test_no_bare_applescript_try.py --write-baseline`; every number must go **down**. Affected entries are listed in the PR #92 body.
