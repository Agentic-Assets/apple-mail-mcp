# Phase 1 report: reference skill teardown (sundial-org/awesome-openclaw-skills, apple-calendar)

Researcher 1 of 5. Scope: fetch and fully document the `apple-calendar` skill from
`sundial-org/awesome-openclaw-skills`, cross-check it against the product owner's target
capability list for apple-mail-mcp's new Apple Calendar tools, and produce a borrow-vs-avoid
list for the implementation phase.

## Fetch method and exact URLs

Fetched with `curl -s` against the GitHub Contents API (directory listings) and
`raw.githubusercontent.com` (file bodies). No WebFetch fallback was needed; every `curl`
call returned a 200 with the expected payload.

Directory listings (GitHub Contents API):
- `https://api.github.com/repos/sundial-org/awesome-openclaw-skills/contents/skills/apple-calendar`
- `https://api.github.com/repos/sundial-org/awesome-openclaw-skills/contents/skills/apple-calendar/scripts`

File bodies (raw, `main` branch):
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/SKILL.md`
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-create.sh`
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-delete.sh`
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-events.sh`
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-list.sh`
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-read.sh`
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-search.sh`
- `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-update.sh`

Human-viewable equivalents (same content, GitHub blob/tree UI):
- `https://github.com/sundial-org/awesome-openclaw-skills/tree/main/skills/apple-calendar`
- `https://github.com/sundial-org/awesome-openclaw-skills/tree/main/skills/apple-calendar/scripts`

Total: 1 `SKILL.md` (45 lines) plus 7 shell scripts (`cal-create.sh` 105 lines,
`cal-delete.sh` 50 lines, `cal-events.sh` 66 lines, `cal-list.sh` 22 lines,
`cal-read.sh` 69 lines, `cal-search.sh` 100 lines, `cal-update.sh` 148 lines).
No files beyond `SKILL.md` and `scripts/` exist in the skill directory: there is no
`references/`, no test suite, no JXA, no Swift/EventKit binary, no icalBuddy dependency.

## Top-line finding

The reference skill is a thin, uniformly AppleScript-via-`osascript` wrapper around
Calendar.app's classic scripting dictionary. It covers the stated baseline (list
calendars, list/today/upcoming events, create, update, delete, search) completely, but
covers **none** of the ten explicitly requested gap capabilities and **none** of the
safety requirement. It also carries two performance anti-patterns that map directly onto
the class of bug this repo fixed in Mail at v3.9.3 (unbounded `whose`-clause scans), plus
several output/consistency defects (mismatched pipe-delimited schemas across scripts,
unescaped delimiter characters, inconsistent CLI conventions between positional and flag
arguments). Full detail below.

---

## File-by-file breakdown

### `SKILL.md`

**Purpose.** Top-level skill manifest and command reference table. Declares
`name: apple-calendar`, a one-line description, `metadata: {"clawdbot":{"emoji":"📅","os":["darwin"]}}`
(macOS-only gate for the host skill runner), then a Markdown body with a command table,
date-format spec, RRULE examples, output-format notes, and three caveats.

**CLI interface.** Not itself a CLI; it is documentation that maps seven commands to the
seven scripts in `scripts/`, each shown with its positional usage string.

**Automation engine.** N/A (documentation only), but it names the engine used by every
script: "Interact with Calendar.app via AppleScript."

**Date handling documented.** Two formats only: `YYYY-MM-DD HH:MM` for timed events and
`YYYY-MM-DD` for all-day events. No timezone syntax is documented anywhere in the file.

**Recurrence documented.** Plain iCalendar RRULE strings, with three worked examples
(`FREQ=DAILY;COUNT=10`, `FREQ=WEEKLY;BYDAY=MO,WE,FR`, `FREQ=MONTHLY;BYMONTHDAY=15`).

**Output format documented.** States events/search output is
`UID | Summary | Start | End | AllDay | Location | Calendar` and that read output is
"Full details with description, URL, recurrence." As detailed below, this documented
schema does not actually match `cal-search.sh`'s real output (see that script's
write-up), which is itself a defect worth noting: the docs and the code disagree.

**Notes/caveats section.** Three bullets: read-only calendars (Birthdays, Holidays)
cannot be modified; calendar names are case-sensitive; deleting a recurring event
removes the entire series (no single-instance delete). All three are accurate to the
script behavior and worth carrying forward as documented constraints in the new tools.

