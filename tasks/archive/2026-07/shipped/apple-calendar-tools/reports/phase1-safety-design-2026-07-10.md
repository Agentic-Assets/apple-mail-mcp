# Phase 1 Safety Design: Apple Calendar tools

Researcher 5 of 5 (safety model). Branch `feat/apple-calendar-tools`.

This document specifies the safety contract for the Apple Calendar tool surface so
those tools are **born bounded** and **born gated**, the way v3.9.3 "bounded mail
access" retrofitted the mail surface after unbounded AppleScript scans spun Mail.app
at 98% CPU on a 24k-message inbox. Every decision below mirrors machinery that already
ships in `plugin/apple_mail_mcp/`: the `ToolError` / `serialize_tool_error` envelope,
the `bounded_inbox_scan` capability-token pattern, the `SCAN_BOUNDS` central cap dict,
the `--read-only` / `--draft-safe` split, the `dry_run` + `max_deletes` + `confirm_*`
destructive patterns, and the ID-first `TARGET_SELECTOR_DEPRECATED` mutation doctrine.

## 0. Two facts that shape the whole design

**Fact A: the baseline skill ships the exact crash pattern the mail side just removed.**
The `awesome-openclaw-skills` apple-calendar delete script runs
`every event of cal whose uid is eventUID` across `calendars` (see
`apple-calendar-ref/scripts/cal-delete.sh`). That is a `whose` predicate materialized
over an unbounded element collection, the same shape as the pre-v3.4.x Gmail crash and
the AGENTIC-988 CPU spin. Its create/read scripts emit a pipe-delimited row
(`UID | Summary | Start | End | AllDay | Location | Calendar`), the same format whose
delimiter-collision risk the mail side hardened with `sanitize_pipe_delimited_field`
(a `|`-bearing title shifts every field right and can map the wrong id onto a delete).
The calendar tools must not inherit either pattern.

**Fact B: engine choice is a safety control, so this document assumes EventKit / PyObjC
as the primary engine and treats AppleScript as a narrow, escaped fallback.** EventKit's
`predicateForEventsWithStartDate:endDate:calendars:` is a natively date-bounded query:
its cost is a function of the requested window, not of total calendar size, which is the
structural fix for the "single call spins the CPU" risk. EventKit writes pass native
`NSString` / `NSDate` objects to `EKEvent` setters, so string-injection risk on titles,
notes, and locations drops to zero (see section 6). Where a capability only exists in
AppleScript, that path applies `escape_applescript()` plus integer/allowlist validation.
This document is engine-aware but the safety contract holds under either engine; the
error codes, caps, and mode gates are identical.

---

## 1. Bounded-read contract for calendar

Every event-returning tool (`list_events`, `search_events`, `get_upcoming_events`,
`get_events_today`, `check_availability`) is bounded on three independent axes, and
refuses any call that is unbounded on the window axis. Reads that address a single
event by id (`get_event_by_id`) and `list_calendars` (bounded by calendar count, which
is tiny) are exempt from the window requirement.

### 1.1 Explicit window is mandatory

A query accepts a window in one of two mutually-exclusive forms:

- Absolute: `start` and `end` as ISO 8601 datetimes (offset-aware or paired with a
  `timezone` param, see section 1.5).
- Relative: `days_back` and/or `days_ahead` as non-negative numbers.

There is **no unbounded default and no "all events" path**. A call that supplies
neither form, or supplies `days_back=0` and `days_ahead=0` with no absolute window, is
refused before any EventKit predicate or AppleScript runs, with:

```
UNBOUNDED_CALENDAR_SCAN
```

This is the direct analogue of `UNBOUNDED_SCAN_REQUIRED` (mail refuses
`recent_days=0` / `max_emails=0`). Convenience tools carry a **bounded** default so
the common case does not require the agent to compute dates: `get_upcoming_events`
defaults to `days_ahead=7` (`CALENDAR_BOUNDS["DEFAULT_UPCOMING_DAYS"]`), `get_events_today`
is implicitly a 1-day window. The core `list_events` / `search_events` tools require an
explicit window and refuse the empty one.

### 1.2 Hard maximum window width: 370 days

The window width (`end - start`, or `days_back + days_ahead`) is capped at
`CALENDAR_BOUNDS["MAX_WINDOW_DAYS"] = 370`. A wider window is refused with
`CALENDAR_WINDOW_TOO_WIDE` before any store query.

Rationale. Mail's `MAX_SCAN_DAYS` is 365. Calendars are legitimately queried a full year
out in ways inboxes are not: annual planning, two academic semesters (about nine months),
"this time next year" holds, yearly-recurring anniversaries. 370 is one year plus a two
week slack so a naive "next year" query does not clip. It is a cap on **width**, not on
the endpoints, so `days_back=200, days_ahead=200` (400 days) is refused even though each
endpoint alone is legal. The number is intentionally close to mail's 365 so the two
surfaces read as one system; both live in the central bounds dict so one edit retunes
everything.

