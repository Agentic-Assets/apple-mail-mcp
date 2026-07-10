# Apple Calendar tools: consolidated phase-1 research report

Lead architect consolidation of five phase-1 researcher reports, branch
`feat/apple-calendar-tools`, 2026-07-10. This document is the durable evidence base for
the implementation plan at
[`plan-2026-07-10.md`](plan-2026-07-10.md). Every design decision in that plan traces to a
section here, and every claim here traces to a primary source (URL, repo file path, or a
live measurement from this machine).

Source reports (all under `tasks/active/apple-calendar-tools/reports/`):

| # | Report | Scope |
|---|--------|-------|
| 1 | `phase1-reference-skill-2026-07-10.md` | Teardown of the `sundial-org/awesome-openclaw-skills` apple-calendar skill |
| 2 | `phase1-codebase-map-2026-07-10.md` | apple-mail-mcp integration map (primitives, patterns, gates, release surfaces) |
| 3 | `phase1-platform-apis-2026-07-10.md` | AppleScript vs EventKit vs JXA vs helpers; TCC; RSVP; packaging |
| 4 | `phase1-live-probe-2026-07-10.md` | Live probes on this Mac mini (macOS 26.5.2, M4) |
| 5 | `phase1-safety-design-2026-07-10.md` | Safety contract: bounds, mode gating, error codes, injection, id doctrine |

---

## 1. Executive summary

1. **The reference skill covers the six baseline operations and nothing else.** It covers
   none of the ten requested gap capabilities and none of the safety requirement, and it
   ships two performance anti-patterns that map directly onto the Mail CPU-spin class
   this repo fixed in v3.9.3 (unbounded `whose uid is X` scans across every calendar, and
   caller-controlled unbounded date windows). Its output is inconsistent, unescaped,
   pipe-delimited text. Verdict: borrow specific AppleScript techniques, port nothing
   wholesale. (Section 2.)
2. **Two owner capabilities are not available through any public macOS API: sending
   invitations that actually reach attendees, and RSVP (accept/decline/tentative).**
   AppleScript can attach an attendee object but never transmits the invite;
   EventKit's `attendees` array and `EKParticipant.participantStatus` are read-only per
   Apple's own documentation. (Sections 4.2, 4.5.)
3. **Performance forces a hybrid engine.** Community benchmarks put Calendar.app
   AppleScript bounded reads at 60 to 112 seconds on modest calendars versus roughly
   0.13 seconds for EventKit (a 460x to 3000x gap). But EventKit is gated by the macOS
   Calendars TCC category attributed to the responsible host process, and Claude Desktop
   and Codex Desktop do not declare the required usage strings, so plain EventKit is
   denied with no prompt on the `.mcpb` surface. AppleScript rides the Apple Events
   Automation grant this repo's Mail tools already use and works on every surface.
   (Sections 4.3, 4.4.)
4. **This machine currently has no resolved Calendar TCC grant on any path.** Every live
   Apple Event to Calendar.app (and to Finder as a control) hung to timeout, and
   EventKit reports `notDetermined` with an access request that never completes. All
   real Calendar.app timing numbers still need a follow-up probe after a human answers
   the consent prompts once. (Section 5.)
5. **The repo already ships every safety primitive the calendar surface needs**
   (`ToolError` envelope, bounded-scan capability tokens, central cap dicts, `--read-only`
   / `--draft-safe` plumbing, `dry_run` + `confirm_*` destructive patterns, ID-first
   mutation doctrine). The calendar surface adds calendar-shaped instances of each,
   no new safety concepts. (Sections 3, 6.)

---

## 2. Reference skill teardown (`sundial-org/awesome-openclaw-skills` apple-calendar)

### 2.1 What it is

One `SKILL.md` (45 lines) plus seven shell scripts, all thin `osascript` wrappers over
Calendar.app's classic AppleScript dictionary. No `references/`, no tests, no JXA, no
EventKit, no icalBuddy. Fetched via the GitHub Contents API and raw.githubusercontent.com
(all 200s):

- Directory listings:
  - https://api.github.com/repos/sundial-org/awesome-openclaw-skills/contents/skills/apple-calendar
  - https://api.github.com/repos/sundial-org/awesome-openclaw-skills/contents/skills/apple-calendar/scripts
- File bodies (raw, `main`):
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/SKILL.md
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-create.sh
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-delete.sh
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-events.sh
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-list.sh
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-read.sh
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-search.sh
  - https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-update.sh
- Human-viewable tree:
  - https://github.com/sundial-org/awesome-openclaw-skills/tree/main/skills/apple-calendar
  - https://github.com/sundial-org/awesome-openclaw-skills/tree/main/skills/apple-calendar/scripts

A separate machine-global copy of this skill exists on this Mac at
`~/.claude/skills/apple-calendar/` (mirrored at `~/.codex/skills/apple-calendar/` and
`~/.agents/skills/apple-calendar/`). It is not part of this repo and must not be shipped
or copied; it is useful only as an AppleScript syntax reference.

### 2.2 Coverage vs the owner's target list

Baseline (all six covered, none safely): list calendars (`cal-list.sh`), list/today/
upcoming events (`cal-events.sh`), create (`cal-create.sh`), update (`cal-update.sh`),
delete (`cal-delete.sh`), search (`cal-search.sh`).

Owner gap list: **all ten missing.**

| # | Capability | Status in reference skill |
|---|------------|---------------------------|
| 1 | Attendees / invitations | Missing; no attendee property read or set anywhere |
| 2 | Alarms | Missing; no alarm object created anywhere |
| 3 | Free-busy / find-slot | Missing; raw event lists only, no gap logic |
| 4 | Batch / bulk ops | Missing; every script is one event per invocation |
| 5 | Calendar CRUD | Missing; list only |
| 6 | Default calendar + fuzzy match | Missing; exact case-sensitive `calendar name` lookups only |
| 7 | Timezone handling | Missing; `current date` local wall-clock only, no tz parameter anywhere |
| 8 | RSVP | Missing (and per section 4.5, not cleanly possible on any engine) |
| 9 | Structured JSON | Missing; three mutually inconsistent text formats, unescaped `\|` delimiter |
| 10 | Conflict detection | Missing; create never reads existing events |