**Error handling / performance.** N/A, documentation file only.

---

### `scripts/cal-list.sh` (22 lines)

**Purpose.** List every calendar on the system with a writable/read-only flag.

**CLI interface.** `cal-list.sh`, no arguments.

**Automation engine.** AppleScript via `osascript <<'EOF' ... EOF` (bare heredoc, no
`argv`, since there is nothing to parameterize).

**AppleScript technique worth reusing.** Bulk property fetch instead of per-object
loops: `set calNames to name of every calendar` and
`set calWritable to writable of every calendar` pull both lists in two Apple Events
round trips total, then a single `repeat with i from 1 to count of calNames` zips them
by index. This avoids the classic anti-pattern of `repeat with cal in calendars` plus a
`name of cal` / `writable of cal` call inside the loop (one extra Apple Event per
calendar per property). Good template for any "list N things and one or two scalar
properties of each" tool.

**Output format.** Pipe-delimited text, one calendar per line:
`<calendar name> | writable` or `<calendar name> | read-only`.

**Date handling.** None; calendars have no date.

**Error handling.** None needed; enumerating calendars cannot fail per-item the way a
`whose` query can.

**Performance.** Bounded and cheap by construction: the number of calendars on a real
system is small (single digits to low tens), never scales with event count. No
anti-pattern here.

---

### `scripts/cal-events.sh` (66 lines)

**Purpose.** List events starting on a given day or across the next N days, optionally
scoped to one calendar. Powers both "today's events" (default) and "upcoming events."

**CLI interface.** `cal-events.sh [days_ahead] [calendar_name]`.
`days_ahead` defaults to `0` (today only); `calendar_name` defaults to empty (all
calendars). Positional, no flags.

**Automation engine.** AppleScript via `osascript - "$DAYS_AHEAD" "$CALENDAR_NAME" <<'EOF' ... on run argv ... end run EOF`. This is the "pass values as `argv` items, not
string-interpolated into the script body" pattern: `$DAYS_AHEAD`/`$CALENDAR_NAME` are
bash variables handed to `osascript` as separate positional arguments after the `-`,
picked up inside AppleScript via `item 1 of argv` / `item 2 of argv`. This sidesteps
AppleScript string-escaping/injection bugs that string-interpolating user input into the
heredoc body would create, and it matches this repo's own documented convention
(`core.run_applescript()`: "stdin osascript, escaped user input, JSON-safe output").

**AppleScript technique worth reusing.** Date-window construction:
`set today to current date` then `set startOfDay to today - (time of today)` (subtracts
the time-of-day component to floor to local midnight), then
`set endDate to startOfDay + ((daysAhead + 1) * 24 * 60 * 60)`. The window is then
applied as a native `whose` predicate:
`every event of cal whose start date ≥ startOfDay and start date < endDate`. This is the
correct general shape for a bounded date-window query and should be the starting point
for any new bounded "list events in window W" tool, provided it is paired with the
caps discussed under Performance below.

**Output format.** Pipe-delimited, 7 fields per line:
`uid | summary | start | end | allday | location | calendar`. Dates are rendered via
AppleScript's default `as string` coercion (locale-dependent, human-readable, not
ISO-8601, not machine-parseable without locale assumptions).

**Date handling.** Local system timezone only; `current date` is timezone-naive from
the caller's point of view (it always reflects the Mac's current timezone, with no
parameter to override it). All-day events and timed events are not distinguished in the
query, only in the returned `allday` field.

**Error handling.** A named calendar that does not exist is caught with a `try`/`on
error` around the calendar lookup and returns a plain-text `"Error: Calendar '...' not
found"` string on the same output channel as success data, not a distinct error
type/exit code. A `try` around each calendar's `whose` query silently swallows any
per-calendar failure (empty `repeat` body on error) rather than surfacing it, so a
transient failure on one calendar in a multi-calendar scan is invisible to the caller.