### 1.3 Hard per-call event cap: 200 returned, 750 occurrences examined

Two caps, because a calendar's cost driver is different from an inbox's.

- `CALENDAR_BOUNDS["EVENT_RETURN_CAP"] = 200`: the maximum number of events a single
  call returns. If the bounded window matches more, the response carries
  `truncated: true` and `next_offset`, and the caller pages with `offset`. Results are
  never silently dropped and the tool never hard-refuses purely for matching many events
  (unlike the window-width violation, which is a hard refuse).
- `CALENDAR_BOUNDS["OCCURRENCE_SCAN_CEILING"] = 750`: a guard against pathological
  recurring-series expansion. A per-minute or per-hour recurring event expanded across a
  370-day window can generate hundreds of thousands of occurrences and is the calendar
  equivalent of the mail CPU spin. If expansion within the bounded window exceeds 750
  occurrences before the return cap is filled, the call hard-refuses with
  `CALENDAR_WINDOW_TOO_DENSE` and asks the caller to narrow the window or target one
  calendar.

Rationale for 200 vs mail's 50. Mail caps at 50 messages **scanned** per call because
each property read on a 24k Exchange/Gmail mailbox is an expensive cold-cache round trip
over IMAP, and the scan walks the store. A calendar is far smaller: a busy professional
runs on the order of 5 to 40 events per week, so a full-year window is roughly 250 to
2000 events, and EventKit returns them from a date-bounded predicate without walking the
whole store. A 50-event return cap would force excessive paging for ordinary "show my
next two months" reads. 200 keeps payloads small (structured JSON per event stays well
under MCP stdio limits), forces paging only for very large windows, and pairs with
the occurrence ceiling that actually defends against the CPU-spin failure mode. The
mail-cap philosophy is preserved (bounded window plus a hard per-call ceiling, page for
more) with numbers tuned to the calendar's real cost model rather than copied blindly.

### 1.4 Availability search is bounded tighter

`check_availability` (free/busy, slot-finding) takes its own window cap
`CALENDAR_BOUNDS["AVAILABILITY_MAX_WINDOW_DAYS"] = 62` (about two months). Slot-finding
does more work per unit time (it walks the window in `slot_minutes` increments across
every requested calendar), so its window is capped tighter than a plain read. Required
params: an explicit window (same `UNBOUNDED_CALENDAR_SCAN` refusal if missing),
`slot_minutes` (the duration to find), and optional `working_hours`
(e.g. `{"start": "09:00", "end": "17:00", "tz": "America/Chicago"}`) and `weekdays_only`.
Two months out is enough for "find a free 30 minute slot in the next few weeks" without
inviting a year-long minute-by-minute walk.

### 1.5 Timezone is a correctness-safety control

Ambiguous wall-clock times across Tulsa (`America/Chicago`), Auburn
(`America/New_York`), and remote are a quiet-error class: a 3pm hold created in the wrong
zone silently double-books or misses. Doctrine:

- Accept either offset-aware ISO 8601 (`2026-08-01T15:00:00-05:00`) or a naive datetime
  plus an explicit IANA `timezone` param.
- If neither an offset nor a `timezone` is supplied, the tool does not silently assume
  host-local; it resolves to host-local **and echoes** `resolved_timezone` in the
  response so the agent can confirm or correct. Reads return each event's start/end in
  both its stored zone and UTC.
- An unknown or malformed zone is refused with `INVALID_TIMEZONE` (a labeled variant of
  `INVALID_EVENT_WINDOW`). EventKit stores the zone on `EKEvent.timeZone`; the AppleScript
  fallback validates the tz against the IANA database before use.

### 1.6 Sketch: the calendar bounds dict and the window guard

Add to `constants.py`, mirroring `SCAN_BOUNDS`:

```python
CALENDAR_BOUNDS = {
    "MAX_WINDOW_DAYS": 370,           # hard cap on (end - start) / (days_back + days_ahead)
    "EVENT_RETURN_CAP": 200,          # max events returned per call; page via offset
    "OCCURRENCE_SCAN_CEILING": 750,   # hard stop on expanded occurrences (recurring-blowup guard)
    "AVAILABILITY_MAX_WINDOW_DAYS": 62,  # free/busy window cap (tighter than a plain read)
    "BATCH_CREATE_CAP": 25,           # max events per batch_create_events call
    "BULK_DELETE_DEFAULT_MAX": 20,    # default max_deletes for bulk_delete_events
    "BULK_DELETE_CEILING": 100,       # absolute ceiling even if max_deletes is raised
    "MAX_ATTENDEES": 50,              # cap invitees per event (mirrors MAX_WHOSE_IDS spirit)
    "DEFAULT_UPCOMING_DAYS": 7,       # bounded default for get_upcoming_events
}
```