Safety requirement: destructive gating missing entirely (`cal-delete.sh` deletes on a UID
match with no dry-run, no confirm, no mode check, and no series-vs-instance distinction);
bounded reads partial (`cal-events.sh`/`cal-search.sh` take a window but with no maximum
and no result cap); `cal-read.sh`, `cal-update.sh`, and `cal-delete.sh` run
`every event of cal whose uid is eventUID` with **no date bound**, repeated across
**every calendar** when unscoped. That is the direct Calendar analogue of the Mail 98%
CPU spin fixed in v3.9.3. No script wraps `osascript` in a timeout.

### 2.3 Techniques to borrow

- `osascript - "$ARG" <<'EOF' ... on run argv ...` argv-passing (matches this repo's
  escaped-input doctrine; this repo goes further with `core.run_applescript()` stdin).
- Bulk property fetch (`name of every calendar` + `writable of every calendar`, two Apple
  Events total) instead of per-item property loops.
- `writable of cal` guard before every write.
- Field-by-field date construction (mutate `year`/`month`/`day`/`hours`/`minutes` of a
  `current date` copy) instead of locale-dependent `date "..."` string coercion.
- `AppleScript's text item delimiters` split/restore idiom.
- UID-based addressing as the identity model (with mandatory bounds added).
- Plain iCalendar RRULE strings on `recurrence` (works for create/update).
- `missing value` normalization before string concatenation.
- Capture-before-delete (read `summary` before `delete e`).
- PATCH-style per-field conditional mutation on update.

### 2.4 Anti-patterns to refuse

- Unbounded UID lookup with no date window, fanned across all calendars.
- Destructive action with zero safety gate and no series/instance distinction.
- Caller-controlled unbounded windows and uncapped result counts.
- In-AppleScript hand-rolled per-character case folding (do the match in Python;
  AppleScript `ignoring case` blocks exist as the light alternative).
- No client-side timeout on any `osascript` call.
- Inconsistent, unescaped pipe-delimited output (7-field rows in one script, 6 in
  another, `Key: Value` blocks in a third; `SKILL.md` docs do not match the code).
- Errors as plain `"Error: ..."` strings on the success channel.
- Copy-pasted helper handlers between scripts instead of shared code.

---

## 3. Codebase integration map (what the calendar surface plugs into)

### 3.1 Load-bearing primitives (reuse unchanged)

- **`core.run_applescript(script, timeout=120)`**
  (`plugin/apple_mail_mcp/core/applescript.py`): stdin osascript (never `-e`),
  `timeout=None` maps to 120s, raises `AppleScriptTimeout`, output sanitized via
  `_sanitize_for_json`, and a module-level single-flight `threading.Lock` (`_MAIL_LOCK`,
  300s acquire cap) serializes every osascript call process-wide. Decision recorded in
  section 7: the calendar engine reuses this function and therefore the same lock; a
  bypassing `subprocess.run(["osascript", ...])` would break the serialization guarantee.
- **`core.escape_applescript(value)`** (`core/escaping.py`): backslash, quote, CR/LF/tab,
  and Unicode U+2028/U+2029 line separators. Every user string interpolated into an
  AppleScript literal goes through it. App-agnostic; reuse as-is.
- **`core.sanitize_pipe_delimited_field(var_name)`**: AppleScript-side snippet that
  neutralizes `|||` and control characters in a variable before a pipe-join. Required on
  every free-text field in any calendar row emission, exactly as the mail search scripts
  do, because a corrupted row can shift an id under a later destructive call.
- **`backend.base.ToolError` / `serialize_tool_error`**: the
  `{"error": true, "code", "message", "remediation"}` envelope. Mail-agnostic; the
  calendar surface reuses it and adds calendar codes (section 6.3). Do not invent a
  parallel envelope.
- **`constants.SCAN_BOUNDS`** pattern: one module-level dict of named caps, documented
  with the incident that motivated each. Calendar gets a sibling `CALENDAR_BOUNDS` dict.
- **Structured error codes already in production** (producers verified by researcher 2):
  `UNBOUNDED_SCAN_REQUIRED`, `TARGET_SELECTOR_DEPRECATED`, `FILTER_SCAN_DISABLED`,
  `BODY_SCAN_DISABLED`, `INVALID_SCAN_WINDOW`, `WHOSE_ID_LIST_TOO_LARGE`,
  `UNSAFE_WHOSE_ON_LIST`, `UNBOUNDED_EXPORT_DISABLED`.

### 3.2 Tool definition pattern

- Registration is import-side-effect: `plugin/apple_mail_mcp/__init__.py` imports six
  tool-surface packages; each package `__init__.py` is a facade that imports core/server
  symbols first (making them patchable package attributes for tests), then each tool
  submodule, then re-exports in `__all__`. A new `tools/calendar/` package must follow
  the same facade shape.
- Decorator order: `@mcp.tool(annotations=...)` outermost, `@inject_preferences` inner.
  Four `ToolAnnotations` presets in `server.py`: `READ_ONLY_TOOL_ANNOTATIONS`,
  `WRITE_TOOL_ANNOTATIONS`, `IDEMPOTENT_WRITE_TOOL_ANNOTATIONS`,
  `DESTRUCTIVE_TOOL_ANNOTATIONS`.
- Every tool returns `str` (text or `json.dumps`), validates `output_format` first, and
  reads `_server.DEFAULT_MAIL_ACCOUNT`-style config lazily (never captured at import) so
  tests can monkeypatch.
- Unbounded-scan refusals fire before any AppleScript call, and fire as the JSON error
  envelope even when `output_format="text"` (deliberate format asymmetry, documented in
  `tools/search/emails.py`).
- Mutation tools: ID-first (`message_ids` exact), `dry_run` first-class, `max_*` caps
  enforced in both AppleScript and Python, `AppleScriptTimeout` caught locally, id lists
  capped at `MAX_WHOSE_IDS` (50) with `iter_id_chunks` batching.
- **Calendar-specific trap:** Mail ids are numeric; every Mail id primitive
  (`normalize_message_ids` `.isdigit()`, `build_whose_id_list` numeric predicates) cannot
  be reused verbatim. Calendar UIDs are UUID-like strings and need their own shape
  validation (non-empty, length-capped, no `|||`, no control chars) and their own quoted
  string-equality predicate builder.

### 3.3 Safe modes

