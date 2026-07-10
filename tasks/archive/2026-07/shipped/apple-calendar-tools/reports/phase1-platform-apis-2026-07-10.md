# Phase 1 Platform API Research: macOS Calendar Automation

**Researcher:** 3 of 5
**Repo:** apple-mail-mcp (branch `feat/apple-calendar-tools`)
**Target platform:** macOS 14 / 15 / 26 era. This build host is macOS 26.5.2 (Darwin 25, `sw_vers` verified locally).
**Question:** Which engine(s) should back Apple Calendar tools in a zero-friction, pip-installed MCP server that also ships as an `.mcpb` bundle to Claude Desktop and as a plugin to Claude Code / Codex?
**Date:** 2026-07-10

---

## TL;DR (read this first)

1. **Two capabilities are impossible through every public macOS API: sending invitations to attendees, and RSVP (accept / decline / tentative).** AppleScript can add an attendee object to an event but never transmits the invite; EventKit's `attendees` and `EKParticipant.participantStatus` are read-only by Apple's own documentation. Plan the feature set around this, do not promise it.

2. **Performance forces a hard split.** Calendar.app AppleScript bounded reads ("every event whose start date >= A and <= B") are measured at **60 to 112 seconds** on modest calendars, versus **~0.13 seconds** for EventKit and "essentially instantaneous" for direct-API tools. The scripting community abandoned Calendar.app AppleScript reads years ago and moved to EventKit.

3. **But EventKit has a packaging problem that AppleScript does not**, and it is specific to this repo's distribution surfaces. EventKit access is gated by the macOS TCC "Calendars" category, which is attributed to the *responsible process* (the host app: Claude Desktop, Codex Desktop). Those host apps do **not** declare `NSCalendarsFullAccessUsageDescription`, so a plain EventKit call (PyObjC, JXA ObjC bridge, or a naive helper) is **denied synchronously with no prompt inside Claude Desktop / Codex Desktop today**. This is filed and confirmed against real EventKit MCP servers (anthropics/claude-code#63032, openai/codex#21228). AppleScript Calendar automation rides the *Apple Events* TCC category, the same grant this repo's existing Mail tools already use, so it keeps working on the `.mcpb` surface.

4. **Recommended architecture: AppleScript as the default/portable engine (writes, small reads, and the Claude Desktop `.mcpb` surface), with an optional EventKit fast-path for bounded reads and free/busy when the host terminal already holds a Calendars grant (Claude Code / Codex CLI launched from iTerm/Terminal).** Making EventKit work *everywhere* (including inside Claude Desktop) requires shipping a signed, notarized, self-disclaiming helper binary, which conflicts with a pure-Python pip install. Details and the full matrix are below.

---

## A. Calendar.app AppleScript dictionary and behavior

### Classes and verbs that exist

Apple's (archived but still authoritative) Calendar Scripting Guide documents the object model and operations: creating calendars and events, subscribing to remote calendars, **adding attendees to events**, **setting alarms**, and locating/displaying events ([About Calendar Scripting](https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/index.html)). The dictionary itself (viewable via Script Editor → File → Open Dictionary → Calendar, per the same guide) exposes these element classes:

- `calendar` (elements: `event`; properties include `name`, `color`, `writable`, `description`)
- `event` (properties: `summary`, `location`, `start date`, `end date`, `allday event`, `recurrence`, `status`, `url`, `description`, `uid`; elements: `attendee`, plus the four alarm classes)
- `attendee` (properties: `display name`, `email`, `participation status`)
- `display alarm`, `sound alarm`, `mail alarm`, `open file alarm` (each with a `trigger interval` / `trigger date`; sound alarm has `sound name`; mail alarm and open file alarm exist in the dictionary but are effectively legacy)

The guide's own examples confirm the creation verbs: [Creating an Event](https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-CreateanEvent.html) uses `make new event at end of events of calendar ... with properties {summary:..., start date:..., end date:...}`, and [Adding an Attendee to an Event](https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-AddanAttendeetoanEvent.html) uses:

```applescript
make new attendee at end of attendees with properties {email:"example@apple.com"}
```