A single producer validates every window (the `bounded_inbox_scan` analogue), so no tool
can smuggle in an unbounded scan:

```python
def bounded_calendar_window(*, start=None, end=None, days_back=None, days_ahead=None):
    # exactly-one-form check, non-negative check, then:
    width = window_width_days(start, end, days_back, days_ahead)
    if width is None:                       # no window supplied at all
        raise ToolError(code="UNBOUNDED_CALENDAR_SCAN", ...)
    if width > CALENDAR_BOUNDS["MAX_WINDOW_DAYS"]:
        raise ToolError(code="CALENDAR_WINDOW_TOO_WIDE", ...)
    return CalendarWindow(start=..., end=..., _issued_by="core.bounded_calendar_window")
```

---

## 2. Mode gating matrix

Three modes, identical to the mail plumbing in `__main__.py`:
`--read-only` sets `server.READ_ONLY = True` **and** `server.DRAFT_SAFE = True`;
`--draft-safe` sets `server.DRAFT_SAFE = True` only. Read-only removes the write and
destructive tools from the registry entirely (the `SEND_TOOLS` removal pattern), extended
here to two new tuples in `server.py`:

```python
CALENDAR_WRITE_TOOLS       = ("create_event", "update_event", "batch_create_events",
                              "create_calendar", "rename_calendar")
CALENDAR_DESTRUCTIVE_TOOLS = ("delete_event", "bulk_delete_events", "delete_calendar")
```

In `--read-only`, both tuples are removed from the registry. In `--draft-safe`, all
tools stay registered and enforce their gates internally (the `manage_drafts` pattern:
registered, but the dangerous action is refused inside the tool). Every tool also keeps a
belt-and-suspenders internal guard so the CLI path (which does not remove tools from a
registry) is equally safe.

Legend: **ALLOW** = permitted; **BLOCK** = refused with the named structured error;
**RESTRICT** = permitted only with the named guard satisfied.

| Tool / path | default | `--draft-safe` | `--read-only` |
|---|---|---|---|
| `list_calendars` | ALLOW | ALLOW | ALLOW |
| `list_events` (bounded) | ALLOW | ALLOW | ALLOW |
| `get_upcoming_events` / `get_events_today` (bounded) | ALLOW | ALLOW | ALLOW |
| `get_event_by_id` | ALLOW | ALLOW | ALLOW |
| `search_events` (bounded) | ALLOW | ALLOW | ALLOW |
| `check_availability` (bounded) | ALLOW | ALLOW | ALLOW |
| `create_event` (own calendar, no attendees) | ALLOW | ALLOW | BLOCK (removed) |
| `update_event` (own calendar, no attendees) | ALLOW | ALLOW | BLOCK (removed) |
| `batch_create_events` (own calendars, no attendees) | ALLOW (batch cap) | ALLOW (batch cap) | BLOCK (removed) |
| `create_calendar` | ALLOW | ALLOW | BLOCK (removed) |
| `rename_calendar` | ALLOW | ALLOW | BLOCK (removed) |
| create/update with `attendees=[...]` and/or `send_invitations=True` | RESTRICT: requires explicit `send_invitations=True` | BLOCK `INVITE_SEND_BLOCKED` | BLOCK (removed) |
| `delete_event` (by id) | RESTRICT: `dry_run=True` default; set `dry_run=False` to act; `span` required if recurring | BLOCK `CALENDAR_DELETE_BLOCKED` (unless env unlock) | BLOCK (removed) |
| `bulk_delete_events` (exact ids only) | RESTRICT: `dry_run=True` default, `max_deletes` cap, ids-only | BLOCK `CALENDAR_DELETE_BLOCKED` (unless env unlock) | BLOCK (removed) |
| `delete_calendar` | RESTRICT: `dry_run=True` default, `confirm_delete_calendar=True`, cascade count reported, `force_nonempty=True` if non-empty | BLOCK `CALENDAR_DELETE_BLOCKED` (unless env unlock) | BLOCK (removed) |
| `respond_to_invitation` (RSVP) | BLOCK `CALENDAR_RSVP_UNSUPPORTED` | BLOCK `CALENDAR_RSVP_UNSUPPORTED` | BLOCK `CALENDAR_RSVP_UNSUPPORTED` |

### 2.1 Key decisions behind the matrix

**Reads are allowed in every mode** because they are bounded (section 1); a read can
never trigger the CPU spin, so there is nothing to gate.

**Own-calendar create/update/batch/create-calendar/rename are allowed in `--draft-safe`.**
This is the deliberate difference from a pure send: the mode agents run under is meant to
let an agent block time on the user's own calendars (the personal-blocking use case)
without any outward-facing or irreversible effect. Creating an event on your own calendar
is reversible (delete it) and touches no third party. `create_calendar` and
`rename_calendar` are non-destructive and touch no events, so they follow the same rule as
mail's `create_mailbox` (a write, allowed in draft-safe because it neither sends nor
destroys).

