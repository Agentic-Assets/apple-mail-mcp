# Phase 4: Apple Calendar tools implementation report (v3.10.0)

Branch `feat/apple-calendar-tools`, implemented 2026-07-10 against
[`../final-plan-2026-07-10.md`](../final-plan-2026-07-10.md) (the adversarially refined
plan; it records which phase-3 findings were accepted and rejected). Working tree left
uncommitted per the task's git rules (no commit, no push, no branch switch).

## Headline numbers

| Metric | Before | After |
|--------|--------|-------|
| MCP tools | 31 | **41** (10 new; verified by `validate_manifests` and the registry test) |
| Workflow skills | 9 | **11** (`calendar-operator`, `meeting-scheduler`) |
| Collected tests | 1053 | **1365** (+312, all in `tests/calendar_surface/`; `tools/expected_test_count.txt` updated) |
| Version | 3.9.3 | **3.10.0** on all six surfaces |
| Module line budget | clean | clean (largest new module: `calendar_core/engine.py`, 366 lines) |

## Gate results

`bash tools/gates/dev-check.sh release` is **fully green** end to end:

- `lint: OK` (fatal `ruff check`, `ruff format --check`, `mypy --strict` over all 99
  package files).
- Artifacts rebuilt and verified: `apple-mail-plugin.zip`, byte-identical
  `apple-mail.plugin`, `apple-mail-mcp-v3.10.0.mcpb`; stale `apple-mail-mcp-v3.9.4.mcpb`
  pruned; `validate_manifests.sh: OK (version=3.10.0, tools=41)`; `mcpb unpack + validate
  OK`; `claude plugin validate --strict OK`.
- `pytest`: 1365 passed; test-count gate OK; tasks layout OK; repo root OK; wrapper
  surface OK; module budget: no module exceeds 600 LOC.
- `python3 tools/validators/sync_skill_references.py --check`: OK.
- All ten full AppleScript builders compile through real `osacompile`
  (`tests/calendar_surface/test_calendar_scripts.py::TestScriptsCompile`, runs on macOS,
  skips on CI).

## Live verification performed (read-only)

- **EventKit read fast path verified live on this host.** After installing the new
  optional extra into the dev venv, `APPLE_MAIL_CALENDAR_ENGINE=eventkit apple-mail
  calendars --json` returned all 15 real calendars with uids and writability, and
  `apple-mail calendar-events --days 7` returned real events with `engine: "eventkit"`,
  `expansion: "native"` on recurring occurrences, correct timezone echo, and no errors.
  This confirms platform-verify claim 11 (Calendars full access is already granted for
  terminal-launched processes here) and exercises the real PyObjC path end to end.
- **AppleScript engine correctly surfaces the pending Automation grant.**
  `apple-mail calendars` (AppleScript engine) timed out after 120 s and returned the
  designed error naming the Automation pane. This is the expected state: the Apple
  Events Automation prompt for Calendar.app is still unanswered on this host, so
  AppleScript reads and all writes stay blocked until a human answers it once
  (final plan section 8). No write, delete, or attendee operation was attempted live.

## Files created

### Engine (`plugin/apple_mail_mcp/calendar_core/`)

| File | Purpose |
|------|---------|
| `__init__.py` | Facade re-exports (mypy-strict clean patch surface) |
| `window.py` | `CalendarWindow` capability token, `bounded_calendar_window` (sole issuer), ISO/zoneinfo parsing, width caps, dual-zone output helpers |
| `validation.py` | Fuzzy calendar resolution (F6 algorithm), string uid shape doctrine, RRULE allowlist front door, alarm/attendee/slot validation |
| `records.py` | `\|\|\|` row-protocol parser with field-count and uid defense; JSON event payload builder (zone-local + UTC, notes preview, detail) |
| `recurrence.py` | Allowlisted RRULE parser and bounded occurrence expansion (COUNT/UNTIL/ceiling; MONTHLY/YEARLY+BYDAY flagged unsupported) |
| `scripts_read.py` | The only sanctioned emitters of `every event of ... whose` (date-bounded both ends); integer date blocks; sanitizer on every text field; recurring filter in-loop (F1) |
| `scripts_write.py` | Create/update/delete/calendar-CRUD builders; writable check before mutation; capture-before-delete; PATCH set-line builder |
| `engine.py` | `CalendarReadEngine` protocol, `AppleScriptCalendarEngine` (reads + all writes, 25-id delete chunking, write-error mapping incl. `-1743` to `CALENDAR_ACCESS_DENIED`), lazy `get_engine()` / `get_write_engine()` selection |
| `eventkit.py` | Guarded-import EventKit read engine; synchronous status check only, never requests access; Desktop-host denial reasons surfaced |

### Tools (`plugin/apple_mail_mcp/tools/calendar/`)