`__main__.py` plumbing: `--read-only` sets `server.READ_ONLY = True` and implies
`DRAFT_SAFE = True`, then physically removes `SEND_TOOLS` from the FastMCP registry via
`mcp.remove_tool(name)`. `--draft-safe` sets `DRAFT_SAFE` only; tools stay registered and
enforce gates internally (`tools/compose/helpers.py::_send_blocked` pattern). The CLI
calls tool functions directly without registry removal, so internal guards are mandatory
defense-in-depth, not optional. Env config lives in `server.py` at import time
(`DEFAULT_MAIL_ACCOUNT`, `DEFAULT_MAIL_SIGNATURE`, `USER_EMAIL_PREFERENCES`), read lazily
by tools. The calendar analogues are `DEFAULT_CALENDAR` and the
`CALENDAR_ALLOW_DESTRUCTIVE` unlock (section 6.2).

### 3.4 Tests

- `tests/<area>/` per surface; `tests/calendar/` is the natural sibling.
- Two mock patterns: patch `subprocess.run` with a `side_effect` that inspects
  `kwargs["input"]` (exercises real escaping/timeout/sanitization), or patch the package
  facade attribute (`apple_mail_mcp.tools.<surface>.run_applescript`) when script text
  does not matter. Patch facades, not `core`, unless the test wants every surface.
- `tests/conftest.py` autouse fixture stubs account validation so the suite never touches
  live Mail; a calendar equivalent (stub calendar list) should be autouse too.
- `tools/expected_test_count.txt` = **1053** now; recount with
  `PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests` and bump in the same commit.
- Module line budget: 600 physical lines, warn everywhere, hard fail on baseline
  regression (`tests/fixtures/module_line_budget/baseline.json`, currently empty).
- **Hook caveat:** `.claude/hooks/check_applescript_compiles.py` line 162 hardcodes the
  literal `tell application "Mail"` as its full-script marker, so calendar `*_script`
  builders returning `tell application "Calendar"` would be silently skipped. The hook
  needs a small generalization plus calendar entries in `SAMPLE_KWARGS` before calendar
  script builders land.

### 3.5 Release surfaces

- Six version files bump together (all currently 3.9.3): `pyproject.toml`
  `[project].version`; `plugin/.claude-plugin/plugin.json` `version`;
  `plugin/.codex-plugin/plugin.json` `version`; `.claude-plugin/marketplace.json`
  `plugins[0].version` (not `metadata.version`); `server.json` top-level and
  `packages[0].version`; `apple-mail-mcpb/manifest.json` `version`. `CHANGELOG.md` must
  carry a matching `## {version} - YYYY-MM-DD` heading with nothing left under
  `Unreleased` (enforced by `tools/manifest_checks/version.py`).
- Tool-count ground truth: recursive `^@mcp\.tool` scan of `plugin/apple_mail_mcp/tools/`
  (currently **31**). `ACTIVE_DOC_TOOL_COUNT_REQUIRED` files that must carry the matching
  claim: `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/CLAUDE.md`,
  `plugin/apple_mail_mcp/CLAUDE.md`, `plugin/apple_mail_mcp/tools/CLAUDE.md`,
  `plugin/docs/CLAUDE.md`, `.claude-plugin/CLAUDE.md`, `apple-mail-mcpb/CLAUDE.md`,
  `apple-mail-mcpb/build-mcpb.sh`, `tools/manifest_checks/artifacts.py`. Scan-only:
  `tools/CLAUDE.md`, `docs/CLAUDE-conventions.md`. The regex is blunt (any digit followed
  by "tool(s)"), so watch for incidental matches. `apple-mail-mcpb/manifest.json` must
  also list every tool by name in `tools[]`.
- Skill-count claims ("9 workflow skills" / "nine"): hand-maintained, no automated gate.
  Verified locations: `AGENTS.md:53`, `CLAUDE.md:51`, `README.md:61`,
  `plugin/docs/CLAUDE.md:47`, `plugin/skills/email-management/README.md:29`,
  `docs/AGENT_LIVE_TESTING.md:373`, `.claude-plugin/CLAUDE.md:47`.
- `tools/gates/dev-check.sh` tiers: `default`, `lint` (fatal ruff + ruff format + mypy
  `--strict` on `plugin/apple_mail_mcp/`), `surface`, `manifest`, `live`, `release`
  (lint, rebuild artifacts, layout validators, pytest, test-count gate, wrapper check).
  Any new calendar module must be fully typed from the first commit.
- Skills: `plugin/skills/<name>/SKILL.md` auto-discovered; directory name equals
  frontmatter `name`; canonical shared references live in `plugin/skills/references/`
  and are copied per-skill by `tools/validators/sync_skill_references.py::SYNC_MAP`
  (never hand-copied; `tests/infra/test_packaged_skill_paths.py` enforces
  self-contained skill folders). Skills-only policy: no slash commands.
- `tasks/` layout rules enforced by `tools/validators/validate_tasks_layout.py`; this
  workstream lives under `tasks/active/apple-calendar-tools/`.

---

## 4. Platform APIs

### 4.1 Calendar.app AppleScript dictionary

Apple's archived but authoritative Calendar Scripting Guide documents the object model:
calendars (name, color, writable, description), events (summary, location, start date,
end date, allday event, recurrence, status, url, description, uid), attendees (display
name, email, participation status), and four alarm classes (display alarm, sound alarm,
mail alarm, open file alarm; the last two are legacy).

- Index: https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/index.html
- Creating an event: https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-CreateanEvent.html
- Adding an attendee: https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-AddanAttendeetoanEvent.html

Alarms are settable by making `display alarm` / `sound alarm` sub-objects with a
`trigger interval` (minutes relative to start). Participation status is readable only.
`tell application "Calendar"` auto-launches Calendar.app if not running; the first send
from a host triggers the Automation TCC prompt, and unresolved consent can surface as
`-1743` or as a silent hang (see section 5):
https://discussions.apple.com/thread/254186467

