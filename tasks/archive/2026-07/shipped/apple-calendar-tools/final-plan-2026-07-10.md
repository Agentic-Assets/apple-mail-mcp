# Apple Calendar tools: refined final plan (release 3.10.0)

Branch `feat/apple-calendar-tools`. This document supersedes
[`plan-2026-07-10.md`](plan-2026-07-10.md) for implementation. It incorporates every
CONFIRMED adversarial finding from the three phase-3 reports and is the document the
implementation follows exactly. Style contract: no em dashes and no banned brand-voice
words anywhere (code, docstrings, skills, manifests, CHANGELOG).

Reports adjudicated:

- [`reports/phase3-plan-attack-2026-07-10.md`](reports/phase3-plan-attack-2026-07-10.md) (F1..F17)
- [`reports/phase3-platform-verify-2026-07-10.md`](reports/phase3-platform-verify-2026-07-10.md) (claims 1..12)
- [`reports/phase3-examples-2026-07-10.md`](reports/phase3-examples-2026-07-10.md) (comparative survey)

---

## 1. Adversarial findings: accepted and rejected

### Accepted (change the plan or its framing)

| Finding | Disposition in this plan |
|---|---|
| **F4 / F7 / F12 (blockers)**: "mirrors mail gating" is false; `_send_blocked` is the only existing mode guard and it gates sends only | Accepted in full. The calendar mode gating is **new, stricter safety plumbing**, stated as such everywhere (this plan, skills, CHANGELOG). `server.py`'s `instructions` string is extended to state the domain split plainly: for mail tools the flags block sends only; for calendar tools `--read-only` removes all writes and `--draft-safe` blocks deletes and attendee sends. Extending equivalent gating to `manage_trash` / `move_email` is a separately scoped product decision recorded as a forward item, not smuggled into this release. `_calendar_write_blocked` / `_calendar_delete_blocked` / attendee gates are treated as a new safety primitive with dedicated test coverage (mode matrix parametrized per tool). |
| **F1 (major)**: the recurring-master pass is a `whose` on a non-indexed predicate; cost tracks store size | Accepted. Three mitigations ship: (a) a **calendar-specific static lint** in `tests/calendar/test_calendar_scripts.py`: every `every event of` occurrence in production source must live in `calendar_core/scripts_read.py` or `scripts_write.py`, and every `whose` predicate over events must carry both `start date >=` and `start date <=` bounds; (b) the recurring-master pass filters `recurrence` **in the repeat loop**, never in the `whose` predicate, and is capped by `RECURRING_MASTER_SCAN_CAP` (200); (c) the unmeasured-cost risk is documented in section 9 with the EventKit fast path named as the structural fix. |
| **F2 (major)**: no aggregate wall-clock budget on fan-out | Accepted. `CALENDAR_BOUNDS["CALL_BUDGET_SECONDS"] = 240`. The fan-out loops in `list_events`, `check_availability`, and `get_events_by_id` check elapsed monotonic time between calendars; when exhausted they stop, return partial results, list the skipped calendars in `calendar_errors`, and set `budget_exhausted: true`. |
| **F3 / F13 (minor)**: `calendar=None` fan-out inverts mail's `account=None` failure mode | Accepted as a documentation obligation, rejected as a behavior change. Reads keep capped fan-out as default because "what is on my calendar" legitimately spans calendars, unlike mail account scoping. Every fan-out tool docstring states this divergence explicitly, and the calendar-operator skill carries a callout. `DEFAULT_CALENDAR` remains a create-target default only, never a read filter. |
| **F5 (major)**: update_event attendee gate unspecified for the echo-current-list case | Accepted. `update_event` performs a bounded id lookup first (needed anyway for span enforcement and dry-run). The attendee gate fires only when the requested attendee set (normalized, lowercased emails) **differs** from the stored set; an identical echo is a no-op that skips the attendee write entirely and needs no confirmation. |
| **F6 (minor)**: fuzzy resolution rule self-contradictory | Accepted. The algorithm is now exact: (1) exact match wins; (2) unique case-insensitive exact match wins; two or more case-insensitive matches return `AMBIGUOUS_CALENDAR_SELECTOR`; (3) unique case-insensitive substring match wins; multiple return `AMBIGUOUS_CALENDAR_SELECTOR` with candidates; zero return `CALENDAR_NOT_FOUND` with `difflib.get_close_matches` candidates. Pure function `resolve_calendar_name(query, names)` in `calendar_core/validation.py`. Destructive targets (`manage_calendars` rename/delete) accept exact names only. |
| **F8 (minor)**: "text is mail's default" overstated | Accepted; this plan says json-default is the calendar-wide convention and notes mail already has json-default tools (`get_email_by_ids`, `full_inbox_export`) and a `ui` default (`inbox_dashboard`). |
| **F9 (minor)**: `get_events_by_id` chunk language unreachable | Accepted. `get_events_by_id` takes at most `MAX_EVENT_IDS_PER_CALL` (25) ids and issues one osascript call per scanned calendar; no chunk loop exists or is claimed. `delete_events` (up to 100 ids) is the tool that really chunks, 25 ids per osascript call. |
| **F10 (minor)**: skill-count list misses `tasks/reference/phase-plan-3.1.7.md` | Accepted; that file's two "nine" claims are updated alongside the seven listed locations. |
| **F11 / F17 (minor)**: `-undefined` filenames; `verify-venv/` inside the tracked lane | Partially accepted. The `-undefined` filenames are kept because the orchestration harness addresses these exact paths (documented hygiene debt, rename after the lane closes). `verify-venv/` (43 MB PyObjC venv) is moved out of the repo tree to the session scratchpad. |
| **Platform claim 6**: the "0.13 s / 462x" figure is a Reminders number | Accepted. Shipped docs and skills cite only the Calendar-specific numbers: roughly 61 to 112 seconds for Calendar.app AppleScript `whose` scans on community benchmarks versus an EventKit path described as near-instant, with the qualitative "roughly 3000x" claim attributed to `ical.sidv.dev`. |
| **Platform claim 11**: "no resolved Calendar TCC grant on this machine" is stale | Accepted. EventKit Calendars **full access is already granted** on this host for terminal-launched processes; the Apple Events Automation grant to Calendar.app is still unresolved. Live-verification preconditions (section 8) re-check status first instead of assuming both prompts are pending. Open risk 1 is reworded accordingly. |
| **Examples survey**: name both future span routes; JSON stream purity; never request TCC from tool calls | Accepted. `RECURRING_SPAN_UNSUPPORTED` remediation names two future unlocks: ASObjC `use framework "EventKit"` through the existing `run_applescript` path, and a signed self-disclaiming helper (FradSer PR #110 pattern). Tool return channels carry JSON or the documented text rendering only, never banners. No code path other than the human-invoked `apple-mail calendar-grant` CLI command ever calls an EventKit `request*` API. |

### Rejected (with reasons)

| Finding | Why rejected |
|---|---|
| F1's alternative "extend the repo-wide mail lint" | The mail lint's regexes are `every message of` specific and guard Mail variables; widening it would weaken a proven gate. A calendar-specific lint in the calendar test suite gives the same structural protection without touching the mail contract. |
| F4's optional suggestion to gate mail's `manage_trash` / `move_email` in this release | Explicitly out of scope: a silent semantic change to shipped mail tools inside a calendar feature PR is exactly the scope-smuggling the finding warns about. Recorded as a forward item requiring its own decision. |
| F3 as a behavior change (refuse unscoped reads) | Refusing `list_events()` outright would make the most common calendar question unanswerable without setup. Bounded, budgeted, documented fan-out is the correct default for calendars. |

Additional deviations from the draft plan made during refinement:

1. **CHANGELOG heading uses the real date** (`## 3.10.0 - 2026-07-10`). The release gate
   (`tools/manifest_checks/version.py`) enforces `## X.Y.Z - YYYY-MM-DD`; the literal
   "undefined" string in the task template cannot pass it.
2. **Attendee and alarm detail ships on `get_events_by_id` only**, not on every
   `list_events` row. Per-attendee and per-alarm reads are one Apple Event each; putting
   them on list rows multiplies exactly the cost class F1 and F2 flag. List rows carry
   `recurring`, `recurrence_rule`, and the standard fields.
3. **`list_calendars` drops the `account` field** (Calendar.app's scripting dictionary
   does not expose a calendar-to-account mapping) and reports `is_default` only from the
   `DEFAULT_CALENDAR` env var or the EventKit default when that engine is active.
4. **Engine seam split into read and write halves.** `get_engine()` returns a
   `CalendarReadEngine` (AppleScript or EventKit); `get_write_engine()` always returns
   the `AppleScriptCalendarEngine`. All writes are AppleScript in 3.10.0.
5. **Availability fetch pads the window back one day** so events that started before the
   requested window but overlap it still count as busy; longer-spanning events are a
   documented limitation.

---

## 2. Engine architecture (confirmed by phase-3 verification)

Hybrid, unchanged from the draft in substance: AppleScript via `core.run_applescript()`
(shared `_MAIL_LOCK`, 120 s default timeout) is the guaranteed engine for every
operation on every install surface; EventKit via PyObjC is an optional read fast path
that activates only when the module imports and
`authorizationStatusForEntityType_(event)` already reports full access (a synchronous,
non-prompting read, verified live). No tool call ever invokes `requestFullAccess*`.

- `APPLE_MAIL_CALENDAR_ENGINE` env: `applescript` | `eventkit` | `auto` (default),
  read lazily per call. Forced `eventkit` without availability returns
  `CALENDAR_ACCESS_DENIED` naming the reason; `auto` falls back silently.
- Every read response echoes `engine`; `list_calendars` adds `eventkit_available`
  with a reason string (distinguishes missing dependency, not determined, denied,
  and write-only).
- Dependencies: no new required deps. `[project.optional-dependencies] eventkit =
  ["pyobjc-framework-EventKit>=10,<13"]`. The MCPB bundles no PyObjC.
- Timezone correctness lives in Python (`zoneinfo`): inputs parsed to aware datetimes,
  converted to host-local integer components before script interpolation; outputs
  composed from numeric AppleScript date components and returned in the requested zone
  plus UTC. Stored per-event timezones remain an EventKit-write forward item.

## 3. Tool surface (10 new tools; 31 + 10 = 41)

Signatures and behavior as in draft plan section 3, with these refinements applied:

- Shared: `output_format: str = "json"` on every tool ("text" renders a readable
  summary; structured errors always return the JSON envelope); `timeout: int | None`
  threaded to the engine; reads echo `engine`, `resolved_timezone`, and paging fields;
  fan-out reads add `calendar_errors` and `budget_exhausted`.
- `list_calendars` (READ_ONLY): calendar_id (uid, `id_kind` reported), name, writable,
  description; `default_calendar`; `engine`; `eventkit_available`.
- `list_events` (READ_ONLY, async): windows per `bounded_calendar_window` (defaults
  `days_back=0, days_ahead=7`); caps `MAX_WINDOW_DAYS` 370, `EVENT_RETURN_CAP` 200 with
  `offset` paging, `OCCURRENCE_SCAN_CEILING` 750, fan-out `MAX_CALENDARS_PER_QUERY` 20,
  aggregate `CALL_BUDGET_SECONDS` 240. `query` matches title/location/notes in Python.
  AppleScript engine: bounded primary pass plus recurring-master pass (in-loop
  recurrence filter, `RECURRING_MASTER_SCAN_CAP` 200, lookback 400 days) expanded by
  `calendar_core/recurrence.py`; unsupported rules returned flagged
  (`expansion: "unsupported_rrule"`), never dropped.
- `get_events_by_id` (READ_ONLY, async): 1..25 exact ids, bounded lookup window
  (defaults back 30 / ahead 90 days), full detail (untruncated notes, alarms,
  attendees); returns `{"events": [...], "missing": [...]}`.
- `check_availability` (READ_ONLY, async): required `start`/`end`, width <= 62 days,
  slot folding in Python over one padded bounded fetch; busy blocks + free slots inside
  working hours; `INVALID_SLOT_PARAMS` for out-of-range knobs.
- `create_event` (WRITE): exactly one of `end` / `duration_minutes` (all-day may omit
  both, 1 day); fuzzy calendar resolution with `DEFAULT_CALENDAR` then engine default;
  alarms <= 5, each 0..40320 minutes; allowlisted RRULE; in-line conflict detection
  `on_conflict = "warn" | "block" | "allow"` (bounded query over the event's own span);
  attendees gated (see section 5) and the response always carries
  `"invitation_delivery": "platform_dependent"`.
- `batch_create_events` (WRITE): 1..25 items, one target calendar, items may not carry
  `attendees` or `recurrence`; validate all items before any write; per-item results;
  `dry_run` previews with conflicts.
- `update_event` (IDEMPOTENT_WRITE): ID-first PATCH; bounded lookup first; recurring
  targets require `span` (`all_occurrences` only on AppleScript;
  `RECURRING_SPAN_UNSUPPORTED` otherwise, remediation naming both future routes);
  attendee gate on set-diff only (F5); `dry_run` returns resolved target + field diff.
- `delete_events` (DESTRUCTIVE): exact ids only, 1..100, `dry_run=True` default,
  `max_deletes` default 20 / ceiling 100, ids chunked 25 per osascript call; any
  unresolved id fails the whole preview (`EVENT_NOT_FOUND`); recurring ids require
  `span`; summaries captured before deletion.
- `manage_calendars` (DESTRUCTIVE, multiplexed): `create` / `rename` / `delete`;
  create+rename are writes allowed under draft-safe; delete requires the dry-run
  preview then `dry_run=False` + `confirm_delete_calendar=True` (+ `force_nonempty=True`
  when events exist); rename/delete accept exact `name` or `calendar_id` only.
- `respond_to_invitation` (READ_ONLY shim): always returns `CALENDAR_RSVP_UNSUPPORTED`
  (no public RSVP API on any engine, verified against SDK headers live); registered in
  every mode; no engine call.

## 4. Bounds and error codes

`constants.CALENDAR_BOUNDS` (single dict, one edit retunes everything):
MAX_WINDOW_DAYS 370, EVENT_RETURN_CAP 200, EVENT_SCAN_CAP 300 (inner per-pass slice),
OCCURRENCE_SCAN_CEILING 750, AVAILABILITY_MAX_WINDOW_DAYS 62,
AVAILABILITY_FETCH_PAD_DAYS 1, BATCH_CREATE_CAP 25, BULK_DELETE_DEFAULT_MAX 20,
BULK_DELETE_CEILING 100, MAX_ATTENDEES 50, MAX_ALARMS_PER_EVENT 5,
MAX_EVENT_IDS_PER_CALL 25, MAX_CALENDARS_PER_QUERY 20, DEFAULT_UPCOMING_DAYS 7,
UID_LOOKUP_BACK_DAYS 30, UID_LOOKUP_AHEAD_DAYS 90, RECURRING_LOOKBACK_DAYS 400,
RECURRING_MASTER_SCAN_CAP 200, NOTES_PREVIEW_CHARS 280, CALL_BUDGET_SECONDS 240.

`bounded_calendar_window(...)` in `calendar_core/window.py` is the only issuer of
`CalendarWindow` tokens; engines refuse foreign tokens.

Error codes (all through the existing `ToolError` / `serialize_tool_error` envelope):
`UNBOUNDED_CALENDAR_SCAN`, `CALENDAR_WINDOW_TOO_WIDE`, `CALENDAR_WINDOW_TOO_DENSE`,
`INVALID_EVENT_WINDOW`, `INVALID_TIMEZONE`, `INVALID_EVENT_ID`,
`INVALID_RECURRENCE_RULE`, `INVALID_ALARM`, `INVALID_SLOT_PARAMS`,
`INVALID_ATTENDEE_EMAIL` (added during refinement: malformed attendee address),
`EVENT_NOT_FOUND`, `CALENDAR_NOT_FOUND`, `AMBIGUOUS_CALENDAR_SELECTOR`,
`CALENDAR_ALREADY_EXISTS`, `CALENDAR_READ_ONLY`, `CALENDAR_WRITE_BLOCKED`,
`CALENDAR_DELETE_BLOCKED`, `INVITE_SEND_REQUIRES_CONFIRM`, `INVITE_SEND_BLOCKED`,
`TOO_MANY_ATTENDEES`, `BATCH_TOO_LARGE`, `TOO_MANY_DELETES`,
`CALENDAR_CONFIRMATION_REQUIRED`, `RECURRING_SPAN_REQUIRED`,
`RECURRING_SPAN_UNSUPPORTED`, `EVENT_CONFLICT`, `CALENDAR_RSVP_UNSUPPORTED`,
`CALENDAR_ACCESS_DENIED`.

AppleScript timeouts return a plain-text error (mail convention) whose message names
the Automation pane as the likely first-use cause; `-1743` in an engine error maps to
`CALENDAR_ACCESS_DENIED` with pane-specific remediation.

## 5. Mode gating (new plumbing, stated as such)

| Tool / path | default | `--draft-safe` | `--read-only` |
|---|---|---|---|
| list/get/availability reads | ALLOW (bounded) | ALLOW | ALLOW |
| `create_event` / `update_event` / `batch_create_events` (no attendees) | ALLOW | ALLOW | BLOCK (registry removal + `CALENDAR_WRITE_BLOCKED` backstop) |
| `manage_calendars` create/rename | ALLOW | ALLOW | BLOCK (removed + backstop) |
| create/update with attendee changes | requires `send_invitations=True` else `INVITE_SEND_REQUIRES_CONFIRM` | BLOCK `INVITE_SEND_BLOCKED` | BLOCK (removed) |
| `delete_events` | RESTRICT (dry-run default, caps, span) | BLOCK `CALENDAR_DELETE_BLOCKED` unless env `CALENDAR_ALLOW_DESTRUCTIVE=1` at launch | BLOCK (removed) |
| `manage_calendars` delete | RESTRICT (dry-run + confirm + force chain) | BLOCK `CALENDAR_DELETE_BLOCKED` (same env unlock) | BLOCK (removed) |
| `respond_to_invitation` | `CALENDAR_RSVP_UNSUPPORTED` | same | same (stays registered) |

`server.py`: `CALENDAR_WRITE_TOOLS`, `CALENDAR_DESTRUCTIVE_TOOLS`, `DEFAULT_CALENDAR`,
`CALENDAR_ALLOW_DESTRUCTIVE`; `instructions` extended with the mode-domain split.
`__main__.py` removes both tuples under `--read-only`. Every write and destructive tool
keeps internal `_server.READ_ONLY` / `_server.DRAFT_SAFE` guards because the CLI
bypasses registry removal. These guards are new code (F7), tested as a first-class
safety primitive in `tests/calendar/test_calendar_gating.py`.

## 6. Skills (9 -> 11) and shared reference

- `plugin/skills/calendar-operator/SKILL.md`: bounded reads, timezone discipline,
  ID-first mutations, dry-run deletes, mode matrix including the mail/calendar
  flag-semantics split, TCC troubleshooting (Automation pane, silent-hang first-use
  signature, corrected performance citations), destructive red-flag table, platform
  gaps (invitations, RSVP).
- `plugin/skills/meeting-scheduler/SKILL.md`: find-slot then conflict-checked create,
  cross-timezone workflow, attendee gate and platform delivery reality, the
  `.ics`-via-Mail draft alternative through the gated compose path (draft first, never
  auto-send).
- Shared canonical reference `plugin/skills/references/calendar-safety-limits.md`
  (bounds, mode matrix, error recovery, platform limitations); `SYNC_MAP` gains both
  skills; run `python3 tools/validators/sync_skill_references.py`.
- Skill-count claims updated at: `AGENTS.md`, `CLAUDE.md`, `README.md`,
  `plugin/docs/CLAUDE.md`, `plugin/skills/email-management/README.md`,
  `docs/AGENT_LIVE_TESTING.md`, `.claude-plugin/CLAUDE.md`, plus
  `tasks/reference/phase-plan-3.1.7.md` (F10), then a repo-wide re-grep.

## 7. Release mechanics

1. Version 3.9.3 -> 3.10.0 on all six surfaces (`pyproject.toml`,
   `plugin/.claude-plugin/plugin.json`, `plugin/.codex-plugin/plugin.json`,
   `.claude-plugin/marketplace.json` `plugins[0].version`, `server.json` top-level and
   `packages[0].version`, `apple-mail-mcpb/manifest.json`).
2. CHANGELOG: `## 3.10.0 - 2026-07-10`, nothing under Unreleased.
3. Tool counts 31 -> 41 in every `ACTIVE_DOC_TOOL_COUNT_REQUIRED` file, both
   `plugin.json` descriptions, marketplace description, mcpb description, and
   `tools/manifest_checks/artifacts.py` (embedded README); mcpb `tools[]` gains the 10
   names. Recount: `find plugin/apple_mail_mcp/tools -name '*.py' | xargs grep -h
   '^@mcp.tool' | wc -l`.
4. `tests/core/test_read_only_registry.py` annotation sets gain the 10 tools
   (read-only: list_calendars, list_events, get_events_by_id, check_availability,
   respond_to_invitation; write: create_event, batch_create_events; idempotent:
   update_event; destructive: delete_events, manage_calendars).
5. `tools/expected_test_count.txt` recounted after the suite lands.
6. `bash tools/gates/dev-check.sh release` fully green before handoff. Where the
   plugin-dev expert agents are not exposed by this host, run the local gates and
   record the gap in the phase-4 report.
7. PR #70 interaction unchanged from the draft plan (whichever merges second resolves
   the six-file version conflict; 3.10.0 supersedes 3.9.4).

## 8. Live verification (deferred to a human-present session)

Protocol as draft plan section 9 with one correction (platform claim 11): begin by
re-reading both grant states. On this host EventKit full access for Events is already
granted to terminal-launched processes; the Automation prompt for Calendar.app is still
unanswered and is the actual blocker for AppleScript reads and all writes. pytest never
touches live Calendar; live verification is a separate, owner-present step.

## 9. Open risks (updated)

1. **Automation grant unresolved on this host** (EventKit read grant is resolved).
   Until a human answers the Calendar Automation prompt, no live AppleScript
   verification or timing is possible. Timeout discipline plus pane-naming errors
   mitigate; the risk is schedule, not safety. The examples survey adds: on some
   host/OS combinations the EventKit prompt may be structurally impossible for the
   host app; `eventkit_available` reasons keep that visible.
2. **AppleScript `whose` cost tracks store size, not window size** (F1). The primary
   and recurring-master passes are result-capped and budget-capped but their engine-side
   cost on a years-deep store is unmeasured on this machine. `CALENDAR_BOUNDS` is
   centralized for one-edit retuning; the EventKit fast path is the structural fix on
   terminal hosts; the calendar lint prevents new unbounded patterns.
3. **Attendee behavior is account-type-dependent and delivery is never guaranteed**
   (verified against primary sources). Shipped gated, confirmed, disclosed; the
   meeting-scheduler skill teaches the `.ics` draft alternative.
4. **Recurring semantics under AppleScript**: whole-series span only; exotic RRULEs
   returned unexpanded and flagged.
5. **Shared osascript lock**: calendar and mail serialize behind one lock; a slow
   calendar fan-out delays mail tools (budget cap bounds the worst case).
6. **Mode-semantics asymmetry between domains** (F12) is now documented in the server
   instructions and skills; unifying mail-side gating is a named forward item.
7. **Forward queue**: EventKit write engine (per-event timezones, `EKSpan` granular
   spans), ASObjC `use framework "EventKit"` span route, signed disclaiming helper,
   mail-side destructive gating parity, conference-link extraction.

## 10. Work breakdown (implementation order)

1. Wave 0: generalize `.claude/hooks/check_applescript_compiles.py` marker to
   Mail or Calendar; add calendar sample kwargs.
2. `constants.py` `CALENDAR_BOUNDS`.
3. `calendar_core/`: `window.py`, `validation.py`, `records.py`, `recurrence.py`,
   `scripts_read.py`, `scripts_write.py`, `engine.py`, `eventkit.py`, facade
   `__init__.py`. Every module under 600 physical lines, mypy strict, ruff clean.
4. `server.py` + `__main__.py` plumbing.
5. `tools/calendar/`: `helpers.py`, ten tool modules, facade `__init__.py`
   (core/server symbols first, then submodules, then `__all__`); root package
   `__init__.py` imports the new surface. Never `import calendar` (stdlib) inside.
6. CLI: `calendars`, `calendar-events`, `calendar-grant` subcommands.
7. Skills + `calendar-safety-limits.md` + `SYNC_MAP` + sync run.
8. Tests: `tests/calendar/` per section 7 of the draft plan, plus the calendar lint,
   the compile check (skipped without osacompile), conftest autouse calendar fixture,
   and registry-test updates.
9. Docs, manifests, version bump, CHANGELOG, counts, expected test count.
10. `bash tools/gates/dev-check.sh release` iterated to green; phase-4 report.
