---
name: meeting-scheduler
description: This skill should be used when the user asks "find a time for us", "schedule a meeting with", "when am I free next week", "send a calendar invite", or "does 3pm work in Auburn time". Runs the find-slot workflow with check_availability, then conflict-checked create_event, then attendee or invitation handling within platform limits, with list_events and compose_email as support tools. Do NOT use for personal time blocking or calendar cleanup (see calendar-operator), and do NOT use for drafting ordinary email replies (see email-drafting).
---

# Meeting Scheduler

Coordinate meetings: find free slots, create conflict-checked events, and handle
attendees within what macOS actually allows.

## When To Use This Skill

| Signal | Skill |
|--------|-------|
| Scheduling with other people, invitations, cross-timezone times | **meeting-scheduler** (this skill) |
| Personal blocking, edits, deletes, calendar hygiene | `calendar-operator` |
| Writing the invitation email body | `email-drafting` (after the event exists) |

## The find-slot workflow

1. **Bound the search**: `check_availability(start=..., end=..., slot_minutes=30)`.
   The window is required and capped at 62 days. Tune `working_hours_start` /
   `working_hours_end` (HH:MM), `weekdays_only`, and `max_slots`. All-day events do
   not block by default (`ignore_all_day_events=True`).
2. **Offer slots**: present `free_slots` in the user's zone; `busy` explains why other
   times fail. Check `calendar_errors` for calendars that could not be read.
3. **Create with conflict checking**: `create_event(..., on_conflict="block")` for
   meetings, so a race with a new event refuses with `EVENT_CONFLICT` instead of
   double-booking. Use `"warn"` when the user wants the event regardless.
4. **Verify**: echo the created event's `start`/`end` plus `start_utc` back to the
   user in their own words.

## Cross-timezone discipline

Always pass `timezone` explicitly when any participant is in another zone and read
`resolved_timezone` back. Example: Tulsa and Auburn are both `America/Chicago`;
Boca Raton is `America/New_York`. "3pm Boca time" means
`create_event(title=..., start="2026-07-15T15:00:00", duration_minutes=60, timezone="America/New_York")`;
responses carry both the zone-local and UTC instants, so quote whichever the user
needs. Never pass a bare date and hope; naive datetimes are interpreted in the
requested zone.

## Attendees and invitations (platform reality)

Attendee attachment on macOS never guarantees an invitation is transmitted; there is no
public API for that, and script-attached attendees may not round-trip on some account
types. The tools therefore treat attendees as outward-facing:

- `create_event(attendees=[...])` requires explicit `send_invitations=True`
  (`INVITE_SEND_REQUIRES_CONFIRM` otherwise). Confirm with the user first.
- Under `--draft-safe`, attendee use returns `INVITE_SEND_BLOCKED`. Do not work
  around it. Under `--read-only` the calendar write tools are removed entirely.
- Every attendee response carries `invitation_delivery: "platform_dependent"`. Tell
  the user to verify in Calendar.app whether invitations actually went out.
- `update_event(attendees=[...])` diffs against the stored set: echoing the current
  list is a no-op; only additions are possible (Calendar.app cannot remove attendees).
- RSVP on behalf of the user is impossible (`CALENDAR_RSVP_UNSUPPORTED`).

### The reliable alternative: .ics invitation by email

When delivery matters, skip platform invitations entirely:

1. Create the event without attendees (`create_event`).
2. Draft (never auto-send) an invitation email through the gated Mail compose path
   (`compose_email(mode="draft")`), describing the meeting and attaching or inlining
   the event details; the user reviews and sends it themselves.
3. Hand the drafting itself to `email-drafting` when tone matters.

## Guardrails

- Bounded windows always: `check_availability` requires `start` and `end` and refuses
  too-wide or zero-width windows (`CALENDAR_WINDOW_TOO_WIDE`, `INVALID_EVENT_WINDOW`).
- One calendar tool call at a time; fan-out responses may carry `calendar_errors` and
  `budget_exhausted` (partial results, not failures).
- Rescheduling an existing meeting is `update_event(event_id=...)` from
  `calendar-operator`'s ID-first doctrine, with `on_conflict="block"` for safety.
- Never create events on calendars the user did not name without saying so; the create
  target falls back to `DEFAULT_CALENDAR`, then the engine default, and is echoed in
  the response.
- Do not attempt to trigger macOS consent prompts from tool calls; if calendar access
  is blocked, surface the `CALENDAR_ACCESS_DENIED` remediation and stop.

## Additional Resources

- `references/calendar-safety-limits.md`: bounds, mode matrix, error recovery, platform limitations.