**Adding attendees is an outward-facing send, and there is no "draft invite" in Calendar.**
This is the single most important gating decision. On macOS there is no concept of an
event with attendees that has not yet sent invitations: with a CalDAV/Exchange account,
the act of **saving** an event that has attendees is what dispatches the invitation
emails. EventKit's `EKParticipant` is read-only (you cannot construct or attach attendees
through EventKit at all), and Calendar.app AppleScript's `attendee` element does not
reliably create-and-send. So "add attendees but do not send" is not a real state the
platform offers. The design therefore treats the attendee path as equivalent to mail's
send:

- The only way to attach attendees is to pass `attendees=[...]` together with an explicit
  `send_invitations=True`. Passing `attendees` without `send_invitations=True` is refused
  (`INVITE_SEND_REQUIRES_CONFIRM`) rather than silently dropping the attendees or silently
  sending.
- Under `--draft-safe` and `--read-only`, `send_invitations=True` (and therefore any
  attendee attachment) is refused with `INVITE_SEND_BLOCKED`. The remediation tells the
  operator to run the server without `--draft-safe`, or to create the event without
  attendees and invite from the Calendar.app UI, or to draft an `.ics` invitation through
  the already-gated mail compose path.
- This mirrors how mail send is blocked behind `--draft-safe` and keeps a clean invariant:
  in the mode agents run under, no message and no invitation ever reaches a real person.

**All deletes are blocked in `--draft-safe` by default.** Deleting an event, a batch of
events, or a whole calendar is irreversible from the server's perspective (there is no
"calendar trash" the way mail has a Trash mailbox for `move_to_trash`; even
`move_to_trash` in mail is recoverable, whereas `delete e` in Calendar AppleScript and
`removeEvent:span:commit:error:` in EventKit are permanent). The whole point of the mode
agents run under is "no irreversible destructive action," so the safe default is a hard
block, not a per-call parameter the agent can flip. An operator who wants agent-driven
deletes runs the server without `--draft-safe`.

For the rare power-user who wants destructive calendar ops under draft-safe, provide an
**environment-level** unlock, not an agent-settable param:
`CALENDAR_ALLOW_DESTRUCTIVE=1` (off by default, read once at startup like
`DEFAULT_MAIL_ACCOUNT`). Even when unlocked, the per-call `dry_run` / `confirm_*` /
`max_deletes` guards below still apply. Keeping the unlock in the environment rather than
in a tool argument means an agent cannot grant itself delete power mid-session; only the
human who launched the server can.

**Calendar delete gets its own confirmation pattern, stronger than event delete.**
Deleting a calendar cascades to every event it contains, so it is the single most
dangerous operation in the surface. It requires all of: `dry_run=True` default (first
call previews and returns the exact count of events that would be destroyed),
`confirm_delete_calendar=True` (an explicit second-call flag, the `confirm_empty`
pattern from `manage_trash empty_trash`), and `force_nonempty=True` when the calendar is
not empty (so a non-empty calendar cannot be destroyed by a caller who thought it was
empty). Fuzzy name matching is disabled for this tool (section 7): you never fuzzy-match
your way into a cascade delete.

**RSVP is not shipped as a functional tool.** See section 2.2.

### 2.2 RSVP: documented gap, not fragile ship

The product owner asked for RSVP "IF the platform allows it; if no public API exists,
document that clearly instead of shipping something fragile." It does not. EventKit
exposes no public API to accept, decline, or tentatively-accept an invitation
(`EKParticipant.participantStatus` is read-only; there is no supported setter or
response call). Calendar.app's AppleScript dictionary does not expose an RSVP action
either. Any implementation would rely on UI scripting the Calendar.app notification
center, which is exactly the fragile, version-brittle path the owner asked to avoid.

Decision: register a `respond_to_invitation` tool that is a **documented refusal shim**,
the same pattern as `full_inbox_export` returning `UNBOUNDED_EXPORT_DISABLED` with no
AppleScript run. It always returns `CALENDAR_RSVP_UNSUPPORTED` with remediation that
names the reason (no stable public macOS API) and points the user to respond in
Calendar.app or their mail client. Shipping the refusal shim (rather than nothing) means
the capability is discoverable, the limitation is machine-readable, and an agent that
tries gets a clear, consistent answer instead of a crash or a silent no-op.

---

## 3. Structured error codes for calendar

All returned via `serialize_tool_error(ToolError(code=..., message=..., remediation={...}))`,
the identical `{"error": true, "code", "message", "remediation"}` envelope every mail
tool emits. Codes are `SCREAMING_SNAKE_CASE` and consistent with the existing set. Where a
mail code already fits (the ID-first mutation doctrine), it is **reused** rather than
duplicated.