| File | Purpose |
|------|---------|
| `__init__.py` | Facade: engine seams + helpers first, then ten tool submodules |
| `helpers.py` | NEW mode-gate primitives (F4/F7), attendee gate, fan-out collector with `CALL_BUDGET_SECONDS` (F2), conflict checker, resolution glue, renderers |
| `calendars_list.py` | `list_calendars` (uids, writability, defaults, `eventkit_available` diagnostics) |
| `events_list.py` | `list_events` (bounded windows, query, recurring expansion, paging, budgeted fan-out) |
| `events_get.py` | `get_events_by_id` (25-id cap, bounded lookup, full detail, `missing` list) |
| `availability.py` | `check_availability` (62-day cap, padded fetch, busy merge, slot folding) |
| `events_create.py` | `create_event` (timezone-correct times, alarms, RRULE, `on_conflict`, gated attendees with delivery disclosure) |
| `events_batch.py` | `batch_create_events` (25 cap, all-or-nothing validation, per-item writes, dry run) |
| `events_update.py` | `update_event` (ID-first PATCH, span rules with dual future-route remediation, attendee set diffing per F5, dry run) |
| `events_delete.py` | `delete_events` (dry-run default, resolve-first abort on any missing id, caps, per-calendar chunked deletes) |
| `calendars_manage.py` | `manage_calendars` (create/rename/delete; exact-selector-only destructive targets; triple-gated cascade delete) |
| `rsvp.py` | `respond_to_invitation` documented-refusal shim (`CALENDAR_RSVP_UNSUPPORTED`, no engine call) |

### Skills and tests

| File | Purpose |
|------|---------|
| `plugin/skills/calendar-operator/SKILL.md` | Bounded reads, timezone discipline, ID-first mutations, destructive red-flag table, mode split, TCC troubleshooting with corrected performance citations |
| `plugin/skills/meeting-scheduler/SKILL.md` | Find-slot workflow, cross-timezone discipline, attendee platform reality, `.ics`-via-Mail draft alternative |
| `plugin/skills/references/calendar-safety-limits.md` | Canonical shared reference (bounds, mode matrix, error recovery, platform limitations); synced into both skills via `SYNC_MAP` |
| `tests/calendar_surface/` (16 files + conftest) | 312 tests: window/validation/records/recurrence primitives, script content + calendar lint + osacompile compile, engine selection and write-error mapping, stubbed EventKit engine, and per-tool suites covering happy paths, caps, error codes, escaping, and the full mode matrix |

## Files modified

| File | Change |
|------|--------|
| `plugin/apple_mail_mcp/constants.py` | `CALENDAR_BOUNDS` dict (incl. `CALL_BUDGET_SECONDS`, `RECURRING_MASTER_SCAN_CAP` per F1/F2) |
| `plugin/apple_mail_mcp/server.py` | `CALENDAR_WRITE_TOOLS`/`CALENDAR_DESTRUCTIVE_TOOLS`, `DEFAULT_CALENDAR`, `CALENDAR_ALLOW_DESTRUCTIVE`; `instructions` now documents the mail/calendar mode-semantics split (F12) |
| `plugin/apple_mail_mcp/__main__.py` | `--read-only` also removes calendar write + destructive tools |
| `plugin/apple_mail_mcp/__init__.py` | Imports the `calendar` tool surface (10 tools) |
| `plugin/apple_mail_mcp/cli/{parser,commands,__init__}.py` | `calendars`, `calendar-events`, `calendar-grant` subcommands (grant: the only EventKit request path, human-run, permission-specific exit codes 0/2/3) |
| `.claude/hooks/check_applescript_compiles.py` | Marker generalized to Mail or Calendar; calendar sample kwargs (Wave 0) |
| `tests/conftest.py` | Autouse calendar guardrails: known-test-calendar name stub plus a tripwire on the engine osascript seam so no test can touch live Calendar |
| `tests/core/test_read_only_registry.py` | Annotation sets extended with the 10 calendar tools |
| `tools/validators/sync_skill_references.py` | `SYNC_MAP` entries for both new skills |
| Six version surfaces | 3.10.0: `pyproject.toml` (+ `eventkit` optional extra), both `plugin.json`s, marketplace `plugins[0]`, `server.json` (x2), mcpb manifest (+ 10 `tools[]` entries) |
| `CHANGELOG.md` | `## 3.10.0 - 2026-07-10` release entry; nothing left under Unreleased |
| Docs (tool/skill counts + calendar guidance) | `AGENTS.md`, `CLAUDE.md`, `README.md` (calendar tool table, skills rows, mode-section updates), `docs/CLAUDE.md`, `docs/CLAUDE-conventions.md`, `docs/AGENT_LIVE_TESTING.md`, `plugin/apple_mail_mcp/CLAUDE.md`, `plugin/apple_mail_mcp/tools/CLAUDE.md` (module map + calendar surface notes), `plugin/docs/CLAUDE.md`, `plugin/skills/CLAUDE.md`, `.claude-plugin/CLAUDE.md`, `apple-mail-mcpb/CLAUDE.md`, `apple-mail-mcpb/build-mcpb.sh`, `tools/CLAUDE.md`, `tools/manifest_checks/artifacts.py`, `plugin/skills/email-management/README.md`, `tasks/reference/phase-plan-3.1.7.md` (F10) |
| `tasks/todo.md`, `tasks/INDEX.md` | Lane status updated to implemented; live-verification next action |
| `tools/expected_test_count.txt` | 1053 to 1365 |

