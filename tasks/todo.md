# Active Pointer — apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; CI enforces).

**Current branch:** `fix/agentic-1214-reply-body-truncation` (off `main` @ v3.10.1).

**Current workstream:** [`tasks/active/agentic-1214-reply-fixes/`](active/agentic-1214-reply-fixes/). AGENTIC-1214 field report: native `reply_to_email` drafts truncated long bodies (~320-480 chars) and typed some short bodies in ALL CAPS, `manage_drafts(action="create", in_reply_to=...)` silently dropped threading, and Exchange Drafts ids drift across `action="list"` calls with no writes in between. Orchestration record: [`orchestration-2026-07-10.md`](active/agentic-1214-reply-fixes/orchestration-2026-07-10.md); implementation plan: [`plan-undefined.md`](active/agentic-1214-reply-fixes/plan-undefined.md); recon/design reports under [`reports/`](active/agentic-1214-reply-fixes/reports/).

**Implemented on this branch:** chunked, focus-guarded typed insertion for the native reply body (new `typing_scripts.py`, `TYPING_CHUNK_SIZE`/`TYPING_INTER_CHUNK_DELAY` constants) replacing the single-keystroke insert that dropped long-body tails and could leak ALL-CAPS shift state; full-body case-sensitive post-save verification above the quote with one automatic delete-and-retype retry and the new `REPLY_BODY_MISMATCH` error; a `REPLY_BODY_TYPING_INTERRUPTED` abort code for mid-typing focus loss (distinct from pre-typing `REPLY_WINDOW_FOCUS_FAILED` / `REPLY_SUBJECT_GUARD_MISMATCH`); a native-path timeout that scales with projected typing time, with `REPLY_BODY_TYPING_BUDGET_EXCEEDED` refusing over-budget bodies up front; `manage_drafts(action="create", in_reply_to=...)` now refuses up front with `CREATE_CANNOT_THREAD` instead of silently dropping threading; the standalone reply-like guard now names the tool that actually called it; `manage_drafts` docstrings document Exchange draft-id instability. Skills (`email-drafting`, `apple-mail-operator`, canonical `references/`), `docs/CLAUDE-conventions.md`, and `CHANGELOG.md` updated to match.

**Next action:** version bump + `bash tools/gates/dev-check.sh release`, a draft-mode-only live-verification pass, then ship per the orchestration record. No PR (not authorized for this repo); merge needs Cayman's explicit phrase.

**Prior pointer (parked) branch:** `feat/apple-calendar-tools` (Apple Calendar tool surface, target v3.10.0).

**Prior pointer (parked) workstream:** [`tasks/active/apple-calendar-tools/`](active/apple-calendar-tools/). Phase-1 research consolidated in [`research-report-2026-07-10.md`](active/apple-calendar-tools/research-report-2026-07-10.md); adversarially refined implementation plan in [`final-plan-2026-07-10.md`](active/apple-calendar-tools/final-plan-2026-07-10.md) (supersedes `plan-2026-07-10.md`); implementation report in [`reports/phase4-implementation-2026-07-10.md`](active/apple-calendar-tools/reports/phase4-implementation-2026-07-10.md).

**Implemented (v3.10.0):** 10 Apple Calendar tools (41 total), `calendar_core/` hybrid engine (AppleScript guaranteed + optional EventKit read fast path), new calendar mode gating, 2 workflow skills (`calendar-operator`, `meeting-scheduler`, 11 total), CLI `calendars` / `calendar-events` / `calendar-grant`, full mocked test suite under `tests/calendar_surface/`.

**Prior pointer next action:** owner-present live verification per final plan section 8 (answer the Calendar Automation prompt first; the EventKit Events grant is already resolved on this host), then PR review. Post-implementation review complete: code-review findings F1-F8 fixed ([`reports/phase7-review-fixes-2026-07-10.md`](active/apple-calendar-tools/reports/phase7-review-fixes-2026-07-10.md)), plugin-validator and skill-reviewer findings applied (Auburn timezone, example accuracy, error-code wording). Lane files renamed to dated names.

**Prior pointer (parked):** `codex/agentic-assets-marketplace-install` / [`tasks/active/manifest-release-hardening/`](active/manifest-release-hardening/) (2026-07-07 version-surface hardening; PR pending after Cayman approval).

**Previous branch:** `chore/module-line-budget-splits` (v3.9.1 pushed; commit `3d2c515`).

**Shipped (v3.9.1):** Module line-budget splits. Flat `cli.py`, `core.py`, and six tool modules became packages with facade `__init__.py` re-exports; tests reorganized into `tests/<area>/` subfolders; recursive `@mcp.tool` count gate (31 tools preserved); `tools/manifest_checks/` package behind `validate_manifests.py`; 1021 tests (`tools/expected_test_count.txt` SSOT); version 3.9.1 across all six version files; all three artifacts rebuilt and validated (`bash tools/gates/dev-check.sh release` green).

**Shipped (v3.8.0):** Native-format reply drafts. `reply_to_email` defaults to `native_format=True` (Mail native reply window + keystroke body; colored quote bar + account logo signature). Flatten path preserved as `native_format=False`. See CHANGELOG 3.8.0 and [`tasks/active/native-reply/`](active/native-reply/).

**Handoff (native-reply live TO-TEST):** [`tasks/active/native-reply/native-reply-handoff-2026-06-30.md`](active/native-reply/native-reply-handoff-2026-06-30.md). Findings + probes: [`tasks/active/native-reply/native-reply-probes-2026-06-30.md`](active/native-reply/native-reply-probes-2026-06-30.md).

**Next action (live, needs Cayman):** remaining native-reply TO-TEST items that cannot be mocked. Send a saved native draft to self and confirm the logo survives the actual SEND; live exercise attachments + native reply, `reply_to_all` native on a real multi-recipient thread, and `GUARD_ABORT` under real focus contention. See the handoff TO-TEST section.

**Deferred follow-up (brand-voice, not a blocker):** `plugin-validator` flagged pre-existing em dashes in ~10 shipped descriptions (top-level + 8 tool descriptions in `apple-mail-mcpb/manifest.json`, plus `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` descriptions). Sweep in a separate brand-voice pass, then rebuild artifacts.

**Caveats (carried, not blockers):**
- Native path needs Mail window focus + Accessibility permission (`native_format=False` avoids it; returns `REPLY_WINDOW_FOCUS_FAILED` when focus cannot be acquired).
- Logo not repainted in the reopened draft editor = native Mail behavior (not our bug); confirm it survives an actual SEND.

**Prior workstream** (cleanup-docs-and-simplify, 2026-06-08) superseded; notes in `tasks/archive/2026-06/shipped/cleanup-docs-and-simplify-2026-06-08/`.
