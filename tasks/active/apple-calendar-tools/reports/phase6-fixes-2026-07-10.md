# Phase 6: fixes and adjudication (feat/apple-calendar-tools)

Branch `feat/apple-calendar-tools`, uncommitted working tree, worked 2026-07-10.
Inputs adjudicated: the phase-5 gate report
([`phase5-gates-2026-07-10.md`](phase5-gates-2026-07-10.md)), the phase-5 live smoke
report ([`phase5-live-smoke-2026-07-10.md`](phase5-live-smoke-2026-07-10.md)), the phase-4
implementation report ([`phase4-implementation-2026-07-10.md`](phase4-implementation-2026-07-10.md)),
and the refined final plan ([`../final-plan-2026-07-10.md`](../final-plan-2026-07-10.md)).
Git rules honored: no commit, no checkout, no push, no branch switch, no stash. Work was
done in the live working tree only.

## Headline

Every finding in the three reports was re-verified against the actual source. No CONFIRMED
code blocker, major, or cheap minor was found, so no source code was changed. The one real
blocker (calendar writes cannot run live) is a macOS Automation consent (TCC) gap in the
host environment, not a code defect; the operator action is documented below. The repo's
governing release gate (`bash tools/gates/dev-check.sh release`) was re-run from scratch in
this session and is fully green (exit 0).

## Finding to action table

| # | Source | Finding | Verified against source | Action | Evidence |
|---|--------|---------|-------------------------|--------|----------|
| 1 | Gates 1a | `ruff check plugin/apple_mail_mcp tools` reports 3 errors (`tools/probes/mcp_tool_smoke.py`, `tools/probes/patch_mcporter_wrapper.py`, `tools/validators/validate_repo_root.py`) | Yes: all 3 files have `git diff HEAD` = 0 lines; not in the branch diff; `tools/` is outside the gate's lint scope (`dev-check.sh` lints `plugin/apple_mail_mcp/` only) | REJECTED (pre-existing debt, out of scope) | The four `tools/` files this branch does touch (`CLAUDE.md`, `expected_test_count.txt`, `manifest_checks/artifacts.py`, `validators/sync_skill_references.py`) are ruff-check clean and ruff-format clean; fixing unrelated `tools/` debt inside a calendar PR is the scope-smuggling the plan rejects for F4 |
| 2 | Gates 2a | `ruff format --check plugin/apple_mail_mcp tools` would reformat 10 `tools/` files | Yes: all 10 have `git diff HEAD` = 0 lines; same scope gap as #1 | REJECTED (pre-existing debt, out of scope) | Same as #1; belongs to a separate `dev-check.sh` scope-change lane if the team wants `tools/` covered |
| 3 | Live smoke, rows 14-22, 37 | Calendar writes (`create_event`, `update_event`, `batch_create_events`, `delete_events`, `manage_calendars` create/delete) could not run live | Yes: writes route `get_write_engine()` -> `AppleScriptCalendarEngine` -> `run_applescript()`; a pending "Terminal wants to control Calendar" Automation consent modal blocks the first Apple Events call; the tool returned the documented 120s timeout naming the Automation pane | ENVIRONMENT BLOCKER (code cannot fix); operator action documented below; live write battery stays DEFERRED to a human-present session | `timeout_error()` in `plugin/apple_mail_mcp/tools/calendar/helpers.py` L57-59 emits the exact Automation-pane message the smoke observed; this is the plan's "Open risk 1" (final plan section 9) |
| 4 | Live smoke, row 23 | Attendee gate fires before any engine call in default mode | Yes: `create_event` body runs `output_format_error`, then `calendar_write_blocked` (no-op when `READ_ONLY=False`), then `attendee_gate` (returns `INVITE_SEND_REQUIRES_CONFIRM`), all before `resolve_create_target`/engine | NO ACTION (correct as designed) | `events_create.py` L143-153; matches smoke result |
| 5 | Live smoke, rows 1-13 | Reads, bounds, fuzzy resolution, availability | Verified correct in the report against real Calendar data (EventKit fast path, `full_access`); bounds error codes (`CALENDAR_WINDOW_TOO_WIDE`, `UNBOUNDED_CALENDAR_SCAN`, `INVALID_SLOT_PARAMS`, `INVALID_EVENT_ID`) all fired as specified | NO ACTION (correct) | Re-confirmed by the green pytest suite (1365 passed) covering the same code paths with mocks |
| 6 | Live smoke, rows 24-37 | Mode gating (read-only backstop, draft-safe delete block, attendee block) | Yes: `calendar_write_blocked` checks only `_server.READ_ONLY`; `calendar_delete_blocked` chains write-block then `DRAFT_SAFE and not CALENDAR_ALLOW_DESTRUCTIVE`; `attendee_gate` blocks outright under `READ_ONLY or DRAFT_SAFE` regardless of the confirm flag | NO ACTION (correct, strongest guarantee held) | `helpers.py` L62-138; smoke rows 32-35 confirm draft-safe attendee block holds even with `send_invitations=True` |
| 7 | Phase-4 deviations 1-8 | CHANGELOG date, `tests/calendar_surface/` naming, lookup param names, `ignore_all_day_events`, detail-on-get-only, `INVALID_ATTENDEE_EMAIL`, additive attendee updates, compile-hook skip | Cross-checked each against the final plan; all are recorded refinement decisions or platform limitations (Calendar.app exposes no attendee-removal scripting), not defects | NO ACTION (documented design) | Final plan section 1 deviations 1-5 and section 9 forward queue; all disclosed in tool responses and skills |
| 8 | F11/F17 | `-undefined` suffixed lane filenames | Yes: kept deliberately because the orchestration harness (and this task) address these exact paths | DEFERRED (rename when the lane closes; documented hygiene debt) | Final plan section 1, phase-4 report remaining-risk 7 |

