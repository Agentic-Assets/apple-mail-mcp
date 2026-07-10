# Calendar safety limits (canonical reference)

Canonical source: `plugin/skills/references/calendar-safety-limits.md`. Edit there and
run `python3 tools/validators/sync_skill_references.py`; per-skill copies are generated.

## Bounds (from `CALENDAR_BOUNDS` in `constants.py`)

| Cap | Value | Meaning |
|-----|-------|---------|
| Window width | 370 days | Any event window wider refuses (`CALENDAR_WINDOW_TOO_WIDE`); availability windows cap at 62 days |
| Events returned | 200 per call | Page with `offset`; `truncated` + `next_offset` signal more |
| Inner scan | 300 per pass | AppleScript slice cap per fetch pass |
| Occurrence expansion | 750 per call | Recurring blowup guard (`CALENDAR_WINDOW_TOO_DENSE`) |
| Batch create | 25 items | `BATCH_TOO_LARGE` above |
| Bulk delete | 20 default, 100 ceiling | `TOO_MANY_DELETES` above; ids chunk 25 per osascript call |
| Event ids per get | 25 | `get_events_by_id` input cap |
| Calendar fan-out | 20 calendars | Unscoped reads cap here (`fan_out_capped: true`) |
| Call budget | 240 seconds | Aggregate fan-out wall clock; skipped calendars land in `calendar_errors` with `budget_exhausted: true` |
| Attendees | 50 per event | `TOO_MANY_ATTENDEES` above |
| Alarms | 5 per event, 0..40320 minutes | `INVALID_ALARM` outside |

## Mode matrix (calendar tools; stricter than mail by design)

Mail's `--read-only` / `--draft-safe` flags block only the three send tools. The
calendar surface is gated harder, under the same two flags:

| Tool | default | `--draft-safe` | `--read-only` |
|------|---------|----------------|----------------|
| `list_calendars`, `list_events`, `get_events_by_id`, `check_availability` | allowed (bounded) | allowed | allowed |
| `create_event`, `update_event`, `batch_create_events` (no attendees) | allowed | allowed | removed (`CALENDAR_WRITE_BLOCKED` backstop) |
| `manage_calendars` create/rename | allowed | allowed | removed |
| create/update with attendee changes | needs `send_invitations=True` | `INVITE_SEND_BLOCKED` | removed |
| `delete_events` | dry-run default, caps, `span` | `CALENDAR_DELETE_BLOCKED` (env unlock `CALENDAR_ALLOW_DESTRUCTIVE=1`) | removed |
| `manage_calendars` delete | dry-run + confirm + force chain | `CALENDAR_DELETE_BLOCKED` (same unlock) | removed |
| `respond_to_invitation` | always `CALENDAR_RSVP_UNSUPPORTED` | same | same |

## Error recovery

| Code | What to do |
|------|------------|
| `UNBOUNDED_CALENDAR_SCAN` | Pass `days_ahead`/`days_back` or an absolute `start`+`end` pair |
| `CALENDAR_WINDOW_TOO_WIDE` / `CALENDAR_WINDOW_TOO_DENSE` | Narrow the window; scope to one calendar; page with `offset` |
| `INVALID_EVENT_WINDOW` / `INVALID_TIMEZONE` | Fix the ISO 8601 string or use an IANA zone name |
| `CALENDAR_NOT_FOUND` / `AMBIGUOUS_CALENDAR_SELECTOR` | Call `list_calendars` and use a candidate from the error |
| `EVENT_NOT_FOUND` | Widen the lookup window (`days_back`/`days_ahead`, or `lookup_days_back`/`lookup_days_ahead` on `update_event`) or pass the right `calendar` |
| `RECURRING_SPAN_REQUIRED` | Pass `span='all_occurrences'` after confirming the whole series should change |
| `RECURRING_SPAN_UNSUPPORTED` | Per-occurrence edits are not possible on this engine; tell the user |
| `EVENT_CONFLICT` | Offer the listed conflicts; retry with `on_conflict='warn'`/`'allow'` or find a free slot |
| `INVITE_SEND_REQUIRES_CONFIRM` | Ask the user, then retry with `send_invitations=True` |
| `INVITE_SEND_BLOCKED` / `CALENDAR_DELETE_BLOCKED` / `CALENDAR_WRITE_BLOCKED` | Mode-blocked; do not work around it, report to the user |
| `TOO_MANY_DELETES` / `BATCH_TOO_LARGE` | Split into smaller batches deliberately |
| `CALENDAR_CONFIRMATION_REQUIRED` | Run the delete dry-run, review, then pass the confirm flags |
| `CALENDAR_ACCESS_DENIED` | Follow the pane named in remediation (Automation for AppleScript, Calendars for EventKit) |

## Platform limitations (verified against primary sources)

- **Invitations:** attendees can be attached but no public macOS API guarantees the
  invitation is transmitted; responses carry `invitation_delivery: "platform_dependent"`.
  The reliable route is drafting an `.ics` invitation email through the gated Mail
  compose path (draft first, never auto-send).
- **RSVP:** accepting/declining/tentative is impossible through any public API;
  `respond_to_invitation` always returns `CALENDAR_RSVP_UNSUPPORTED`.
- **Recurring spans:** Calendar.app scripting mutates whole series only
  (`span='all_occurrences'`).
- **Recurring read coverage (AppleScript engine):** recurring occurrences are
  projected from masters whose start date falls within the last 400 days, so a
  standing series created earlier can be missing from a read window with no
  per-row error. `list_events` and `check_availability` disclose this with
  `recurring_lookback_days` and a `recurring_coverage_note` when the AppleScript
  recurring pass runs; the EventKit fast path expands natively and carries no
  such horizon. For mutations, the write-side lookup for recurring targets is
  widened back by the same 400-day horizon, and a series whose master started
  earlier returns `EVENT_NOT_FOUND` until the lookup window is widened.
- **Attendee removal:** not supported by Calendar.app scripting; updates only
  add. A removal-only or empty attendee diff is reported as "no attendee change
  applied", never as an applied change.
- **Performance:** community benchmarks put Calendar.app AppleScript `whose` scans at
  roughly 61 to 112 seconds on modest calendars, versus a near-instant EventKit path
  (roughly 3000x faster per ical.sidv.dev). The EventKit fast path activates
  automatically when installed (`pip install 'mcp-apple-mail[eventkit]'`) and already
  granted; check `eventkit_available` in `list_calendars`.
- **First-use hang:** a pending Automation consent prompt presents as a silent hang,
  not an error. If the first calendar call times out, answer the prompt under System
  Settings > Privacy & Security > Automation and retry. Never try to trigger the
  EventKit consent prompt from a tool call; the human-run `apple-mail calendar-grant`
  CLI command exists for that.