| Code | Trigger | Remediation text |
|---|---|---|
| `UNBOUNDED_CALENDAR_SCAN` | Event query with no window (`days_back`/`days_ahead` both 0/None and no `start`/`end`). | Pass an explicit window: `start` + `end` (ISO 8601) or `days_back` / `days_ahead`. Full-calendar scans are disabled; bound this call. |
| `CALENDAR_WINDOW_TOO_WIDE` | Window width exceeds `MAX_WINDOW_DAYS` (370). | Narrow to a window of 370 days or fewer, or page with `offset` across several bounded calls. |
| `CALENDAR_WINDOW_TOO_DENSE` | Bounded window expands to more than `OCCURRENCE_SCAN_CEILING` (750) occurrences before the return cap fills (recurring-series blowup). | Narrow the window, or target a single `calendar_id`, or exclude the high-frequency recurring calendar. |
| `INVALID_EVENT_WINDOW` | `start >= end`, or a malformed datetime. | Ensure `start` is before `end` and both are valid ISO 8601. |
| `INVALID_TIMEZONE` | Unknown or malformed IANA timezone. | Pass a valid IANA name such as `America/Chicago` or `America/New_York`, or an offset-aware ISO 8601 datetime. |
| `TARGET_SELECTOR_DEPRECATED` (reused) | A mutation (`update_event`, `delete_event`, `bulk_delete_events`) was targeted by `title` / `summary` / query instead of an exact event id. | Call `search_events(...)` or `list_events(...)` first, collect `event_id`, then retry with `event_id` / `event_ids`. |
| `RECURRING_SPAN_REQUIRED` | `delete_event` or `update_event` targets a recurring series id without an explicit `span`. | Pass `span="this_occurrence"`, `"future_occurrences"`, or `"all_occurrences"`. There is no default: deleting or editing a series is not assumed to mean the whole series. |
| `EVENT_NOT_FOUND` | `event_id` (or an id in `event_ids`) does not resolve in the target calendar/window. | Re-run `search_events(...)` to get current ids; ids can change after a store sync. |
| `CALENDAR_NOT_FOUND` | Named calendar not found after fuzzy match. | Call `list_calendars()`; use an exact name or `calendar_id` from that list. Close matches are listed in `remediation.candidates`. |
| `AMBIGUOUS_CALENDAR_SELECTOR` | A fuzzy calendar name matched more than one calendar. | Pass an exact name or `calendar_id`; candidates are listed in `remediation.candidates`. (Mirrors `AMBIGUOUS_ATTACHMENT_SELECTOR`.) |
| `CALENDAR_READ_ONLY` | Write/delete targeted a subscribed or system calendar (Birthdays, Holidays, subscribed `.ics`). | Choose a writable calendar; `list_calendars()` reports `writable` per calendar. |
| `CALENDAR_WRITE_BLOCKED` | Any write attempted while `READ_ONLY` (belt-and-suspenders for the CLI path and any tool the registry removal missed). | Restart the server without `--read-only` to enable writes. |
| `CALENDAR_DELETE_BLOCKED` | Any delete attempted while `DRAFT_SAFE` and `CALENDAR_ALLOW_DESTRUCTIVE` unset. | Restart without `--draft-safe` (or set `CALENDAR_ALLOW_DESTRUCTIVE=1`) to allow deletes; export/verify the events first. |
| `INVITE_SEND_BLOCKED` | `send_invitations=True` (or an `attendees` attachment, which implies a send) while `DRAFT_SAFE` or `READ_ONLY`. | Restart without `--draft-safe` to send invitations; or create the event without `attendees` and invite from Calendar.app; or draft an `.ics` invite through the gated mail compose path. |
| `INVITE_SEND_REQUIRES_CONFIRM` | `attendees=[...]` passed without an explicit `send_invitations=True`. | There is no draft-invite state in Calendar: saving an event with attendees sends invitations. Pass `send_invitations=True` to confirm the send, or omit `attendees`. |
| `TOO_MANY_ATTENDEES` | `attendees` longer than `MAX_ATTENDEES` (50). | Split the invite list or invite in batches; very large invitee lists are error-prone and rate-limited by the mail server. |
| `BATCH_TOO_LARGE` | `batch_create_events` items exceed `BATCH_CREATE_CAP` (25). | Chunk into batches of 25 or fewer and call once per batch. |
| `TOO_MANY_DELETES` | `bulk_delete_events` ids exceed `max_deletes`, or exceed `BULK_DELETE_CEILING` (100) even with `max_deletes` raised. | Reduce the id list, raise `max_deletes` up to the ceiling with intent, or chunk across calls. |
| `CALENDAR_CONFIRMATION_REQUIRED` | `delete_calendar` called with `dry_run=False` but `confirm_delete_calendar` not set, or a non-empty calendar without `force_nonempty=True`. | Review the dry-run cascade count, then re-call with `confirm_delete_calendar=True` (and `force_nonempty=True` if the calendar has events). |
| `CALENDAR_RSVP_UNSUPPORTED` | `respond_to_invitation` called. | No stable public macOS API exposes invitation RSVP. Accept/decline in Calendar.app or your mail client. |
| `CALENDAR_ACCESS_DENIED` | EventKit authorization `Denied`/`Restricted`, or AppleScript automation error (`-1743`). | See section 8. Names the exact System Settings pane and access level. |