Housekeeping: `tasks/active/apple-calendar-tools/verify-venv/` (43 MB PyObjC venv) moved
out of the repo tree to the session scratchpad (F17).

## Deviations from the final plan (all small, none from the draft-plan spine)

1. **CHANGELOG date**: the task template literally said `## 3.10.0 - undefined`; the
   release gate enforces `YYYY-MM-DD`, so the real date 2026-07-10 is used (recorded in
   the final plan as refinement deviation 1).
2. **Test directory is `tests/calendar_surface/`, not `tests/calendar/`**: pytest puts
   `tests/` on `sys.path`, so a `tests/calendar/` package shadows the stdlib `calendar`
   module and breaks unrelated imports (httpx). Same stdlib-shadowing trap the plan
   flagged for the source tree, discovered to also apply to the test tree.
3. **`update_event` lookup params are `lookup_days_back`/`lookup_days_ahead`** instead
   of the draft plan's single `start_hint`, for symmetry with `get_events_by_id` and
   `delete_events`.
4. **`check_availability` exposes `ignore_all_day_events` (default True)** instead of
   the draft's `ignore_free_availability`: neither engine surfaces a per-event
   free/busy transparency field in our record shape, so the honest and useful knob is
   all-day blocking. Cancelled events never block.
5. **Attendee/alarm detail ships on `get_events_by_id` only** (already recorded in the
   final plan): per-attendee reads are one Apple Event each and would multiply exactly
   the F1/F2 cost class on list rows.
6. **`INVALID_ATTENDEE_EMAIL`** added to the error-code table (final plan section 4).
7. **Attendee updates are additive**: Calendar.app scripting exposes no attendee
   removal, so `update_event` attaches only the diff and discloses the limitation in
   the response and skills.
8. **The generalized compile hook still skips calendar (and mail) modules that are part
   of the package import graph** when invoked standalone (pre-existing hook limitation:
   its isolated loader collides with the package's own facade imports). Real compile
   coverage lives in `tests/calendar_surface/test_calendar_scripts.py`, which compiles
   all ten full scripts through `osacompile` on macOS.

## Process notes for reviewers

- **Subagents and plugin-dev expert agents were not exposed by this host session** (no
  task-dispatch tool available), so the work was done directly per the repo's stated
  fallback and this gap is recorded here. Compensating controls: the full local gate
  matrix (`dev-check.sh release`), the calendar lint, the osacompile compile tests, the
  registry annotation matrix, and 312 new mocked tests.
- The em-dash and banned-word style contract holds for every added line and every new
  file (verified by diff scan). Pre-existing em dashes elsewhere in shipped
  descriptions remain in the separately tracked brand-voice sweep noted in
  `tasks/todo.md`.
- The dev venv now has the optional `pyobjc-framework-EventKit` extra installed (used
  for the live EventKit read verification above). The full suite was re-run with it
  installed: 1365 passed, confirming the stub-based EventKit tests do not depend on the
  dependency being absent.

## Remaining risks (carry into review and live verification)

1. **AppleScript engine is still unverified live** on this host: the Calendar
   Automation prompt is unanswered, so every AppleScript read and all writes wait on a
   human answering it once. The EventKit read path is verified; the write path
   (creates, updates, deletes, calendar CRUD) has osacompile-verified scripts and full
   mocked coverage but no live execution yet. Run final plan section 8's protocol
   (throwaway `MCP Test Calendar`) before trusting writes.
2. **AppleScript `whose` cost on years-deep stores is unmeasured** (F1). Result caps,
   scan caps, and the 240 s call budget bound the damage; `CALENDAR_BOUNDS` centralizes
   retuning; the calendar lint blocks new unbounded patterns.
3. **EventKit ids vs AppleScript uids**: the EventKit engine reports
   `calendarItemExternalIdentifier` (the iCal UID, which is what AppleScript's `uid`
   exposes) with `eventIdentifier` fallback. Cross-engine id round-trips (EventKit read
   then AppleScript delete) matched design review but should be spot-checked in live
   verification step 3.
4. **Attendee behavior remains account-type-dependent** and delivery is never
   guaranteed; shipped gated + disclosed, with the `.ics` alternative taught.
5. **PR #70 interaction**: this branch bumps 3.9.3 to 3.10.0 directly; whichever branch
   merges second resolves the six version files plus CHANGELOG (3.10.0 supersedes
   3.9.4).
6. **Codex runtime smoke** (`tools/gates/validate-codex-plugin.sh`) was not run:
   `plugin/.mcp.json` and the launch contract are untouched, and the smoke asserts
   mail-tool names only. Worth one run before merge if the reviewer wants belt and
   suspenders.
7. **Lane file naming**: the `-undefined` suffixed filenames are kept because the
   orchestration harness addresses these exact paths; rename to dated files when the
   lane closes (F11).