**Alarms are settable via AppleScript** by making `display alarm` / `sound alarm` sub-objects with a `trigger interval` (minutes relative to start) on an event. **Participation status is readable** (`participation status` of `attendee`), matching the read-only reality of EventKit.

### Can AppleScript create attendees and does Calendar SEND invitations?

**AppleScript can create the attendee object, but it does NOT send an invitation, and there is no scriptable way to trigger the send.** This is consistent across a long trail of reports:

- Apple Developer Forums, [thread 681057](https://developer.apple.com/forums/thread/681057) (posted May 2021, zero replies): the exact `make new attendee ... {display name:..., email:...}` script adds the attendee but "does not send an invitation."
- MacScripter, [How to send an iCal event's attendee their invitation](https://www.macscripter.net/t/how-to-send-an-ical-events-attendee-their-invitation/48665): no AppleScript method exists to click iCal's Send button or trigger invitation delivery; the only offered workaround is building the invitation email from scratch and sending it through Mail scripting with a hand-constructed `.ics` attachment.
- Microsoft Q&A, [Creation of an attendee for a calendar event via AppleScript](https://learn.microsoft.com/en-us/answers/questions/4467708/creation-of-an-attendee-for-a-calendar-event-via-a): same finding, the attendee is added to the local event object but no invite is transmitted.

The practical result on modern macOS (iCloud / Exchange / CalDAV): adding an attendee via AppleScript is a **silent no-op for invitations**. Worse, attendees you add via script may not round-trip to the server correctly and can be dropped on the next sync; the community treats attendee writes via AppleScript as unreliable. **Do not build an "invite people" tool on this.**

### osascript auto-launch and the "not authorized" gate

`tell application "Calendar"` will auto-launch Calendar.app if it is not running (osascript sends an Apple Event, which starts the target app); Calendar does not need to be pre-running. However, the **first** send from a CLI/host triggers the TCC Automation prompt "X wants to control Calendar," and from a bare `osascript` in some contexts you get `-1743` / "Not authorized to send Apple events to Calendar" until granted ([Apple Community 254186467](https://discussions.apple.com/thread/254186467)). This is the same Apple Events automation gate this repo already handles for Mail. Note that launching Calendar.app on every call adds UI churn and latency; a `.mcpb` deployment should expect Calendar to open.

### Broader state of Calendar AppleScript (2024)

Michael Tsai's [The Sad State of Mac Calendar Scripting](https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/) (Oct 2024) documents that Apple Calendar's dictionary "does not even let you access the selected calendar events," that some create/duplicate/set-property paths only mutate temporary objects that never persist, and that the recommended escape hatch is EventKit via Shane Stanley's CalendarLib EC (AppleScriptObjC wrapper over EventKit). This is a strong signal that AppleScript is fine for *simple, well-formed writes* (make new event with explicit properties) but fragile for anything selection-based or bulk.

---

## B. Performance: Calendar.app AppleScript bounded reads

The community has quantified this and the numbers are damning. Dr. Drang's [AppleSloth](https://leancrew.com/all-this/2020/03/applesloth/) benchmarked `every event whose start date >= (current date)` on Calendar.app:

| Runner | Time |
|--------|------|
| Script Editor | ~61 s |
| Script Debugger | ~112 s |
| Terminal (`osascript`) | ~85 s |
| Standalone app | ~85 s |
| Equivalent **Python** (direct DB / EventKit) | "essentially instantaneous" |
| **Shortcuts** find-events | "less than two seconds" |

That was **collecting** matching events with no writes. Root cause: AppleScript (and JXA identically) crosses the process boundary with one Apple Event per property per event, so cost scales with events × properties. MacScripter threads corroborate: [Faster way to find a Calendar event](https://www.macscripter.net/t/faster-way-to-find-a-calendar-event/69257) and [Speeding up AppleScript searching for events in large calendar](https://macscripter.net/viewtopic.php?id=47065) both conclude the `whose` filter against Calendar.app is "almost unimaginably slow" and push people to EventKit or a dedicated small calendar.

The migration verdict is explicit in modern tooling. The `rem`/`ical` CLIs ([ical.sidv.dev](https://ical.sidv.dev/)) state their EventKit path is the "same API Calendar.app uses internally, not AppleScript, not IPC," giving "direct in-process memory access ... no IPC overhead," roughly **3000× faster than AppleScript**. The companion [rem architecture page](https://rem.sidv.dev/docs/architecture/) puts hard numbers on the same workload: **JXA read layer 42 to 60 s → EventKit 0.13 s (≈462× speedup)**. Shane Stanley wrote CalendarLib EC specifically to bypass this bottleneck (referenced by Tsai above).

**Conclusion for us:** bounded `whose`-clause reads over Calendar.app AppleScript are usable only for tiny calendars or single-event lookups by UID. Any "list my events this week/month" tool over real calendars needs EventKit (or a comparable direct engine) to be acceptable.

---

## C. EventKit via PyObjC (`pyobjc-framework-EventKit`)

### Authorization flow on macOS 14+

macOS 14 split calendar access into full vs write-only. The gate is `EKEventStore.requestFullAccessToEventsWithCompletion:` (PyObjC: `requestFullAccessToEventsWithCompletion_`), and the app **must** declare `NSCalendarsFullAccessUsageDescription` in the responsible bundle's Info.plist or TCC refuses before EventKit is reached. PyObjC ships the bindings ([apinotes/EventKit](https://pyobjc.readthedocs.io/en/latest/apinotes/EventKit.html), [PyPI](https://pypi.org/project/pyobjc-framework-EventKit/)) but defers all authorization semantics to Apple's framework.

**The critical CLI reality (this is the load-bearing risk for the project):** TCC attributes the request to the *responsible process*, which for a spawned child is normally the launching app, not the Python process. Verified evidence:

- **anthropics/claude-code#63032** ("Claude Desktop's Info.plist missing TCC usage strings, blocks all EventKit-based MCP servers"): a signed, notarized EventKit MCP (`PsychQuant/che-ical-mcp`) installed via `.mcpb` returns `Error: Calendar access denied` in **44 ms, synchronous, no prompt**. The reporter's diagnosis: "When the MCP binary calls `EKEventStore.requestFullAccessToEvents`, macOS attributes the request to the responsible process, Claude.app ... Because Claude.app declares no `NSCalendarsFullAccessUsageDescription`, TCC returns `.denied` synchronously without prompting." Confirmed still reproducible on Claude Desktop 1.11847.5 (2026-06-11). The stated user workaround is to run the MCP under **Claude Code (CLI)** instead, "The MCP binary inherits TCC from the terminal application (iTerm2, Terminal.app) that launched Claude Code, which can be granted normally via the system prompt."
- **openai/codex#21228** ("Codex Desktop on macOS cannot trigger Calendar/Reminders TCC prompts for EventKit CLIs"): `ical`/`rem` work from iTerm after the macOS prompt, but from Codex Desktop fail immediately ("Calendar access denied") with no prompt, because `Codex.app/Contents/Info.plist` lacks `NSCalendarsUsageDescription` / `NSCalendarsFullAccessUsageDescription`. `tccutil reset Calendar com.openai.codex` does not help.

**So: a bare CLI Python process does get a TCC prompt and does work when launched from a Terminal/iTerm that itself can hold (or be granted) the Calendars grant, because attribution flows to that terminal.** The same bare process launched by Claude Desktop or Codex Desktop is denied with no prompt, because those apps do not declare the usage string. There is no user-facing "+" button to add an app manually in current macOS, so end users cannot self-repair it.

Also note the completion-handler mechanics: `requestFullAccessToEventsWithCompletion_` returns asynchronously, so a short-lived Python CLI must spin an `NSRunLoop` (or block on a semaphore/threading.Event set inside the Python completion block) until the callback fires, or the process exits before authorization resolves. PyObjC does not document a ready recipe; this is bespoke glue we would own.

### Bounded fetch and its performance

`predicateForEventsWithStartDate:endDate:calendars:` builds an `NSPredicate` you pass to `eventsMatchingPredicate:`. This is the fast, indexed path (the 0.13 s / "instantaneous" numbers in §B). This is the correct primitive for every "events in window" and free/busy query.

### EKEvent writable fields

Writable via EventKit (`saveEvent:span:commit:error:`): `title`, `startDate`, `endDate`, `isAllDay`, `location`, `notes`, `url`, `timeZone`, `availability`, `alarms`, `recurrenceRules`, `calendar`. Save semantics: batch with `commit:NO` then a final `commit` for bulk writes ([EventKit Swift changes](https://developer.apple.com/library/archive/releasenotes/General/APIDiffsMacOSX10_11/Swift/EventKit.html)).

- **Timezone:** `EKCalendarItem.timeZone` is a writable `NSTimeZone` on `EKEvent`; setting it makes the event a fixed-timezone (floating vs zoned) event. Fully supported.
- **Alarms:** writable via `addAlarm:` / the `alarms` array with `EKAlarm` built from `alarmWithRelativeOffset:` or `alarmWithAbsoluteDate:` ([Setting alarms for calendar events](https://www.createwithswift.com/setting-alarms-for-calendar-events/)). Contrast: attendees are not.
- **Recurrence:** writable via `EKRecurrenceRule` + `addRecurrenceRule:`. Caveat: EventKit effectively supports a **single** recurrence rule; adding one replaces the existing rule ([nemecek.be](https://nemecek.be/blog/35/how-to-create-ios-reminders-in-code-with-alarms-or-recurrences)). Good enough for standard RRULEs, not for multi-RRULE edge cases.

### CRITICAL: attendees are read-only through public EventKit

Confirmed. `EKCalendarItem.attendees` is read-only and Apple's documentation states it is not possible to add attendees with EventKit:

- [openradar rdar://15504551](https://openradar.appspot.com/15504551) ("Should be possible to add attendees to events with Event Kit") and Apple Developer Forums [thread 74209](https://developer.apple.com/forums/thread/74209) ("Unable to add EKParticipant to an event"): the `attendees` property is read-only; the documented purpose of `EKParticipant` is to *read* participant info, not to construct attendees.
- Third-party library [AMGCalendarManager issue #2](https://github.com/AlbertMontserrat/AMGCalendarManager/issues/2): "EventKit cannot add participants to an event nor change participant information."

**Therefore invitations cannot be sent via EventKit.** This is a hard, long-standing platform limitation, not a version regression.

### Alarms / recurrence / timezone — see writable fields above (all supported)

### Saving/removing events and calendars

- Events: `saveEvent:span:error:` and `removeEvent:span:error:` with `EKSpan` (this event vs future occurrences).
- Calendars: **editable.** Create `EKCalendar calendarForEntityType:eventStore:`, assign an `EKSource` (must loop `eventStore.sources` to find the desired Local/iCloud source; the source cannot be set arbitrarily), then `saveCalendar:commit:error:` ([Apple doc](https://developer.apple.com/documentation/eventkit/ekeventstore/savecalendar(_:commit:))). Gotcha: [EKCalendar Not Persisting](https://developer.apple.com/forums/thread/70522) reports calendars created under the wrong source can disappear after app termination, so calendar CRUD needs careful source handling. `EKCalendar.isImmutable` / `allowsContentModifications` tells you whether a given calendar (e.g. a subscribed or delegated one) is writable at all.

### Free / busy

**There is no dedicated free/busy query in EventKit.** Availability is a per-event property: `EKEvent.availability` returns `busy` / `free` / `tentative` / `unavailable` ([EKEvent.availability](https://developer.apple.com/documentation/eventkit/ekevent/availability)). To compute a person's free/busy for a window you must fetch events with `predicateForEventsWithStartDate:endDate:calendars:` and fold their `availability` + start/end into busy intervals yourself. EventKit exposes no server-side CalDAV `VFREEBUSY` lookup for other people. So free/busy = "fetch events in window, compute busy blocks locally." AppleScript could do the same fetch but at the §B performance penalty.

---

## D. RSVP (accept / decline / tentative)

**There is no public macOS API to set your RSVP status. Say this plainly to the user.**

- `EKParticipant.participantStatus` is **read-only** by Apple's documentation ([participantStatus](https://developer.apple.com/documentation/eventkit/ekparticipant/participantstatus)); you can read `accepted` / `declined` / `tentative` / `pending` but cannot write them. The whole `attendees` array is read-only (§C).
- AppleScript exposes `participation status` on `attendee` as readable, with no verb to change your own response and no verb to emit the RSVP email.
- ScriptingBridge is just a different front-end to the same AppleScript dictionary, so it inherits the same limitation.

The only real ways to RSVP programmatically are out of scope for a local-app MCP: the CalDAV/Exchange/Graph server APIs, or GUI-scripting the Calendar/Mail buttons (fragile, breaks across macOS versions, and depends on the invite still sitting in the inbox). **Recommendation: do not ship an RSVP tool; document it as a platform limitation.**

---

## E. Packaging assessment

### PyObjC EventKit dependency weight

Small and pip-clean:

- `pyobjc-framework-EventKit`: sdist ~34 kB; it is a thin binding layer ([PyPI](https://pypi.org/project/pyobjc-framework-EventKit/)). Runtime deps: `pyobjc-core` and `pyobjc-framework-Cocoa`.
- `pyobjc-core`: prebuilt universal2 wheels ~670 to 720 kB depending on CPython (3.13/3.14) ([PyPI](https://pypi.org/project/pyobjc-core/)). No compiler needed on install ([install docs](https://pyobjc.readthedocs.io/en/latest/install.html)).
- `pyobjc-framework-Cocoa`: another modest binding wheel.

Total added install weight is on the order of a few MB, universal2, Python 3.9+ supported (PyPy/Jython/IronPython not). **Depending on it from the pip package is reasonable on size grounds.** Bundling it inside the `.mcpb` is also size-fine.

**The problem is not size, it is TCC attribution (§C), and it bites hardest exactly on the `.mcpb` / Claude Desktop surface** where the responsible app lacks the Calendars usage string. So a PyObjC EventKit dependency would work great for the Claude Code / Codex CLI installs (terminal-attributed grant) and be dead-on-arrival inside Claude Desktop until Anthropic adds the Info.plist keys, which is out of our control (issue #63032 was bot-closed).

### Pure-osascript JXA route to EventKit (no pip dependency)

You can reach EventKit with **zero Python dependency** via `osascript -l JavaScript` using the JXA ObjC bridge: `ObjC.import('EventKit')` then `$.EKEventStore.alloc.init`, `$.NSPredicate`, etc. ([galvanist JXA notes](https://www.galvanist.com/posts/2020-03-28-jxa_notes/), [scriptingosx: the unexpected return of JXA](https://scriptingosx.com/2021/11/the-unexpected-return-of-javascript-for-automation/)). `joargp/accli` is a real Apple Calendar CLI built on **JXA + EventKit** ([GitHub](https://github.com/joargp/accli)), proving the route works for real event CRUD.

**Feasibility:** works, and it removes the pip dependency, which suits the "zero-friction pure-Python" goal (we already shell out to `osascript` for Mail). **Auth behavior caveat that must not be missed:** JXA-via-`osascript` calling `$.EKEventStore` hits the **same Calendars TCC category** as PyObjC (it is an in-process EventKit call inside the `osascript` process), so attribution flows to the responsible app exactly as in §C. It does **not** escape the Claude Desktop denial. It only avoids the pip dependency, not the TCC problem. Complexity is also higher: async `requestFullAccessToEventsWithCompletion:` inside a one-shot `osascript` needs a manual run-loop pump, and marshaling `NSArray`/`NSDate`/predicate results back to JSON in JXA is finicky. Net: a viable dependency-free EventKit path for the terminal-attributed surfaces, with the identical Desktop limitation.

### The only way to make EventKit work inside Claude Desktop: a self-disclaiming signed helper

The escape hatch is the undocumented `responsibility_spawnattrs_setdisclaim()` API, which lets a spawned process "disclaim" the parent and become its **own** responsible process, so the TCC prompt is attributed to the helper (which carries its own embedded Info.plist usage string and code signature) instead of Claude Desktop. Background: Qt's [The Curious Case of the Responsible Process](https://www.qt.io/blog/the-curious-case-of-the-responsible-process), Michael Tsai's [2025 summary](https://mjtsai.com/blog/2025/07/07/the-curious-case-of-the-responsible-process/), Peter Steinberger's [Making AppleScript Work in macOS CLI Tools](https://steipete.me/posts/2025/applescript-cli-macos-complete-guide) (embed Info.plist in the binary, sign with `com.apple.security.automation.apple-events`, use `responsibility_spawnattrs_setdisclaim`), and the standalone tool [torarnv/disclaim](https://github.com/torarnv/disclaim). A real MCP does exactly this: [FradSer/mcp-server-apple-events](https://github.com/FradSer/mcp-server-apple-events) vendors a Swift `event` CLI spawned through a `bin/event-disclaim` shim that "disclaims TCC responsibility at spawn time. macOS therefore attributes the permission request to `event` itself," giving one machine-wide grant that works across MCP clients.

**Cost of that route:** ship a compiled, Developer-ID-signed, **notarized** helper binary (Swift or a disclaiming launcher) inside a Python pip package and an `.mcpb`. That breaks the pure-Python, no-build, no-signing-identity model this repo is built around, adds notarization to release, and is a meaningful maintenance surface. It is the *correct* long-term answer for full EventKit-everywhere, but it is a project of its own, not a phase-1 default.

---

## F. Other engines (one paragraph each)

**icalBuddy.** Unmaintained since ~2014 (original [hasseg.org/icalBuddy](https://hasseg.org/icalBuddy/); still packaged as [homebrew ical-buddy](https://formulae.brew.sh/formula/ical-buddy) via a 64-bit fork). Read-only (events/tasks listing), requires a Homebrew/binary install (friction, and another TCC-attributed process), and does no writes. Not viable as a primary engine for a pip-installed server, and forcing users to `brew install` breaks zero-friction. Modern replacements exist (`itspriddle/ical-guy` Swift CLI needs macOS 14+; `ajrosen/icalPal` Ruby; `ekctl`) but all are external binaries and mostly read-focused. Skip.

**Shortcuts CLI (`shortcuts run`).** `shortcuts run <name>` executes a user's Shortcut from the terminal, and Shortcuts' "Find Calendar Events" is fast (<2 s in §B) and its "Add New Event" can create events. But it requires the user to **pre-build and name Shortcuts**, passing structured input/JSON in and out of `shortcuts run` is awkward, and it cannot express attendees/invites or RSVP anyway. It is a poor fit for a self-contained MCP that must work on a fresh machine with no user setup. Not viable as the engine; at best a niche escape for a specific canned workflow.

**Swift one-off compiled helper.** A tiny Swift binary over EventKit is the highest-fidelity, fastest engine and is exactly what the notarized-helper route in §E uses (FradSer's `event`, `ical-guy`). It gives full EventKit read/write, bounded predicates, alarms, recurrence, timezone, calendar CRUD, and computed free/busy, and with `responsibility_spawnattrs_setdisclaim` + an embedded Info.plist it is the only thing that works **inside Claude Desktop**. The cost is a compile + Developer ID signing + notarization pipeline and a per-arch binary shipped in the wheel/`.mcpb`. Reasonable as a **phase-2 accelerator**, not as the zero-friction default.

---

## Recommendation matrix

Engine legend: **AS** = Calendar.app AppleScript (rides existing Apple Events grant, works on all surfaces incl. `.mcpb`, slow reads). **EK** = EventKit via PyObjC or JXA ObjC bridge (fast, but denied inside Claude Desktop until a disclaiming signed helper exists; works from terminal-attributed hosts). **EK-helper** = signed/notarized self-disclaiming Swift/EventKit helper (works everywhere, heavy to ship). **none** = no public API.

| Capability | Recommended engine | Confidence | Key risk | Citation |
|---|---|---|---|---|
| Bounded reads (events in window) | **EK** where terminal-attributed; **AS** fallback for `.mcpb`/small calendars | High | AS is 60 to 112 s on real calendars; EK is denied inside Claude Desktop | [leancrew AppleSloth](https://leancrew.com/all-this/2020/03/applesloth/), [rem arch](https://rem.sidv.dev/docs/architecture/), [claude-code#63032](https://github.com/anthropics/claude-code/issues/63032) |
| Create simple event | **AS** default (portable, works on `.mcpb`); EK when authorized | High | AS launches Calendar.app UI and can drop odd properties on sync; must set explicit properties | [Calendar Scripting: Creating an Event](https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-CreateanEvent.html), [ical.sidv.dev](https://ical.sidv.dev/) |
| Update event | **AS** default; EK when authorized | Med | Selection-based AS edits are unreliable; prefer lookup by UID then set explicit properties | [Sad State of Mac Calendar Scripting](https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/) |
| Delete event | **AS** default; EK `removeEvent:span:` when authorized | High | Recurring-event span (this vs future) semantics must be explicit | [EKEventStore](https://developer.apple.com/documentation/eventkit/ekeventstore) |
| Attendees + send invites | **none** | High | Impossible on every public API: AS adds attendee but never sends; EK `attendees` read-only | [Forums 681057](https://developer.apple.com/forums/thread/681057), [openradar 15504551](https://openradar.appspot.com/15504551), [AMGCalendarManager#2](https://github.com/AlbertMontserrat/AMGCalendarManager/issues/2) |
| Alarms | **EK** (`EKAlarm`) preferred; **AS** (`display/sound alarm`) works too | High | AS mail/open-file alarms are legacy; EK relative-offset alarms are clean | [createwithswift alarms](https://www.createwithswift.com/setting-alarms-for-calendar-events/), [Calendar Scripting Guide](https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/index.html) |
| Recurrence | **EK** (`EKRecurrenceRule`); AS `recurrence` string as fallback | Med | EK supports a single RRULE (adding replaces); complex multi-rule recurrence not expressible | [nemecek.be recurrence](https://nemecek.be/blog/35/how-to-create-ios-reminders-in-code-with-alarms-or-recurrences) |
| Timezone | **EK** (`EKEvent.timeZone`) | High | AS timezone handling is weaker; EK gives explicit floating vs zoned events | [EventKit Swift changes](https://developer.apple.com/library/archive/releasenotes/General/APIDiffsMacOSX10_11/Swift/EventKit.html) |
| RSVP (accept/decline/tentative) | **none** | High | No public API: `participantStatus` and `attendees` are read-only; ScriptingBridge inherits AS limits | [participantStatus](https://developer.apple.com/documentation/eventkit/ekparticipant/participantstatus), [Forums 74209](https://developer.apple.com/forums/thread/74209) |
| Free/busy | **EK** (fetch window + fold `availability`); compute locally | Med | No native free/busy query anywhere; must derive from events; slow if done via AS | [EKEvent.availability](https://developer.apple.com/documentation/eventkit/ekevent/availability) |
| Calendar CRUD | **EK** (`saveCalendar:commit:`) preferred; **AS** create/read | Med | Must assign correct `EKSource` or calendar vanishes after quit; subscribed/delegated calendars are immutable | [saveCalendar](https://developer.apple.com/documentation/eventkit/ekeventstore/savecalendar(_:commit:)), [EKCalendar Not Persisting](https://developer.apple.com/forums/thread/70522) |

### Cross-cutting deployment recommendation

1. **Default engine = AppleScript** for writes and for the Claude Desktop `.mcpb` surface, because it reuses the Apple Events TCC grant this repo's Mail tools already rely on and needs no signed binary. Accept its read-performance ceiling by keeping AS reads scoped (by UID, or narrow windows).
2. **Optional EventKit fast-path** (JXA ObjC bridge preferred to avoid a pip dependency, or PyObjC if the bridge glue proves too fiddly) for bounded reads and free/busy, gated on a runtime capability check; expect it to succeed under Claude Code / Codex CLI launched from a terminal and to fail-closed (fall back to AS) inside Claude Desktop.
3. **Do not ship attendees+invites or RSVP.** Surface them as documented platform limitations.
4. **Defer the signed, notarized self-disclaiming EventKit helper** (the only thing that makes fast EventKit work inside Claude Desktop) to a later phase, because it breaks the pure-Python/no-signing release model. Track anthropics/claude-code#63032 in case Anthropic adds the `NSCalendarsFullAccessUsageDescription` key, which would instantly unlock plain EventKit on the `.mcpb` surface with no helper.

---

## Sources

- Apple, Calendar Scripting Guide (index, Creating an Event, Adding an Attendee): https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/index.html · https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-CreateanEvent.html · https://developer.apple.com/library/archive/documentation/AppleApplications/Conceptual/CalendarScriptingGuide/Calendar-AddanAttendeetoanEvent.html
- Apple Developer Forums 681057 (attendee added, invite not sent): https://developer.apple.com/forums/thread/681057
- MacScripter, send invitation to attendee: https://www.macscripter.net/t/how-to-send-an-ical-events-attendee-their-invitation/48665
- Microsoft Q&A, create attendee via AppleScript: https://learn.microsoft.com/en-us/answers/questions/4467708/creation-of-an-attendee-for-a-calendar-event-via-a
- Michael Tsai, The Sad State of Mac Calendar Scripting (2024): https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/
- Apple Community, osascript not authorized to send Apple events: https://discussions.apple.com/thread/254186467
- Dr. Drang, AppleSloth (AppleScript Calendar perf benchmarks): https://leancrew.com/all-this/2020/03/applesloth/
- MacScripter, Faster way to find a Calendar event: https://www.macscripter.net/t/faster-way-to-find-a-calendar-event/69257
- MacScripter, Speeding up searching in large calendar: https://macscripter.net/viewtopic.php?id=47065
- ical / rem CLIs (EventKit vs AppleScript perf, architecture): https://ical.sidv.dev/ · https://rem.sidv.dev/docs/architecture/
- PyObjC EventKit apinotes / PyPI / install / core wheel sizes: https://pyobjc.readthedocs.io/en/latest/apinotes/EventKit.html · https://pypi.org/project/pyobjc-framework-EventKit/ · https://pyobjc.readthedocs.io/en/latest/install.html · https://pypi.org/project/pyobjc-core/
- EventKit attendees / participant read-only: https://openradar.appspot.com/15504551 · https://developer.apple.com/forums/thread/74209 · https://github.com/AlbertMontserrat/AMGCalendarManager/issues/2 · https://developer.apple.com/documentation/eventkit/ekparticipant/participantstatus
- EventKit writable fields (alarms, recurrence, timezone, save/calendar): https://www.createwithswift.com/setting-alarms-for-calendar-events/ · https://nemecek.be/blog/35/how-to-create-ios-reminders-in-code-with-alarms-or-recurrences · https://developer.apple.com/library/archive/releasenotes/General/APIDiffsMacOSX10_11/Swift/EventKit.html · https://developer.apple.com/documentation/eventkit/ekevent/availability · https://developer.apple.com/documentation/eventkit/ekeventstore/savecalendar(_:commit:) · https://developer.apple.com/forums/thread/70522
- TCC / responsible-process / disclaim (packaging): https://github.com/anthropics/claude-code/issues/63032 · https://github.com/openai/codex/issues/21228 · https://www.qt.io/blog/the-curious-case-of-the-responsible-process · https://mjtsai.com/blog/2025/07/07/the-curious-case-of-the-responsible-process/ · https://steipete.me/posts/2025/applescript-cli-macos-complete-guide · https://github.com/torarnv/disclaim · https://github.com/FradSer/mcp-server-apple-events · https://github.com/microsoft/vscode/issues/307364
- JXA ObjC bridge to EventKit / dependency-free CLIs: https://www.galvanist.com/posts/2020-03-28-jxa_notes/ · https://scriptingosx.com/2021/11/the-unexpected-return-of-javascript-for-automation/ · https://github.com/joargp/accli
- Other engines: https://hasseg.org/icalBuddy/ · https://formulae.brew.sh/formula/ical-buddy · https://github.com/itspriddle/ical-guy · https://github.com/ajrosen/icalPal · https://schappi.com/blog/meet-ekctl-a-command-line-interface-for-managing-calendars-and-reminders-on-maco