Response-level flags (not errors, so not in the table): `truncated` + `next_offset` on
reads that hit `EVENT_RETURN_CAP`; `has_conflicts` + `conflicts[]` on create/update
(section 4); `resolved_timezone` on any tool that inferred a zone.

---

## 4. Conflict detection semantics

**Recommendation: check by default, in the same round trip, non-blocking, with an opt-out
and an opt-to-block. Do not require a separate `check_availability` call first.**

On `create_event` and `update_event`, before committing, the tool runs one bounded query
over exactly the new event's own `[start, end]` window on the target calendar (and,
optionally, a caller-listed set of calendars to check against). Any overlapping event is
returned in a `conflicts[]` array with `event_id`, `title`, `start`, `end`, and
`calendar`, and `has_conflicts` is set. Behavior is controlled by one param:

- `on_conflict="warn"` (default): create the event and report the conflicts. This is the
  safe default because double-booking is often intentional (a tentative hold over a
  soft-committed block, a travel event spanning a meeting), and a hard block would surprise
  the user. The warning is always surfaced, so the agent and user can react.
- `on_conflict="block"`: refuse the create/update with `EVENT_CONFLICT` and the same
  `conflicts[]` payload; nothing is written.
- `on_conflict="allow"`: create silently, skipping the check entirely (saves the extra
  round trip when the caller already knows).

Why in-line and not a mandatory separate call. The conflict check is cheap: it is a
predicate over the event's own duration (minutes to hours), not the 370-day read window,
so with EventKit it is a single native `predicateForEventsWithStartDate:endDate:calendars:`
scoped to `[start, end]`, one extra round trip of negligible cost. Requiring the agent to
call `check_availability` first is easy to forget; a forgotten check means a silent
double-book, which is the exact failure the feature exists to prevent. Baking it into the
write **fails safe**: you always get the warning unless you explicitly opt out. The
standalone `check_availability` tool still exists for the different job of **finding** a
free slot across a multi-day window (requirement 3); conflict-on-create does not depend on
the agent remembering to run it.

`batch_create_events` runs the same per-item check and aggregates conflicts per item; with
`dry_run=True` it previews the whole set's conflicts before any event is written, so an
agent can inspect a proposed series for collisions and commit only if satisfied.

`EVENT_CONFLICT` is added to the code list as the `on_conflict="block"` refusal.

---

## 5. Batch caps and destructive-op parameters