**Performance.** Only partially bounded. The date window itself is real, but: (1) if no
`calendar_name` is given, it iterates **every** calendar on the system (including
subscribed/shared/read-only calendars such as Birthdays and Holidays) and issues one
`whose`-filtered Apple Event query per calendar; (2) there is no cap on `days_ahead`
(the caller can pass 365 or larger) and no cap on the number of events returned, so a
wide window against a dense calendar can return an arbitrarily large blob; (3)
Calendar.app's AppleScript `whose` clause on `events` is documented (widely, in
Apple-scripting community bug reports and Stack Overflow threads) to evaluate the
predicate by marshalling each candidate event across the Apple Events boundary rather
than pushing the filter down to an index, so query cost scales with total event count in
the calendar, not with the size of the matching window; a calendar with years of dense
history can make even a "just today" query slow. No client-side timeout wraps the
`osascript` call anywhere in this script.

---

### `scripts/cal-list.sh`, `cal-events.sh` covered above. Next: `scripts/cal-read.sh` (69 lines)

**Purpose.** Fetch full detail for one event by UID (summary, start, end, all-day flag,
location, description, URL, recurrence rule), optionally scoped to one calendar.

**CLI interface.** `cal-read.sh <event-uid> [calendar_name]`. Required UID, optional
calendar scope; exits 1 with a usage message if UID is missing.

**Automation engine.** AppleScript via the same `osascript - "$UID" "$CAL" <<'EOF' ...
argv ... EOF` pattern as `cal-events.sh`.

**AppleScript technique worth reusing.** Consistent "missing value" normalization before
building the text output: `if eventLoc is missing value then set eventLoc to ""` is
repeated for `location`, `description`, `URL`, `recurrence`. This guards against
AppleScript's `missing value` propagating into a string concatenation and throwing, and
is a pattern worth keeping (translated to Python `None`-coalescing) when mapping
Calendar.app's optional properties into JSON.

**Output format.** Not pipe-delimited like the other list-style scripts; instead a
multi-line `Key: Value` block (`UID:`, `Calendar:`, `Summary:`, `Start:`, `End:`,
`All Day:`, `Location:`, `Description:`, `URL:`, `Recurrence:`). This is a third distinct
output shape in the skill (alongside the 7-field and 6-field pipe formats used
elsewhere), underscoring the "no consistent structured schema" problem called out under
target capability 9 below.

**Date handling.** Same as `cal-events.sh`: local timezone only, `as string` coercion,
no ISO-8601, no timezone parameter.

**Error handling.** Same shape as `cal-events.sh`: calendar-not-found and
event-not-found both return a plain `"Error: ..."` string, indistinguishable on the
wire from a legitimate value that happens to start with the word "Error".

**Performance (the most serious anti-pattern in the whole skill).** This script has
**no date bound at all**. The lookup predicate is
`every event of cal whose uid is eventUID`, evaluated against the calendar's entire
event history with no window restriction, and if `calendar_name` is omitted it repeats
this fully-unbounded scan against **every calendar on the system in sequence** until a
match is found. For any calendar with years of accumulated events (the normal case for
a real iCloud calendar), this is exactly the shape of query that produces the
Calendar.app analogue of the Mail 98% CPU spin this repo fixed in v3.9.3: an
Apple-Events predicate evaluated by full linear scan, with no upper bound on how much
history it walks, no per-call timeout, and no result cap.

---

### `scripts/cal-search.sh` (100 lines)

**Purpose.** Case-insensitive substring search across event summary, location, and
description, scoped by a date window (default 30 days ahead) and optionally by
calendar.

**CLI interface.** `cal-search.sh <query> [days_ahead=30] [calendar_name]`. Required
query, optional window (defaults to 30), optional calendar scope.

**Automation engine.** AppleScript via the same `osascript - "$QUERY" "$DAYS" "$CAL" <<'EOF' ... argv ... EOF` pattern.

**AppleScript technique worth reusing (with a caveat).** A hand-rolled
`toLowerCase(theString)` handler that walks the string one character at a time,
looking up each character's index in a literal `"ABCDEFGHIJKLMNOPQRSTUVWXYZ"` constant
via `offset of c in uppercaseChars` and substituting the matching character from a
parallel lowercase constant, falling through unchanged for non-letters. This is a real,
working way to get case-insensitive `contains` semantics in pure AppleScript when there
is no built-in case-insensitive string operator exposed to Calendar.app's dictionary
(AppleScript does support `considering case`/`ignoring case` blocks as a lighter-weight
built-in alternative worth evaluating instead of hand-rolling this from scratch). Worth
noting as a technique that exists and works, but see the Performance note below for why
it should not be ported as-is.