State-of-the-art caveat: Michael Tsai, "The Sad State of Mac Calendar Scripting"
(2024-10), documents that selection-based access is missing and some create/duplicate
paths mutate temporary objects that never persist; the community escape hatch is
EventKit (Shane Stanley's CalendarLib EC):
https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/
Conclusion: AppleScript is fine for simple explicit-property writes and UID-targeted
mutations, fragile for anything selection-based or bulk.

### 4.2 Attendees and invitations: not deliverable by script

AppleScript can create the attendee object, but no invitation is sent and no scriptable
verb triggers the send:

- Apple Developer Forums thread 681057 (attendee added, invite not sent):
  https://developer.apple.com/forums/thread/681057
- MacScripter (no way to script the Send button; the workaround is a hand-built `.ics`
  mailed via Mail scripting):
  https://www.macscripter.net/t/how-to-send-an-ical-events-attendee-their-invitation/48665
- Microsoft Q&A (same finding):
  https://learn.microsoft.com/en-us/answers/questions/4467708/creation-of-an-attendee-for-a-calendar-event-via-a

Community reports add that script-attached attendees may not round-trip to CalDAV/
Exchange servers and can be dropped on the next sync. EventKit cannot attach attendees at
all (section 4.3). Net platform reality: **attendee attachment is possible but delivery
of invitations is not guaranteed by any public API, and on some account types the save
may behave differently than the Calendar.app UI send.** The plan treats attendee writes
as an outward-facing, explicitly confirmed, mode-gated action with documented
non-guaranteed delivery, plus a reliable alternative (draft an `.ics` invitation through
the already-gated Mail compose path).

### 4.3 EventKit (PyObjC or JXA ObjC bridge)

- Bindings: https://pyobjc.readthedocs.io/en/latest/apinotes/EventKit.html ·
  https://pypi.org/project/pyobjc-framework-EventKit/ ·
  https://pyobjc.readthedocs.io/en/latest/install.html ·
  https://pypi.org/project/pyobjc-core/
- Authorization (macOS 14+): `requestFullAccessToEventsWithCompletion:` plus an
  `NSCalendarsFullAccessUsageDescription` string in the responsible bundle's Info.plist.
  The completion handler is async; a short-lived CLI must pump an `NSRunLoop` or block on
  an event until the callback fires.
- Bounded fetch: `predicateForEventsWithStartDate:endDate:calendars:` into
  `eventsMatchingPredicate:` is the fast indexed path and the correct primitive for every
  window read and free-busy computation.
- Writable on `EKEvent`: title, startDate, endDate, isAllDay, location, notes, url,
  **timeZone** (real per-event zone support), availability, **alarms**
  (`EKAlarm alarmWithRelativeOffset:` / `alarmWithAbsoluteDate:`), **recurrenceRules**
  (`EKRecurrenceRule`, effectively one rule; adding replaces), calendar. Save via
  `saveEvent:span:commit:error:`; remove via `removeEvent:span:error:` with `EKSpan`
  (this event vs future occurrences).
  - https://developer.apple.com/library/archive/releasenotes/General/APIDiffsMacOSX10_11/Swift/EventKit.html
  - https://www.createwithswift.com/setting-alarms-for-calendar-events/
  - https://nemecek.be/blog/35/how-to-create-ios-reminders-in-code-with-alarms-or-recurrences
- **Attendees are read-only.** `EKCalendarItem.attendees` cannot be constructed or
  mutated; `EKParticipant` exists to read participant info:
  - https://openradar.appspot.com/15504551
  - https://developer.apple.com/forums/thread/74209
  - https://github.com/AlbertMontserrat/AMGCalendarManager/issues/2
- Calendar CRUD: `calendarForEntityType:eventStore:` + assign a real `EKSource` +
  `saveCalendar:commit:error:`; wrong-source calendars can vanish after quit;
  `allowsContentModifications` flags immutable (subscribed/delegated) calendars:
  - https://developer.apple.com/documentation/eventkit/ekeventstore/savecalendar(_:commit:)
  - https://developer.apple.com/forums/thread/70522
- Free-busy: **no native free-busy query exists.** `EKEvent.availability` is a per-event
  property; free/busy for a window = fetch events with the bounded predicate and fold
  intervals locally: https://developer.apple.com/documentation/eventkit/ekevent/availability
- Dependency weight: pip-clean, universal2 wheels, a few MB total; live install on this
  machine took 1.6s and 43 MB for a whole venv (section 5). Size is not the problem.

### 4.4 The TCC attribution problem (the load-bearing packaging risk)

TCC attributes an EventKit request to the **responsible process**, normally the host app
that launched the server. Claude Desktop and Codex Desktop do not declare
`NSCalendarsFullAccessUsageDescription`, so EventKit calls under them are denied
synchronously with no prompt, and users cannot self-repair (no manual add button):

- anthropics/claude-code#63032 (signed, notarized EventKit MCP denied in 44 ms inside
  Claude Desktop; works under Claude Code launched from a terminal because attribution
  flows to the terminal): https://github.com/anthropics/claude-code/issues/63032
- openai/codex#21228 (same failure inside Codex Desktop; `tccutil reset` does not help):
  https://github.com/openai/codex/issues/21228
- Related: https://github.com/microsoft/vscode/issues/307364