| Operation | Cap / default | Rationale |
|---|---|---|
| `batch_create_events` | `BATCH_CREATE_CAP = 25` events/call; `dry_run` optional (default `False`) | 25 covers the real use cases (a semester of guest lectures ~15, a conference agenda ~20) while keeping one transaction bounded and the failure blast radius small. Each item still runs a conflict check, so 25 * (create + bounded conflict query) stays well under timeout. `dry_run=True` previews the whole set's conflicts before committing. Over-cap: `BATCH_TOO_LARGE`. |
| `bulk_delete_events` | exact `event_ids` only (never a query); `dry_run=True` default; `max_deletes=BULK_DELETE_DEFAULT_MAX=20`; hard `BULK_DELETE_CEILING=100` | Bulk delete by exact ids only, never by fuzzy query, matches the mail `move_email(message_ids=[...])` doctrine and the owner's "bulk-delete by exact ids, never by fuzzy query." Default cap 20 matches `manage_drafts cleanup_empty` (the recoverable-ish bulk path); it is above `manage_trash`'s 5 because calendar bulk delete is always id-explicit (no scan surprise), but a 100 absolute ceiling caps the blast radius even if `max_deletes` is raised. `dry_run=True` previews which ids resolve to which events; a typo'd or non-existent id fails the whole preview (`EVENT_NOT_FOUND`) rather than partially deleting. |
| `delete_event` | by `event_id` only; `dry_run=True` default; `span` required if recurring | Single-event delete previews by default, acts on `dry_run=False`. Recurring ids require an explicit `span` (`RECURRING_SPAN_REQUIRED`) so "delete this event" never silently removes an entire series (the baseline skill's documented footgun). |
| `delete_calendar` | `dry_run=True` default; `confirm_delete_calendar=True`; cascade count reported; `force_nonempty=True` if non-empty; fuzzy matching disabled | The only cascade operation. Dry run reports the exact number of events destroyed. The extra confirm flag and the non-empty force flag together make an accidental cascade delete take three deliberate signals (name-or-id exact, confirm, force). |

`dry_run` default summary: **True** for all three delete tools; **False** for
create/update/create-calendar/rename (creating is the intent, but they still return
conflict warnings and a full echo of what was written). `batch_create_events` offers
`dry_run` (default False) as a preview affordance.

---

## 6. Injection safety

**Primary engine (EventKit / PyObjC): injection risk on free-text fields is near zero.**
Titles, notes/description, location, URL, and calendar names are set with native setters
(`EKEvent.setTitle_()`, `setNotes_()`, `setLocation_()`, `EKCalendar.setTitle_()`) that
take `NSString` objects. There is no script string for a value to break out of, so a
title like `"Standup"; delete calendar "Work"` is stored verbatim as text and cannot
execute. This is a concrete safety reason to prefer EventKit over AppleScript for writes.
Two residual EventKit concerns:

- Date-range queries must use the typed convenience
  `predicateForEventsWithStartDate:endDate:calendars:`, never a string
  `predicateWithFormat:` built from user input. A format-string predicate is the EventKit
  analogue of SQL injection; the typed constructor has no format string, so it is immune.
- Recurrence must be built with typed `EKRecurrenceRule` constructors (frequency,
  interval, `EKRecurrenceEnd`), never by parsing a user-supplied RRULE string into a
  predicate or script.

**AppleScript fallback: full escaping plus structural validation.** Any path that must
drop to Calendar.app AppleScript (for a capability EventKit lacks) treats every user
string exactly as the mail side does:

- Route every user string through `core.escape_applescript()`: it already handles
  backslash-first, then double-quote, then CR/LF/tab, and the Unicode line/paragraph
  separators `U+2028` / `U+2029` that break AppleScript string parsing. Reuse it as-is;
  do not write a second escaper.
- Never interpolate a raw datetime string into `date "..."`. Parse to integer
  year/month/day/hour/minute in Python, validate each is an integer in range, and
  interpolate the validated integers into the `set year of theDate to N` form (the
  baseline `parseDate` shape, made injection-safe by integer validation).
- Validate any recurrence RRULE against an allowlist grammar
  (`FREQ`, `INTERVAL`, `COUNT`, `UNTIL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH` with typed
  values) before it is interpolated; reject any RRULE containing a quote, newline, or an
  unrecognized token. RRULE is user-adjacent and must not be a raw interpolation.

**Structured JSON output is itself an injection control (requirement 9).** The baseline
skill emits `UID | Summary | Start | End | AllDay | Location | Calendar` pipe rows. A
summary containing `|` shifts every subsequent field right and can map the wrong `UID`
onto a later `delete_event(event_id=...)` call, the identical wrong-target-delete risk the
mail side documents for `|||` rows and defends with `sanitize_pipe_delimited_field`. By
emitting one JSON object per event (never a delimited row), the calendar tools remove that
corruption class entirely: `event_id` is a discrete JSON field that no title content can
displace. Do not reintroduce a delimited output format anywhere in the surface. This makes
requirement 9 (structured JSON everywhere) a safety requirement, not only an ergonomic
one.

---

## 7. Identifier doctrine

Mirror the mail ID-first rule exactly. Mutations act **only** by exact event identifier,
never by title/summary/date match.

- The exact id is the EventKit `eventIdentifier` (or `calendarItemIdentifier` for
  cross-store stability), or the AppleScript `uid` in the fallback path. Discovery tools
  (`search_events`, `list_events`, `get_upcoming_events`) return `event_id` on every row.
- `update_event`, `delete_event`, and `bulk_delete_events` accept `event_id` /
  `event_ids` only. A `title` / `summary` / query selector on any of these returns
  `TARGET_SELECTOR_DEPRECATED` (reused from mail, with calendar remediation:
  `discovery = search_events`, `exact_selector = event_id`) **before** any store query, so
  a fuzzy target never even runs a scan. This is the `move_email` / `manage_trash` doctrine
  applied unchanged.
- The workflow the bundled skill teaches: `search_events(...)` -> read `event_id` ->
  `delete_event(event_id=..., dry_run=True)` -> review -> `dry_run=False`. Never re-search
  by title to act; re-searching re-pays scan cost and risks matching a different event than
  the one the user reviewed.
- **Recurring-series id caveat.** An `eventIdentifier` is shared across all occurrences of
  a series, so an id alone is ambiguous for a recurring event. Targeting one occurrence
  requires `event_id` **plus** `occurrence_date` (the specific instance start). Any
  mutation of a recurring id without an explicit `span` (section 3) is refused with
  `RECURRING_SPAN_REQUIRED`; there is no default span, so "delete this event" can never
  silently wipe a whole series.
- **Calendar identifiers.** `calendar_id` is the EventKit `calendarIdentifier`. Fuzzy
  calendar-name resolution (requirement 6, the preferred-calendar helper) is a convenience
  for **reads and for the create target only**: an exact case-insensitive name match wins;
  more than one candidate returns `AMBIGUOUS_CALENDAR_SELECTOR` (never silently pick one);
  no match returns `CALENDAR_NOT_FOUND` with close-match candidates. Fuzzy matching is
  **disabled** for `delete_calendar` and for any destructive target: those require an
  exact `calendar_id` or exact name, so you cannot fuzzy-match your way into destroying the
  wrong calendar. The primary-calendar default (`DEFAULT_CALENDAR` env var, else EventKit
  `defaultCalendarForNewEvents`) is used only to pick a create target when none is given;
  it is never used to resolve an update or delete target.

For `bulk_delete_events`, the exact-ids-only rule is absolute: there is no query path at
all, and the `dry_run` preview resolves every id to a concrete event first, so a single
bad id aborts the whole batch rather than deleting the wrong thing.

---

## 8. TCC / permission failure UX

Calendar access is gated by macOS TCC, separately from Mail's automation permission. The
first tool call may trigger the OS consent prompt; a denied prompt persists until changed
in Settings. Detect and surface it as a structured error, never as a raw stack trace.

Detection:

- EventKit: check `EKEventStore.authorizationStatusForEntityType_(EKEntityTypeEvent)` and
  branch on `Denied` / `Restricted`; also catch a denied result from
  `requestAccessToEntityType:completion:` on first use.
- macOS 14 (Sonoma) and later distinguish **Full Access** from **Write Only**. Write-only
  authorization cannot read events, so a read that fails with a write-only status is a
  distinct sub-case; include the detected `access_level` in the error payload so the
  remediation is precise.
- AppleScript fallback: catch osascript error `-1743` ("Not authorized to send Apple
  events") and Calendar automation denials.

Structured error:

```json
{
  "error": true,
  "code": "CALENDAR_ACCESS_DENIED",
  "message": "Calendar access is not authorized for the app running this server (detected access level: none).",
  "remediation": {
    "pane": "System Settings > Privacy & Security > Calendars",
    "action": "Enable Calendar access (choose 'Full Access' on macOS 14+) for the app hosting this server: Claude, Terminal, or your MCP host process.",
    "note": "The first call may show a one-time consent prompt. If it was denied, the choice persists until changed in the pane above. 'Write Only' access cannot read events; reads require 'Full Access'.",
    "access_level": "none"
  }
}
```

This mirrors the mail surface's TCC guidance style (Automation plus Mail Data Access) but
names the Calendars pane and the Full-vs-Write-Only distinction specific to EventKit on
recent macOS.

---

## 9. Summary of new safety primitives to add

1. `constants.CALENDAR_BOUNDS` dict (section 1.6): window, return, occurrence, availability,
   batch, bulk-delete, attendee caps, and the bounded default. One edit retunes the surface,
   the `SCAN_BOUNDS` pattern.
2. `bounded_calendar_window(...)` producer (section 1.6): the only sanctioned window
   validator, the `bounded_inbox_scan` capability-token analogue. Emits
   `UNBOUNDED_CALENDAR_SCAN` / `CALENDAR_WINDOW_TOO_WIDE`.
3. `server.CALENDAR_WRITE_TOOLS` and `server.CALENDAR_DESTRUCTIVE_TOOLS` tuples (section 2),
   removed from the registry under `--read-only` exactly like `SEND_TOOLS`.
4. Internal `DRAFT_SAFE` guards in every write/delete tool (the `manage_drafts` /
   `_send_blocked` pattern): deletes -> `CALENDAR_DELETE_BLOCKED`; `send_invitations` ->
   `INVITE_SEND_BLOCKED`. Plus the `CALENDAR_ALLOW_DESTRUCTIVE` env unlock (off by default).
5. Reuse `TARGET_SELECTOR_DEPRECATED` for id-first mutation enforcement; add the calendar
   codes in section 3.
6. EventKit-first engine with escaped AppleScript fallback (section 6), and JSON-only
   output (no delimited rows) as an injection control.
7. `dry_run` / `max_deletes` / `confirm_delete_calendar` / `force_nonempty` / `span`
   destructive-op guards (section 5), matching `manage_trash` and `manage_drafts`.
8. `CALENDAR_ACCESS_DENIED` TCC UX with pane and access-level (section 8).

Every one of these has a direct precedent already shipping in `plugin/apple_mail_mcp/`, so
the calendar surface adds no new safety concept, only a calendar-shaped instance of each
control the mail surface already proved.