**Output format.** Pipe-delimited, but only **6 fields**:
`uid | summary | start | allday | location | calendar`: it drops the `end` field that
`cal-events.sh` includes, so the two "list-shaped" scripts in the same skill do not
share a schema and a caller cannot reuse one parser for both. This also does not match
what `SKILL.md` documents as "the" events/search output format (`SKILL.md` shows 7
fields including `End`).

**Date handling.** Same local-timezone-only, `as string` coercion, no ISO-8601, no
timezone parameter, as the other scripts.

**Error handling.** Same "Error: ..." plain-string pattern as the others; empty result
set returns `"No events found matching: <query>"` rather than an empty structured
result.

**Performance.** Inherits every issue from `cal-events.sh` (all-calendars fan-out when
unscoped, unbounded `days_ahead`, unbounded result count, no timeout) and adds cost on
top: for every candidate event already inside the date window, it runs the
character-by-character `toLowerCase` handler up to three times (summary, location,
description), each O(string length) with an `offset of c in uppercaseChars` lookup per
character. This is meaningfully slower than doing the same case-fold/substring match in
Python after pulling the raw fields out via a single bulk property fetch, and is a
concrete example of "logic that belongs in the host process, not inside the
AppleScript payload."

---

### `scripts/cal-create.sh` (105 lines)

**Purpose.** Create a new event (timed or all-day, with optional location, description,
and RRULE recurrence) on a named calendar.

**CLI interface.** `cal-create.sh <calendar> <summary> <start_date> <end_date> [location] [description] [allday] [recurrence]`, eight positional arguments, first four
required, last four optional and empty-string/`"false"` by default. Exits 1 with a usage
message if any of the first four are missing.

**Automation engine.** AppleScript via
`osascript - "$CAL" "$SUMMARY" "$START" "$END" "$LOC" "$DESC" "$ALLDAY" "$RECUR" <<'EOF' ... argv ... EOF`.

**AppleScript techniques worth reusing.**
- A `splitString(theString, theDelimiter)` handler built on
  `AppleScript's text item delimiters` (save old delimiter, set new one, split via
  `every text item of theString`, restore old delimiter): the standard, correct way to
  tokenize strings in AppleScript, reusable verbatim for any future date/RRULE/CSV-ish
  parsing needed inside a script payload.
- A `parseDate(dateStr)` handler that starts from `current date` (which carries the
  system's current timezone implicitly) and overwrites its `year`, `month`, `day`,
  `hours`, `minutes`, `seconds` properties individually from the split string, rather
  than trying to coerce a string directly with `date "..."` (which is locale-dependent
  and unreliable across different macOS System Settings > Language & Region
  configurations). This field-by-field mutation approach is the more robust of the two
  known AppleScript date-construction techniques and is worth keeping as the base for a
  timezone-aware version (see the Date handling gap below for what it is missing).
- Safety check before write: `if not (writable of cal) then return "Error: ... is
  read-only"`, applied before attempting `make new event`. This directly implements the
  "read-only calendars (Birthdays, Holidays) can't be modified" note from `SKILL.md` and
  is the right place to also fold in this repo's own safe-mode gating later.
- Property-record construction for creation:
  `set eventProps to {summary:eventSummary, start date:startDate, end date:endDate}`,
  conditionally extended with `allday event:true`, then a single
  `make new event at end of events of cal with properties eventProps` call, with
  `location`, `description`, and `recurrence` set as separate follow-up statements only
  when non-empty. This "build a properties record, one creation call, then a few
  conditional post-set statements" shape is a clean, reusable template for a Python-side
  event-creation tool.

**Output format.** Single line: `"Created event: " & (uid of newEvent)` on success, or a
plain `"Error: ..."` string on failure (calendar not found, calendar read-only).

**Date handling.** Local system timezone implicitly, via `current date`; no timezone
parameter accepted anywhere in the CLI or the AppleScript; no way to say "create this
event as 2pm Central regardless of the Mac's current timezone."

**Error handling.** Two explicit checks (calendar-not-found via `try`/`on error`,
calendar-read-only via `writable of cal`); anything else (malformed date string, for
example) is left to throw an uncaught AppleScript runtime error, which `osascript` will
print to stderr and exit non-zero on, rather than a handled, structured error.

**Performance.** Single-item write, O(1) beyond the calendar name lookup; no fan-out
concern. Notably: **zero conflict/overlap detection**, nothing reads existing events
on the target calendar before inserting the new one, so double-booking is silent (this
is target capability 10, entirely unaddressed).

---

### `scripts/cal-update.sh` (148 lines)

**Purpose.** Mutate one or more fields (summary, start, end, location, description,
all-day flag, recurrence) of an existing event located by UID, optionally re-scoping the
search to one calendar.

**CLI interface.** `cal-update.sh <event-uid> [--calendar <name>] [--summary <text>] [--start <date>] [--end <date>] [--location <text>] [--description <text>] [--allday <true/false>] [--recurrence <rrule>]`. This is the **only** script in the skill that
uses a `case`-based `while [[ $# -gt 0 ]]` flag parser instead of pure positional
arguments: `cal-create.sh` (the closest sibling in shape) is entirely positional. That
inconsistency (positional vs. flag CLI conventions between sibling scripts in the same
skill) is itself worth flagging: a caller cannot infer one script's calling convention
from another's, and any new tool surface should standardize on one calling shape (in
this repo's case, structured JSON/kwargs via the MCP tool schema, not a shell CLI at
all).