AppleScript Calendar automation rides the separate Apple Events Automation TCC category
(the same grant class this repo's Mail tools already use), so it keeps working on the
`.mcpb` / Claude Desktop surface.

The only route that makes EventKit work inside Claude Desktop today is a signed,
notarized, self-disclaiming helper binary (`responsibility_spawnattrs_setdisclaim`):

- https://www.qt.io/blog/the-curious-case-of-the-responsible-process
- https://mjtsai.com/blog/2025/07/07/the-curious-case-of-the-responsible-process/
- https://steipete.me/posts/2025/applescript-cli-macos-complete-guide
- https://github.com/torarnv/disclaim
- Working example MCP: https://github.com/FradSer/mcp-server-apple-events

That route requires a compiled Swift helper, Developer ID signing, and notarization in
the release pipeline, which conflicts with this repo's pure-Python model. It is a
plausible phase-2 accelerator, not a 3.10.0 dependency.

JXA ObjC bridge (`osascript -l JavaScript`, `ObjC.import('EventKit')`) reaches EventKit
with zero pip dependency but hits the identical Calendars TCC category, so it does not
escape the Desktop denial; it only trades the pip dependency for finicky bridge glue:

- https://www.galvanist.com/posts/2020-03-28-jxa_notes/
- https://scriptingosx.com/2021/11/the-unexpected-return-of-javascript-for-automation/
- Working JXA+EventKit CLI: https://github.com/joargp/accli

### 4.5 RSVP: no public API, on any engine

`EKParticipant.participantStatus` is read-only
(https://developer.apple.com/documentation/eventkit/ekparticipant/participantstatus), the
whole `attendees` array is read-only, and AppleScript exposes `participation status` as
readable with no verb to change your own response or emit the RSVP email. ScriptingBridge
inherits the AppleScript dictionary. The only programmatic RSVP paths are server-side
protocols (CalDAV/Exchange/Graph) or GUI scripting, both out of scope. **Decision input:
ship a documented refusal, not a fragile implementation.**

### 4.6 Performance evidence

Dr. Drang's AppleSloth benchmark, `every event whose start date >= (current date)` on
Calendar.app (https://leancrew.com/all-this/2020/03/applesloth/):

| Runner | Time |
|--------|------|
| Script Editor | ~61 s |
| Script Debugger | ~112 s |
| Terminal (`osascript`) | ~85 s |
| Standalone app | ~85 s |
| Python direct (EventKit-class access) | essentially instantaneous |
| Shortcuts find-events | under 2 s |

Root cause: one Apple Event per property per event, so cost scales with events times
properties, and Calendar.app's `whose` filter evaluates by marshalling candidates rather
than an index push-down, so cost tracks total store size, not window size. Corroboration:

- https://www.macscripter.net/t/faster-way-to-find-a-calendar-event/69257
- https://macscripter.net/viewtopic.php?id=47065
- `rem`/`ical` CLIs: EventKit "same API Calendar.app uses internally," roughly 3000x
  faster than AppleScript; JXA read layer 42 to 60 s dropped to 0.13 s (about 462x):
  https://ical.sidv.dev/ · https://rem.sidv.dev/docs/architecture/

Additional correctness caveat for the AppleScript engine: Calendar.app stores a recurring
series as one event object whose `start date` is the series start, so a
`whose start date >= A and start date < B` window can miss occurrences of series that
started before the window. EventKit's predicate expands occurrences correctly. The plan
addresses this with a bounded recurring-master second pass plus Python-side RRULE
expansion (plan section 3.2).

### 4.7 Other engines considered and rejected

- **icalBuddy**: unmaintained since ~2014, read-only, external binary install
  (https://hasseg.org/icalBuddy/ · https://formulae.brew.sh/formula/ical-buddy).
  Successors are also external binaries: https://github.com/itspriddle/ical-guy ·
  https://github.com/ajrosen/icalPal ·
  https://schappi.com/blog/meet-ekctl-a-command-line-interface-for-managing-calendars-and-reminders-on-maco
- **Shortcuts CLI** (`shortcuts run`): fast reads but requires user-prebuilt named
  Shortcuts and awkward structured IO; no attendees/RSVP anyway. Not a fit for a
  zero-setup MCP.
- **Compiled Swift helper**: highest fidelity and the only EventKit-everywhere path, but
  brings signing plus notarization into the release pipeline. Deferred (tracked risk).

---

## 5. Live probe results (this Mac mini, 2026-07-10)

System: macOS 26.5.2 (build 25F84), Darwin 25.5.0 arm64, Mac16,10 (M4, 10 cores, 16 GB),
host `Caymans-Mac-mini-3.local`. Active unlocked console session, display on, so the
hangs below are not a locked-screen artifact. Timing harness: a scratchpad
`run_timed.py` wrapping `subprocess.run(..., timeout=N)` with monotonic wall-clock, so
elapsed values are internal measurements, not harness cutoffs. All probes were read-only;
no event, calendar, or attendee was created, modified, or deleted.

### 5.1 Measured results

| Path | Call | Result | Wall time |
|------|------|--------|-----------|
| AppleScript, no target app | `return 1+1` (control) | OK | 0.028 s |
| AppleScript to Calendar | `get name of calendars` | **BLOCKED (silent hang)** | 25.005 s timeout, 20.007 s on retry |
| AppleScript to Calendar | bounded -3d/+4d single-calendar event count | **BLOCKED (silent hang)** | 15.006 s timeout |
| AppleScript to Finder (control) | `get name of home` | **BLOCKED (silent hang)** | 15.007 s timeout |
| AppleScript to System Events | GUI window enumeration | **DENIED, fast explicit error** `-25211` | 13.609 s (error, not timeout) |
| EventKit (PyObjC, bare venv python) | `authorizationStatusForEntityType` (sync read) | `notDetermined` | sub-second |
| EventKit (PyObjC, bare venv python) | `requestFullAccessToEventsWithCompletion_` | **PENDING, callback never fired** | 20 s internal cap (21.107 s script total) |
| EventKit (JXA ObjC bridge) | `authorizationStatusForEntityType` (sync read) | `notDetermined` | 0.071 s |

Venv footprint: `python3 -m venv` 1.096 s; `pip install pyobjc-framework-EventKit`
1.607 s (pyobjc-core 12.2.1 + pyobjc-framework-Cocoa 12.2.1 +
pyobjc-framework-EventKit 12.2.1); full venv 43 MB.

### 5.2 Interpretation (TCC buckets, inferred from behavior only; TCC.db never queried)

- **Automation (Apple Events)**: Terminal-to-Calendar and Terminal-to-Finder both hang
  identically for this process ancestry (Terminal.app -> login -> zsh -> claude -> zsh).
  A silent hang, not a fast `-1743`, is the signature of a first-time or unresolved
  Automation prompt with nobody answering it; a recorded "No" would fail fast.
- **Accessibility**: a separate bucket that fails fast with a named error (`-25211`),
  which is a useful operational contrast for timeout policy.
- **Calendars full access (EventKit)**: attaches to the calling executable; both PyObjC
  and JXA independently report `notDetermined`, and the access request never resolves.
  A bare interpreter with no bundle identity and no usage string is a second plausible
  reason the prompt cannot present cleanly, on top of unanswered consent.

### 5.3 What was skipped and what remains unmeasured

The all-calendar fan-out probe and the unbounded-risk characterization were explicitly
skipped once the Automation hang reproduced (same gate, no new information from waiting
out more timeouts). Consequences carried into the plan:

- **No live Calendar.app bounded-read, fan-out, or unbounded-scan timings exist from
  this machine.** All AppleScript performance expectations rest on the community
  benchmarks in section 4.6 and the Mail precedent in this repo.
- Before phase-2 live verification, a human at the console must trigger and answer, once,
  the Automation (host app to Calendar) prompt and, if the EventKit fast path is being
  validated, the Calendars full-access prompt. The live-verification plan (plan section
  9) starts with exactly this and then re-collects the missing timings.
- Practical failure-mode note for the tools: pending Automation consent presents as a
  silent hang, so every calendar osascript call must run under the standard
  `run_applescript` timeout, and the timeout error text should name the Automation pane
  as a likely cause on first use.

---

## 6. Safety design (adopted contract)

Researcher 5's design, adopted with the engine adjustments in section 7. All controls
mirror machinery already shipping in `plugin/apple_mail_mcp/`.

### 6.1 Bounded-read contract

- Every event-returning call requires a window: absolute `start`/`end` ISO 8601 or
  relative `days_back`/`days_ahead`. No window (or a zero-width one, with no bounded
  default) refuses with `UNBOUNDED_CALENDAR_SCAN` before any engine call.
- Window width hard cap: 370 days (`CALENDAR_WINDOW_TOO_WIDE`). Rationale: mail caps at
  365; calendars legitimately query a year ahead (annual planning, two semesters), plus
  two weeks of slack; it is a width cap, not an endpoint cap.
- Return cap 200 events per call with `truncated` + `next_offset` paging; occurrence
  expansion ceiling 750 per call (`CALENDAR_WINDOW_TOO_DENSE`) as the recurring-blowup
  guard. Rationale for 200 vs mail's 50: calendar stores are local and date-indexed
  under EventKit, and a year is roughly 250 to 2000 events for a busy professional;
  50 would force paging on ordinary two-month reads. The AppleScript engine keeps its
  own inner scan caps regardless.
- Availability search capped tighter: 62-day window (`AVAILABILITY_MAX_WINDOW_DAYS`),
  since slot-finding walks the window in slot increments.
- Timezone is a correctness control: accept offset-aware ISO 8601 or naive datetime plus
  IANA `timezone`; when neither is supplied, resolve host-local and echo
  `resolved_timezone`; unknown zones refuse with `INVALID_TIMEZONE`.
- Central caps dict `CALENDAR_BOUNDS` in `constants.py` (values in plan section 4.1);
  one sanctioned window producer (`bounded_calendar_window`) issues window tokens, the
  `bounded_inbox_scan` analogue.

### 6.2 Mode gating

- `--read-only`: all calendar write and destructive tools removed from the registry
  (the `SEND_TOOLS` removal pattern) plus internal `CALENDAR_WRITE_BLOCKED` backstops
  for the CLI path.
- `--draft-safe`: own-calendar create/update/batch create and calendar create/rename stay
  allowed (reversible, touch no third party; the personal-blocking use case). All deletes
  blocked (`CALENDAR_DELETE_BLOCKED`) unless the operator set the environment unlock
  `CALENDAR_ALLOW_DESTRUCTIVE=1` at launch (an agent can never grant itself delete power
  mid-session). Attendee attachment and invitation confirmation blocked
  (`INVITE_SEND_BLOCKED`).
- Default mode: deletes are RESTRICT (dry-run default, caps, confirms); attendee
  attachment requires explicit `send_invitations=True`
  (`INVITE_SEND_REQUIRES_CONFIRM` otherwise), because there is no draft-invite state on
  this platform and attendee saves must be treated as potentially outward-facing.
- RSVP: permanently refused with `CALENDAR_RSVP_UNSUPPORTED` (documented-refusal shim,
  the `full_inbox_export` precedent).
- `delete_calendar` is the strongest gate: dry-run default with cascade count, explicit
  `confirm_delete_calendar=True`, `force_nonempty=True` for non-empty calendars, and
  fuzzy name matching disabled.

### 6.3 Error codes

Calendar additions, all via the existing envelope: `UNBOUNDED_CALENDAR_SCAN`,
`CALENDAR_WINDOW_TOO_WIDE`, `CALENDAR_WINDOW_TOO_DENSE`, `INVALID_EVENT_WINDOW`,
`INVALID_TIMEZONE`, `RECURRING_SPAN_REQUIRED`, `EVENT_NOT_FOUND`, `CALENDAR_NOT_FOUND`,
`AMBIGUOUS_CALENDAR_SELECTOR`, `CALENDAR_READ_ONLY`, `CALENDAR_WRITE_BLOCKED`,
`CALENDAR_DELETE_BLOCKED`, `INVITE_SEND_BLOCKED`, `INVITE_SEND_REQUIRES_CONFIRM`,
`TOO_MANY_ATTENDEES`, `BATCH_TOO_LARGE`, `TOO_MANY_DELETES`,
`CALENDAR_CONFIRMATION_REQUIRED`, `CALENDAR_RSVP_UNSUPPORTED`, `CALENDAR_ACCESS_DENIED`,
`EVENT_CONFLICT`. Reused where semantics match rather than duplicated
(`TARGET_SELECTOR_DEPRECATED` stays reserved; calendar mutations simply never expose a
fuzzy selector). Full trigger/remediation table: plan section 4.3.

### 6.4 Conflict detection

Check by default, in the same round trip as the write, non-blocking:
`on_conflict="warn"` (default: create and report `conflicts[]`), `"block"` (refuse with
`EVENT_CONFLICT`), `"allow"` (skip the check). The check is a bounded query over the new
event's own duration only, so it is cheap; baking it into the write fails safe where a
mandatory separate `check_availability` call would be forgettable. The standalone
availability tool still exists for slot-finding.

### 6.5 Injection safety and identifier doctrine

- AppleScript path: every user string through `core.escape_applescript()`; datetimes
  parsed to validated integers in Python and interpolated as integers (never
  `date "..."` strings); RRULE validated against an allowlist grammar
  (`FREQ`, `INTERVAL`, `COUNT`, `UNTIL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`) before any
  interpolation; row emission sanitized with `sanitize_pipe_delimited_field` plus
  Python-side UID shape validation (the mail defense-in-depth, adapted for string UIDs).
- EventKit path (when active): native typed setters, no script strings; date queries only
  via the typed predicate constructor, never `predicateWithFormat:`; recurrence only via
  typed `EKRecurrenceRule` constructors.
- Tool output is structured JSON per event, never delimited rows, which removes the
  field-shift corruption class outright (requirement 9 doubles as an injection control).
- ID-first mutations: update/delete accept exact `event_id`/`event_ids` only; discovery
  tools return `event_id` on every row; recurring ids additionally require an explicit
  `span` (no default), so a series can never be wiped by an unqualified delete.
  Fuzzy calendar-name resolution serves reads and create targets only, never destructive
  targets, and never silently picks between multiple matches
  (`AMBIGUOUS_CALENDAR_SELECTOR`).

### 6.6 TCC error UX

Detect EventKit `Denied`/`Restricted` (and the macOS 14+ full-vs-write-only split) and
AppleScript `-1743`/timeout signatures; return `CALENDAR_ACCESS_DENIED` with remediation
naming the exact pane (System Settings > Privacy & Security > Calendars, or
> Automation for the AppleScript path), the detected access level, and the note that
write-only access cannot read events. The live probe adds one nuance: pending Automation
consent hangs silently rather than erroring, so calendar timeout errors must mention the
Automation pane on first-use timeouts.

---

## 7. Cross-report conflicts and their resolutions

1. **Engine primacy.** The safety report assumed EventKit as the primary engine; the
   platform report recommends AppleScript as the portable default with EventKit as an
   optional fast path; the live probe could not validate either end to end on this
   machine. Resolution: hybrid with AppleScript as the guaranteed engine for all
   operations on all surfaces, plus an opt-in EventKit read fast path that activates only
   when the dependency is installed and full access is already granted. The safety
   contract is engine-neutral by design, so this substitution changes no caps, codes, or
   gates. (Plan section 2.)
2. **Attendee save semantics.** The safety report reasoned from the platform model that
   saving an event with attendees dispatches invitations; the platform report's cited
   evidence is that script-attached attendees do not send invitations and may not
   round-trip on sync. Resolution: treat attendee attachment as both outward-facing
   (gate it like a send: explicit confirm plus draft-safe block) and unreliable
   (document non-guaranteed delivery in the tool response and teach the `.ics`-via-Mail
   alternative in the meeting-scheduler skill). Both reports agree on the gating shape.
3. **osascript lock sharing.** The codebase map flagged whether Calendar should share
   Mail's single-flight lock or get its own. Resolution: share `core.run_applescript`
   and its lock in 3.10.0 (simplest, preserves the one-osascript-at-a-time doctrine and
   the repo's no-parallel-tool-calls guidance); revisit only if live verification shows
   Calendar and Mail workloads contending in practice.
4. **Recurring-window semantics.** The safety report's occurrence ceiling assumed
   engine-side occurrence expansion; the platform evidence shows AppleScript does not
   expand occurrences in `whose` windows. Resolution: the AppleScript engine adds a
   bounded recurring-master pass with Python-side RRULE expansion limited to the same
   allowlist grammar as the write path; unsupported rules return the master flagged
   unexpanded. (Plan section 3.2.)

---

## 8. Capability matrix

Engines: **AS** = Calendar.app AppleScript via `core.run_applescript` (Apple Events
Automation grant; works on all four install surfaces including `.mcpb`).
**EK** = EventKit via PyObjC (fast; requires Calendars full access attributed to the
host; denied with no prompt inside Claude Desktop / Codex Desktop today).
**PY** = computed in Python on top of either engine's fetch. **none** = no public API.

| Capability | AS | EK | Ship verdict (engine) | Confidence | Key citations |
|---|---|---|---|---|---|
| List calendars (+writable flag) | Yes | Yes | AS (EK when active) | High | Calendar Scripting Guide; reference skill `cal-list.sh`; probe section 5 |
| Bounded event reads (window) | Yes but slow, no occurrence expansion | Yes, fast, expands occurrences | Hybrid: AS guaranteed + EK fast path | High | https://leancrew.com/all-this/2020/03/applesloth/ · https://rem.sidv.dev/docs/architecture/ · https://github.com/anthropics/claude-code/issues/63032 |
| Search events (text match) | Fetch via AS, match in PY | Fetch via EK, match in PY | PY over hybrid fetch | High | Reference skill `cal-search.sh` teardown (avoid in-script case folding) |
| Get event by id | Yes (UID predicate; must be window-bounded) | Yes (identifier lookup) | Hybrid, window-bounded on AS | High | Reference skill `cal-read.sh` teardown; https://www.macscripter.net/t/faster-way-to-find-a-calendar-event/69257 |
| Create event (title/times/location/notes/url) | Yes | Yes | AS (all writes in 3.10.0) | High | https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-CreateanEvent.html |
| Alarms ("remind me 10 min before") | Yes (`display alarm`, `sound alarm`, trigger interval) | Yes (`EKAlarm`) | AS | High | Calendar Scripting Guide; https://www.createwithswift.com/setting-alarms-for-calendar-events/ |
| Recurrence (RRULE) | Yes (`recurrence` string) | Yes (`EKRecurrenceRule`, single rule) | AS with allowlisted RRULE | Medium | Reference skill; https://nemecek.be/blog/35/how-to-create-ios-reminders-in-code-with-alarms-or-recurrences |
| Timezone-correct create/read | Instant-correct via PY zone math; stored zone stays host-local | Full (`EKEvent.timeZone`) | PY zone math over AS now; EK write engine later for stored zones | Medium | https://developer.apple.com/library/archive/releasenotes/General/APIDiffsMacOSX10_11/Swift/EventKit.html |
| Update event (PATCH by id) | Yes (per-field set) | Yes | AS | Medium | https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/ (avoid selection-based paths) |
| Delete event by id | Yes (whole series only for recurring) | Yes (`EKSpan` this/future) | AS; span granularity documented as engine-limited | High | Reference skill `SKILL.md` caveat; https://developer.apple.com/documentation/eventkit/ekeventstore |
| Batch create | Loop of AS creates (capped) | Batch `commit:NO` saves | AS loop, cap 25 | High | EventKit save semantics link above |
| Bulk delete by exact ids | Yes (capped, chunked UID predicates) | Yes | AS, ids only, dry-run default | High | Mail `manage_trash`/`move_email` precedent in this repo |
| Free-busy / find-slot | Fetch + fold in PY (62-day cap) | Fetch + fold in PY, fast | PY over hybrid fetch | Medium | https://developer.apple.com/documentation/eventkit/ekevent/availability (no native free-busy anywhere) |
| Conflict detection on create | Bounded duration-window query | Same, faster | PY over hybrid fetch, in-line with write | High | Safety report section 4 |
| Calendar create/rename | Yes | Yes (EKSource care needed) | AS | Medium | https://developer.apple.com/documentation/eventkit/ekeventstore/savecalendar(_:commit:) · https://developer.apple.com/forums/thread/70522 |
| Calendar delete (cascade) | Yes | Yes | AS, triple-gated | Medium | Safety report section 5 |
| Default calendar + fuzzy name match | Name list + PY fuzzy match; default via env | `defaultCalendarForNewEvents` | PY fuzzy over either; `DEFAULT_CALENDAR` env first | High | Codebase map (env-var pattern); probe section 6 |
| Attendees attached to event | Object created; server round-trip unreliable | **No (read-only)** | AS, gated as outward-facing, delivery documented as not guaranteed | Low | https://developer.apple.com/forums/thread/681057 · https://openradar.appspot.com/15504551 |
| Send invitations that reach people | **No** | **No** | Not shipped; `.ics` via gated Mail compose is the taught alternative | High | https://www.macscripter.net/t/how-to-send-an-ical-events-attendee-their-invitation/48665 · https://learn.microsoft.com/en-us/answers/questions/4467708/creation-of-an-attendee-for-a-calendar-event-via-a |
| RSVP accept/decline/tentative | **No** | **No (participantStatus read-only)** | Documented refusal shim | High | https://developer.apple.com/documentation/eventkit/ekparticipant/participantstatus · https://developer.apple.com/forums/thread/74209 |
| Structured JSON everywhere | Via PY (sanitized row protocol internal only) | Native objects | PY | High | Codebase map section 1.6; safety report section 6 |
| Works inside Claude Desktop `.mcpb` | **Yes** (Automation grant) | **No** (TCC attribution) | AS mandatory baseline | High | https://github.com/anthropics/claude-code/issues/63032 · https://github.com/openai/codex/issues/21228 |

---

## 9. Full source inventory

### Repo files (verified on this branch)

`plugin/apple_mail_mcp/core/applescript.py`, `core/escaping.py`, `core/normalization.py`,
`core/validation.py`, `core/script_fragments.py`, `backend/base.py`, `bounded_scan.py`,
`constants.py`, `server.py`, `__main__.py`, `tools/search/emails.py`,
`tools/search/records.py`, `tools/manage/move.py`, `tools/manage/helpers.py`,
`tools/inbox/parsing.py`, `cli/parser.py`, `cli/commands.py`, `tests/conftest.py`,
`tools/expected_test_count.txt` (1053), `tests/fixtures/module_line_budget/baseline.json`,
`.claude/hooks/check_applescript_compiles.py`, `tools/gates/dev-check.sh`,
`tools/manifest_checks/tool_count.py`, `tools/manifest_checks/version.py`,
`tools/validators/sync_skill_references.py`, `docs/CLAUDE-conventions.md`,
`tasks/CLAUDE.md`, plus the six version-bearing manifests.

### External URLs (complete list)

Reference skill: the twelve URLs in section 2.1.

Apple documentation:
- https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/index.html
- https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-CreateanEvent.html
- https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-AddanAttendeetoanEvent.html
- https://developer.apple.com/documentation/eventkit/ekparticipant/participantstatus
- https://developer.apple.com/documentation/eventkit/ekevent/availability
- https://developer.apple.com/documentation/eventkit/ekeventstore/savecalendar(_:commit:)
- https://developer.apple.com/documentation/eventkit/ekeventstore
- https://developer.apple.com/library/archive/releasenotes/General/APIDiffsMacOSX10_11/Swift/EventKit.html
- https://developer.apple.com/forums/thread/681057
- https://developer.apple.com/forums/thread/74209
- https://developer.apple.com/forums/thread/70522

PyObjC:
- https://pyobjc.readthedocs.io/en/latest/apinotes/EventKit.html
- https://pyobjc.readthedocs.io/en/latest/install.html
- https://pypi.org/project/pyobjc-framework-EventKit/
- https://pypi.org/project/pyobjc-core/

Performance and community:
- https://leancrew.com/all-this/2020/03/applesloth/
- https://www.macscripter.net/t/faster-way-to-find-a-calendar-event/69257
- https://macscripter.net/viewtopic.php?id=47065
- https://www.macscripter.net/t/how-to-send-an-ical-events-attendee-their-invitation/48665
- https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/
- https://learn.microsoft.com/en-us/answers/questions/4467708/creation-of-an-attendee-for-a-calendar-event-via-a
- https://discussions.apple.com/thread/254186467
- https://openradar.appspot.com/15504551
- https://github.com/AlbertMontserrat/AMGCalendarManager/issues/2
- https://ical.sidv.dev/
- https://rem.sidv.dev/docs/architecture/
- https://nemecek.be/blog/35/how-to-create-ios-reminders-in-code-with-alarms-or-recurrences
- https://www.createwithswift.com/setting-alarms-for-calendar-events/

TCC / responsible process / packaging:
- https://github.com/anthropics/claude-code/issues/63032
- https://github.com/openai/codex/issues/21228
- https://github.com/microsoft/vscode/issues/307364
- https://www.qt.io/blog/the-curious-case-of-the-responsible-process
- https://mjtsai.com/blog/2025/07/07/the-curious-case-of-the-responsible-process/
- https://steipete.me/posts/2025/applescript-cli-macos-complete-guide
- https://github.com/torarnv/disclaim
- https://github.com/FradSer/mcp-server-apple-events

JXA and alternative engines:
- https://www.galvanist.com/posts/2020-03-28-jxa_notes/
- https://scriptingosx.com/2021/11/the-unexpected-return-of-javascript-for-automation/
- https://github.com/joargp/accli
- https://hasseg.org/icalBuddy/
- https://formulae.brew.sh/formula/ical-buddy
- https://github.com/itspriddle/ical-guy
- https://github.com/ajrosen/icalPal
- https://schappi.com/blog/meet-ekctl-a-command-line-interface-for-managing-calendars-and-reminders-on-maco