## Why no code was changed

The gate report's own bottom line is that the governing release gate is fully green and the
branch introduced no lint, format, type, test, budget, or sync regression. The live smoke
report exercised reads and the entire mode-gating matrix live and found them correct, and
verified the one unreachable slice (draft-safe write ALLOW path) by static trace. I
re-verified the load-bearing pieces directly (helpers gating, `create_event` gate ordering,
the `tools/` diff-vs-HEAD claims) and reproduced the full gate from a clean start. Editing a
green branch to chase pre-existing `tools/` debt outside the gate's scope would expand the
diff into unrelated files and violate the repo's minimal-change discipline, so it is recorded
as rejected rather than applied.

## Operator action required (TCC, not code)

Calendar **writes** stay unverifiable live until a human answers the macOS Automation consent
once on this host:

1. Open **System Settings > Privacy & Security > Automation**, find the terminal app
   (the "Terminal wants to control Calendar" grant), and enable **Calendar**; or answer
   **Allow** on the pending consent dialog when it next appears.
2. Re-run the write battery (throwaway `MCP Test Calendar`, per final plan section 8):
   alarm/timezone round trip, conflict warn/block, update, batch create, dry-run-then-real
   bulk delete, single delete, calendar delete, and the draft-safe write ALLOW path
   (smoke rows 15-22, 37).

Do not click through the system security dialog on the user's behalf from an unattended
session: declining it converts a recoverable "pending" state into a persistent `-1743`
denial that needs `tccutil reset Calendar` to undo. EventKit Calendars full access is already
granted for terminal-launched processes here, so every read path is verified; the Automation
grant is a separate, independent permission that only the write path needs.

## Final gate status

`bash tools/gates/dev-check.sh release` re-run from a clean start this session: **exit 0,
fully green.**

```
lint: OK                       (ruff check + ruff format --check + mypy --strict, 99 files)
validate_manifests.sh: OK      (version=3.10.0, tools=41)
mcpb unpack + validate OK
claude plugin validate --strict OK
tasks layout: OK
repo root: OK
test count: OK                 (1365 collected, matches tools/expected_test_count.txt)
wrapper surface: OK
```

Test count is unchanged (1365); no test was added, weakened, or removed, so
`tools/expected_test_count.txt` did not move. The gate rebuilt `apple-mail-plugin.zip`
byte-identically and touched no source file; the working tree remains the expected
calendar-branch set (12 modified `.py` files plus the 7 untracked calendar directories/files,
matching the phase-4 report).

## Verification commands (reproduction)

```bash
cd /Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp
git diff HEAD -- tools/probes/mcp_tool_smoke.py | wc -l          # 0 (pre-existing debt)
.venv/bin/ruff check   tools/manifest_checks/artifacts.py tools/validators/sync_skill_references.py
.venv/bin/ruff format --check tools/manifest_checks/artifacts.py tools/validators/sync_skill_references.py
bash tools/gates/dev-check.sh release                            # exit 0, fully green
```