**Automation engine.** AppleScript via
`osascript - "$UID" "$CAL" "$SUMMARY" "$START" "$END" "$LOC" "$DESC" "$ALLDAY" "$RECUR" <<'EOF' ... argv ... EOF`. Reuses the same `splitString`/`parseDate` handlers verbatim
from `cal-create.sh` (copy-pasted between the two files rather than shared), with one
addition: `parseDate` here starts with `if dateStr is "" then return missing value` so
an omitted `--start`/`--end` flag is a true no-op rather than parsing an empty string.

**AppleScript technique worth reusing.** Per-field conditional mutation, only touching
properties the caller actually passed:
```
if newSummary is not "" then set summary of e to newSummary
if newStartStr is not "" then set start date of e to my parseDate(newStartStr)
...
if newAllDay is "true" then set allday event of e to true
else if newAllDay is "false" then set allday event of e to false
```
This "empty string means leave alone, `true`/`false` string means an explicit tri-state
boolean" convention is a reasonable pattern for a PATCH-style update call and is worth
carrying forward (translated to `Optional[...] = None` semantics in Python, which is
cleaner than sentinel empty strings).

**Output format.** Single line: `"Updated event: " & eventUID` on success, or a plain
`"Error: ..."` string on failure.

**Date handling.** Identical limitation to `cal-create.sh`: local timezone only, no
override parameter.

**Error handling.** Same shape as `cal-create.sh`: calendar-not-found and
calendar-read-only are explicit `try`/`writable` checks; event-not-found after
exhausting all searched calendars returns `"Error: Event with UID '...' not found"`.

**Performance.** Shares `cal-read.sh`'s and `cal-delete.sh`'s worst anti-pattern: the
lookup that finds the event to update is
`every event of cal whose uid is eventUID`, with **no date bound**, iterated across
**every calendar on the system** whenever `--calendar` is omitted. The mutation itself
is O(1) once the event is found, but the find step carries the same unbounded-scan risk
already described under `cal-read.sh`.

---

### `scripts/cal-delete.sh` (50 lines)

**Purpose.** Delete an event by UID, optionally scoped to one calendar.

**CLI interface.** `cal-delete.sh <event-uid> [calendar_name]`. Required UID, optional
calendar scope; exits 1 with a usage message if UID is missing.

**Automation engine.** AppleScript via the same `osascript - "$UID" "$CAL" <<'EOF' ... argv ... EOF` pattern as `cal-read.sh`.

**AppleScript technique worth reusing.** The same `writable of cal` guard used in
`cal-create.sh`/`cal-update.sh`, applied here before the `delete e` call, and the event's
`summary` is captured into a local variable **before** deletion so the confirmation
message can still name the deleted event afterward (`delete e` invalidates further
property reads on `e`).

**Output format.** Single line: `"Deleted event: " & eventName & " (" & eventUID & ")"`
on success, or a plain `"Error: ..."` string on failure.

**Date handling.** N/A (no date fields involved in deletion itself).

**Error handling.** Same shape as the other UID-lookup scripts: calendar-not-found,
event-not-found, and read-only-calendar are the three explicit cases; anything else
throws uncaught.

**Performance (second-worst anti-pattern in the skill, and the most consequential).**
Identical unbounded `every event of cal whose uid is eventUID` scan as `cal-read.sh` and
`cal-update.sh` (no date window, all-calendars fan-out when unscoped), but here it backs
a **destructive** action with **zero safety gate of any kind**: no dry-run flag, no
confirmation prompt, no environment-variable or config opt-in check, no distinction
between "delete a single instance" and "delete an entire recurring series" (the
`SKILL.md` notes bluntly that deleting a recurring event removes the whole series, and
the script does nothing to warn about or prevent that at call time). This is the
single largest safety gap in the whole reference skill relative to the product owner's
explicit safety requirement.

---

## Cross-check against target capabilities

Baseline (per the product owner: "everything the awesome-openclaw-skills apple-calendar
skill does"):

| Baseline capability | Status | Script |
|---|---|---|
| List calendars | Covered | `cal-list.sh` |
| List/today/upcoming events | Covered | `cal-events.sh` |
| Create event | Covered | `cal-create.sh` |
| Update event | Covered | `cal-update.sh` |
| Delete event | Covered | `cal-delete.sh` |
| Search events | Covered | `cal-search.sh` |

All six baseline items are present and functional. None of them are safe or fully
structured by this repo's standards (see anti-patterns below), but the baseline
feature surface is real.

Explicit gap list (from the product owner):

| # | Target capability | Status | Evidence |
|---|---|---|---|
| 1 | Attendee/invitee support (add attendees, send invitations) | **Missing** | No `attendee`, `invitee`, or `organizer` property is read or set anywhere in `cal-create.sh` or `cal-update.sh`. Nothing to invite with. |
| 2 | Alarms/reminders on events | **Missing** | No `sound alarm`, `display alarm`, or `mail alarm` object is created or attached anywhere in any script. |
| 3 | Availability/free-busy check (free slot, "am I busy at X", working hours, slot duration) | **Missing** | No busy/free query exists; `cal-events.sh`/`cal-search.sh` return raw event lists only, with no gap-finding or overlap logic and no working-hours or slot-duration parameters. |
| 4 | Batch/bulk operations (create series from a list; bulk-delete by exact IDs) | **Missing** | Every script operates on exactly one event per invocation (`cal-create.sh` makes one event; `cal-delete.sh`/`cal-update.sh`/`cal-read.sh` each take exactly one UID). No loop-over-a-list entry point exists at any layer. |
| 5 | Calendar CRUD (create/delete/rename a calendar) | **Missing** | `cal-list.sh` only enumerates existing calendars. No script creates, deletes, or renames a calendar. |
| 6 | Default/preferred-calendar + fuzzy name matching | **Missing** | Every script resolves a calendar via the exact-match AppleScript object specifier `calendar calendarName`, which is documented in `SKILL.md` itself as case-sensitive; a mismatch throws and is caught only as a generic "Calendar not found," with no fuzzy matching and no default/primary-calendar fallback when the name is omitted (omission instead means "search/act across every calendar," which is a different behavior than "pick a sensible default one"). |
| 7 | Timezone handling (specific timezone, not just local wall-clock) | **Missing** | `parseDate` in both `cal-create.sh` and `cal-update.sh` builds dates from `current date`, which always carries the Mac's current system timezone; there is no timezone parameter in any CLI, in `SKILL.md`'s documented date format, or in the AppleScript itself. Events are read back the same way (`as string` coercion in the caller's local timezone), with no timezone field in any output. |
| 8 | RSVP handling for invited events (accept/decline/tentative) | **Missing, and likely not cleanly available.** No script attempts this. Calendar.app's public AppleScript dictionary exposes `attendee` largely as a read surface tied to organizer-sent invitations and has no documented, reliable settable "reply status" property in the standard dictionary; this is consistent with the product owner's own fallback instruction ("if no public API exists, document that clearly instead of shipping something fragile") and should be verified independently (e.g. against EventKit's `EKParticipant`, which is also read-only for the current user's own status in most configurations) before committing to an implementation approach. |
| 9 | Structured JSON output everywhere | **Missing** | Every script emits plain text: pipe-delimited rows (`cal-list.sh`, `cal-events.sh`, `cal-search.sh`, each with a **different field count/order**) or ad hoc `Key: Value` blocks (`cal-read.sh`) or single free-text confirmation/error lines (`cal-create.sh`, `cal-update.sh`, `cal-delete.sh`). None of it is JSON, and the pipe-delimited fields are not escaped against the delimiter character appearing inside a summary/location/description value. |
| 10 | Conflict detection on create (warn on overlap) | **Missing** | `cal-create.sh` never queries existing events on the target calendar before inserting; overlapping bookings are silently allowed. |

Safety requirement (destructive/outward-facing gating, bounded reads, hard per-call
caps, mirroring `--draft-safe`):

| Safety element | Status | Evidence |
|---|---|---|
| Destructive actions gated/opt-in | **Missing** | `cal-delete.sh` deletes immediately on a UID match with no dry-run flag, no confirmation, and no environment/config-based safe-mode check of any kind. |
| Outward-facing actions gated/opt-in | **N/A in reference skill, but a forward risk** | No attendee/invitation support exists yet (gap 1), so there is nothing to gate today; when it is added, it must ship gated from day one rather than needing a later retrofit. |
| Bounded reads (explicit date window required) | **Partial** | `cal-events.sh` and `cal-search.sh` do apply a date window, but `days_ahead` is caller-supplied with no maximum, so "explicit window" is present while "hard cap" is not; `cal-read.sh` (and the UID-lookup portions of `cal-update.sh`/`cal-delete.sh`) have **no date window at all**, the single most direct precedent for a Calendar-side CPU-spin equivalent to the Mail bug fixed in v3.9.3. |
| Hard per-call caps (max results, max window) | **Missing** | No script anywhere enforces a maximum `days_ahead`, a maximum returned-event count, or a client-side timeout around the `osascript` invocation. A single call with a large window and/or an unscoped multi-calendar fan-out can run unbounded. |

---

## Borrow vs. avoid

### Borrow (concrete techniques worth reusing in the new tools)

- **`osascript - "$ARG1" "$ARG2" ... <<'EOF' ... on run argv ... end run EOF`** for
  passing untrusted/dynamic values as AppleScript `argv` items rather than
  string-interpolating them into the script body. This matches this repo's own
  documented convention (`core.run_applescript()`: stdin osascript, escaped user input)
  and should be the template for every new Calendar AppleScript call.
- **Bulk property fetch** (`name of every calendar`, `writable of every calendar` in
  `cal-list.sh`) instead of a per-item loop with per-item property reads, wherever the
  target set is small and fixed (calendar list, attendee list on one event, and so on).
- **`writable of cal` guard before any write**, applied consistently across
  create/update/delete in the reference skill; extend this same guard to gate the new
  destructive/outward-facing actions behind this repo's safe-mode flags.
- **Field-by-field `current date` mutation for date construction**
  (`set year of theDate to ...`, `set month of theDate to ...`, and so on) rather than
  coercing a string directly with `date "..."`, since the latter is locale-dependent and
  unreliable. Use this as the starting point for a timezone-aware date builder (it will
  need real timezone-offset math added; see the timezone gap above).
- **`AppleScript's text item delimiters` split/restore idiom** (`splitString` handler)
  for any future tokenizing needed inside an AppleScript payload.
- **UID-based addressing** as the identity model for read/update/delete, and especially
  for target capability 4's "bulk-delete by exact IDs, never by fuzzy query" requirement
  : the shape (`whose uid is eventUID`) is right, it just needs a mandatory
  calendar-and/or-date-window bound added (see Avoid, below) before it is safe to reuse
  at scale.
- **Plain iCalendar RRULE string support** (`set recurrence of e to eventRecurrence`):
  this is a real, working technique for recurring-event creation/update and should carry
  straight over.
- **`missing value` normalization before string concatenation** (`if eventLoc is
  missing value then set eventLoc to ""`), translated to Python's `None`-coalescing when
  mapping Calendar.app's optional properties into a JSON payload.
- **Capture-before-delete** (`set eventName to summary of e` before `delete e`) so a
  confirmation payload can still describe what was removed.
- **Per-field conditional PATCH semantics** on update (only mutate properties the
  caller actually supplied), translated to `Optional[...] = None` parameters in Python
  rather than the reference skill's sentinel-empty-string convention.

### Avoid (anti-patterns, especially anything unbounded)

- **Unbounded UID lookup with no date window**, present in `cal-read.sh`,
  `cal-update.sh`, and `cal-delete.sh` (`every event of cal whose uid is eventUID`
  evaluated against a calendar's entire history, repeated across every calendar on the
  system when none is named). This is the direct Calendar-side analogue of the Mail CPU
  spin fixed in v3.9.3 and must not be ported as-is; any UID-based lookup in the new
  tools needs either a caller-supplied date hint, a mandatory calendar scope, or an
  internally enforced timeout/result cap, ideally all three.
- **Destructive action with zero safety gate.** `cal-delete.sh` deletes on a single UID
  match with no dry-run, no confirmation, no opt-in flag, and no distinction between
  deleting one instance versus an entire recurring series. This is the opposite of the
  product owner's explicit safety requirement and must not be the model for the new
  delete tool; use this repo's `--draft-safe` precedent for Mail send as the template
  instead.
- **Caller-controlled, unbounded date window.** `cal-events.sh` and `cal-search.sh`
  accept an arbitrary `days_ahead` with no maximum and return every matching event with
  no result cap, fanning out across every calendar on the system (including read-only
  subscribed calendars like Birthdays/Holidays) whenever no calendar is named. New
  bounded-read tools need a hard maximum window and a hard maximum result count enforced
  independently of what the caller asks for.
- **In-AppleScript case-folding and substring search**, as implemented in
  `cal-search.sh`'s hand-rolled `toLowerCase` character loop, run up to three times per
  candidate event. Cheaper and simpler to fetch raw fields via a bulk property call and
  do the case-insensitive match in Python.
- **No client-side timeout around any `osascript` invocation**, anywhere in the skill.
  If Calendar.app hangs mid-scan (plausible on a large iCloud calendar under the
  unbounded-scan patterns above), the wrapping shell script hangs indefinitely with no
  recovery path. Any new tool must wrap its AppleScript calls with an enforced timeout,
  consistent with the caution that motivated the Mail v3.9.3 fix.
- **Inconsistent, unescaped output schemas.** Three different shapes across the skill
  (7-field pipe rows in `cal-events.sh`, a different 6-field pipe row in
  `cal-search.sh`, free-text `Key: Value` blocks in `cal-read.sh`), none of them
  escaping the `|` delimiter against its own appearance inside a summary/location/
  description value, and the `SKILL.md` documentation does not even match the real
  `cal-search.sh` output. Replace entirely with one consistent JSON schema
  (target capability 9), which also fixes the delimiter-collision correctness bug for
  free.
- **Errors returned as plain "Error: ..." strings on the same output channel as
  success data**, with no distinct exit code or JSON error envelope, across every
  script. A caller cannot reliably tell an error apart from a legitimately
  error-prefixed summary/description field. Replace with a structured error envelope
  and non-zero exit codes, consistent with this repo's existing structured-error
  conventions.
- **Inconsistent CLI conventions between sibling scripts** (`cal-create.sh` purely
  positional with eight ordered arguments; `cal-update.sh` a mix of one positional UID
  plus `--flag value` pairs). Not a runtime risk by itself, but a design smell worth
  naming: it signals that ad hoc shell-script CLIs were never meant to be a stable
  contract, which further supports moving straight to typed MCP tool parameters instead
  of any CLI-shaped intermediate layer.
- **Copy-pasted `splitString`/`parseDate` handlers** duplicated verbatim between
  `cal-create.sh` and `cal-update.sh` rather than shared. Fine in a from-scratch bash
  skill with no shared runtime; not a pattern to repeat in this repo, which already has
  a shared `core.run_applescript()` module and should centralize any equivalent date
  parsing/timezone logic there once.

---

## Sources fetched (recap)

All content in this report was derived directly from the following, fetched during this
session:

1. `https://api.github.com/repos/sundial-org/awesome-openclaw-skills/contents/skills/apple-calendar`
2. `https://api.github.com/repos/sundial-org/awesome-openclaw-skills/contents/skills/apple-calendar/scripts`
3. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/SKILL.md`
4. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-create.sh`
5. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-delete.sh`
6. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-events.sh`
7. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-list.sh`
8. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-read.sh`
9. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-search.sh`
10. `https://raw.githubusercontent.com/sundial-org/awesome-openclaw-skills/main/skills/apple-calendar/scripts/cal-update.sh`
