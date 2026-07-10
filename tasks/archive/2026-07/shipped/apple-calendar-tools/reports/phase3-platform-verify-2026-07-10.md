# Phase 3: adversarial platform-claim verification (Apple Calendar tools plan)

Verification pass against
[`plan-2026-07-10.md`](../plan-2026-07-10.md) and
[`research-report-2026-07-10.md`](../research-report-2026-07-10.md), branch
`feat/apple-calendar-tools`, run 2026-07-10 on this Mac mini (macOS 26.5.2,
build 25F84, Darwin 25.5.0 arm64). Method: every claim below was checked
against a fresh primary source (Apple documentation, an SDK header file, a
GitHub issue fetched live via `gh`, or a community benchmark), and where the
check was cheap and read-only, also against a live, bounded probe on this
machine. No event, calendar, attendee, or Mail object was created, modified,
or deleted at any point. The scratch environment used for the PyObjC probes
lives at `tasks/active/apple-calendar-tools/verify-venv` (43 MB,
`pyobjc-core`/`pyobjc-framework-Cocoa`/`pyobjc-framework-EventKit` all
`12.2.1`, matching the versions RR section 5.1 recorded); it is left in place
as the allowed scratch venv for this workstream. All `/tmp` probe scripts used
to generate the evidence below were deleted after the run.

Twelve claims were selected as the riskiest platform-fact dependencies of the
plan: the ones where a wrong answer would either flip the engine architecture
decision (section 2 of the plan) or invalidate a shipped safety/feature
decision (attendees, RSVP, free-busy, recurring windows).

---

## Verdict summary

| # | Claim | Verdict | Changes engine decision if wrong? |
|---|-------|---------|-----------------------------------|
| 1 | EventKit `attendees` is read-only; no public API sets it | **CONFIRMED** | Yes |
| 2 | EventKit `EKParticipant.participantStatus` is read-only; no RSVP API on any engine | **CONFIRMED** | Yes (for RSVP shim) |
| 3 | AppleScript can attach an attendee object but never sends the invitation | **CONFIRMED** | Yes |
| 4 | Claude Desktop / Codex Desktop deny EventKit synchronously with no consent prompt | **CONFIRMED** | Yes, this is the load-bearing claim |
| 5 | AppleScript rides a separate TCC category (Automation) from EventKit's Calendars category, so it survives inside `.mcpb` | **CONFIRMED** | Yes, paired with #4 |
| 6 | EventKit reads are dramatically faster than AppleScript `whose` reads on Calendar | **CONFIRMED (direction)** / citation needs correction | No, but the number in the plan is wrong |
| 7 | AppleScript recurring-series window predicates miss occurrences whose series started before the window | **CONFIRMED** | Yes, for the recurring-master-pass design |
| 8 | No native free-busy query API exists on macOS on either engine | **CONFIRMED** | Yes, for `check_availability` design |
| 9 | `EKEvent`/`EKCalendarItem.timeZone` is a real, writable per-event property | **CONFIRMED** | No (forward-queue item, not blocking) |
| 10 | JXA ObjC bridge hits the same Calendars TCC category as PyObjC; does not escape Desktop denial | **CONFIRMED** | No (rules out an alternative, doesn't change the chosen one) |
| 11 | "This machine has no resolved Calendar TCC grant on any path" (RR 5, Open risk 1) | **REFUTED (stale)** | No, but changes Open risk 1 and the live-verification precondition |
| 12 | `authorizationStatusForEntityType_` is a safe, synchronous, non-prompting read; never risks the hang that `requestFullAccessToEventsWithCompletion_` can | **CONFIRMED** | Yes, for the "auto" engine-selection safety property |

---

## 1. EventKit `attendees` is read-only

**Claim in plan/RR:** "EventKit's `attendees` array and `EKParticipant.participantStatus` are read-only per Apple's own documentation" (plan section 1, RR 4.2 and 4.3), used to justify shipping attendee attachment only through AppleScript, gated and documented as non-guaranteed.

**Verdict: CONFIRMED**, and more strongly than the plan states.

- Primary source, SDK header (`EKCalendarItem.h`):
  `@property(nonatomic, readonly, nullable) NSArray<__kindof EKParticipant *> *attendees;`
  (https://github.com/mstg/iOS-full-sdk/blob/master/iPhoneOS9.3.sdk/System/Library/Frameworks/EventKit.framework/Headers/EKCalendarItem.h)
- Apple Developer Forums thread 74209, "Unable to add EKParticipant to an event": confirms EventKit cannot add or mutate participants.
  https://developer.apple.com/forums/thread/74209
- Live probe (read-only, `EKCalendarItem.instancesRespondToSelector_`): `setAttendees:` **does** exist on `EKEvent` at the Objective-C runtime level (`responds = True`), which at first looked like a refutation. A follow-up probe called it directly on a transient, unsaved `EKEvent` (never passed to `EKEventStore.save`, so nothing was persisted): the call raised no exception, but `attendees` read back `None` afterward, i.e. the private setter is a no-op with respect to the observable property. This is a private/internal implementation detail (likely used by the framework's own CalDAV/Exchange sync layer to populate the object), not a supported mutation path, and it does not move the public property. The finding reinforces the "no public API to set attendees" claim rather than weakening it: even an out-of-band call to the private selector does not work.

## 2. `EKParticipant.participantStatus` is read-only; no RSVP API on any engine

**Verdict: CONFIRMED.**

- SDK header (`EKParticipant.h`): `@property(nonatomic, readonly) EKParticipantStatus participantStatus;`
  (https://github.com/phracker/MacOSX-SDKs/blob/master/MacOSX10.9.sdk/System/Library/Frameworks/EventKit.framework/Versions/A/Headers/EKParticipant.h)
- Apple's current docs page for the same property: https://developer.apple.com/documentation/eventkit/ekparticipant/participantstatus
- Live probe: `EKParticipant.instancesRespondToSelector_('setParticipantStatus:')` returned **False** (unlike the `attendees` case, there is no private setter selector at all here), while the getter selector responds `True`. This is a cleaner, unambiguous confirmation than claim 1's.
- AppleScript exposes `participation status` as a readable-only property in the Calendar Scripting Guide (RR 4.5), with no scriptable verb to change one's own RSVP. Combined with the EventKit finding, this supports the plan's `respond_to_invitation` documented-refusal shim (plan 3.10) as the correct ship decision, not a placeholder for a feature that is actually reachable.

## 3. AppleScript can attach an attendee object but never sends the invitation

**Verdict: CONFIRMED**, primary sources checked directly (not just RR's summary).

- MacScripter thread 48665: the original poster asks whether AppleScript can trigger Calendar's native "Send" button for an invitation; the only answer offered is a workaround that hand-builds the email through Mail.app. No one in the thread demonstrates a way to invoke the native send. https://www.macscripter.net/t/how-to-send-an-ical-events-attendee-their-invitation/48665
- Apple Developer Forums thread 681057: user follows Apple's own Calendar Scripting Guide `make new attendee at end of attendees` recipe and reports explicitly, "this adds the attendee to the event but doesn't send an invite to the attendee." The thread has zero replies. https://developer.apple.com/forums/thread/681057
- This directly supports the plan's decision to gate attendee attachment as an outward-facing, explicitly confirmed action with `"invitation_delivery": "platform_dependent"` in the response (plan 3.5), rather than promising delivery.

## 4. Claude Desktop / Codex Desktop deny EventKit synchronously with no consent prompt

**Claim:** the single most load-bearing claim in the plan. If EventKit actually worked fine inside Claude Desktop's `.mcpb` surface, the entire "AppleScript is the guaranteed baseline, EventKit is an opt-in fast path" architecture in plan section 2 would need to be rethought (EventKit could plausibly become primary, since it is faster and its writes are more capable).

**Verdict: CONFIRMED**, with primary-source evidence current within the last week.

Both GitHub issues were fetched via `gh issue view --json body,comments` (not summarized secondhand), because `gh` is the correct tool for GitHub content per this environment's tooling guidance and because the two RR citations turned out to have materially more nuance than a title-only read would suggest:

- **`anthropics/claude-code#63032`**: filed 2026-05-28, documents `che-ical-mcp` denied in 44 ms inside Claude Desktop with no prompt, root-caused to Claude.app's `Info.plist` declaring zero `NSCalendars*`/`NSReminders*` usage strings, with the request attributed to Claude.app via its `disclaimer` helper (responsible-process attribution), confirmed via `codesign -d --entitlements` showing no `com.apple.security.personal-information.calendars` entitlement either. **The issue itself was auto-closed 2026-05-31 by a triage bot** as "doesn't appear to be about Claude Code" (it is filed against the wrong repo class, Desktop vs CLI, and is currently labeled `invalid`), but a 2026-06-11 comment reconfirms the bug on Claude Desktop 1.11847.5 with the same missing-string evidence, and cross-references a companion issue (`#58239`).
- **`anthropics/claude-code#58239`** (the cross-referenced companion, also checked directly): a long, evolving thread with a regression timeline (94 successful EventKit writes 2026-01-15 through 2026-04-29, then 10 straight `Calendar access denied` failures starting 2026-05-11, correlated to a Claude Desktop 1.6608.2 update that introduced/changed the `disclaimer` wrapper's TCC attribution). Retested and reconfirmed on 1.7196.3 (2026-05-20) and 1.8089.1 (2026-05-20, with a partial fix noted: Reminders access recovered, Calendar access did not). **Most importantly, the thread's most recent comment is dated 2026-07-03, one week before this verification run**, and states plainly: "The core issue this thread tracks is unchanged: Claude.app still ships no `NSCalendarsFullAccessUsageDescription` / `NSRemindersFullAccessUsageDescription`, so the OS cannot render a Calendar/Reminders prompt for an EventKit request attributed to Claude.app." That comment also notes the MCP-server side has since shipped a foreground out-of-band grant workaround (a separate setup binary that runs outside Claude.app's attribution chain), which is independent confirmation that the underlying block is structural and unresolved, not something server-side code can route around from inside Claude Desktop.
- **`openai/codex#21228`**: filed with `Codex Desktop 26.429.61741`, reproduces the identical failure pattern for `rem-cli`/`ical` (Calendar) and `remi` (Reminders), confirms `tccutil reset` does not help, confirms Codex.app's `Info.plist` lacks `NSCalendarsUsageDescription`/`NSCalendarsFullAccessUsageDescription`, and has a comment from `ppeirce` reproducing the same failure on a materially newer build (`26.609.41114`), i.e. still current. Issue remains open (`state: OPEN` via `gh issue view --json state`).

This is a well-corroborated, still-open, still-reproducing platform limitation as of the most recent evidence (one week before this run). The plan's core architecture bet is sound.

## 5. AppleScript rides a separate TCC category from EventKit's, and survives inside `.mcpb`

**Verdict: CONFIRMED**, via three independent lines of evidence:

1. **Live differential behavior on this machine** (RR section 5, reconfirmed live in this run, see claim 12): AppleScript-to-Calendar and AppleScript-to-Finder (Automation) both hang identically for the same process ancestry, while AppleScript-to-System-Events (Accessibility) fails fast with `-25211`, and EventKit reports a separate, independently-tracked status. Three distinguishable outcomes for three distinct TCC buckets on the same host, same moment, is strong behavioral proof the buckets are separate.
2. **Entitlement-key evidence from the GitHub issues in claim 4**: Codex.app's entitlements list shows `com.apple.security.automation.apple-events = true` present while `com.apple.security.personal-information.calendars`/`.reminders` are absent (these are documented as two different entitlement keys gating two different frameworks, Apple Events vs EventKit, not one).
3. **This repo's own `core/applescript.py`** already exercises the Automation category successfully for Mail on every install surface including `.mcpb` (verified by reading `plugin/apple_mail_mcp/core/applescript.py`: stdin `osascript -`, default 120 s timeout, single-flight `_MAIL_LOCK`, exactly as the plan describes), which is direct existence proof that Automation-based AppleScript is not blocked the way EventKit is on the Desktop surface.

## 6. EventKit reads are dramatically faster than AppleScript `whose` reads (perf claim, citation correction needed)

**Verdict: CONFIRMED in direction; the specific number quoted in the plan is misattributed and should be corrected before it ships in docs.**

- Dr. Drang's AppleSloth benchmark (https://leancrew.com/all-this/2020/03/applesloth/, fetched directly) is the actual Calendar-specific, timed benchmark: `every event whose start date >= (current date)` on Calendar.app took **~61 s (Script Editor)**, **112 s (Script Debugger)**, **85 s (Terminal `osascript`)**, **85 s (standalone app)**, all on a 2012 iMac, versus a Python/EventKit equivalent described only as "essentially instantaneous" (no absolute number given) and an iOS Shortcut at "under 2 s." This part of RR 4.6 and plan section 2 is solid and directly sourced.
- **The plan's specific "0.13 s" / "462x" figure is not from this benchmark.** Tracing it to its actual source (`rem.sidv.dev/docs/architecture/`, fetched directly): that number is from a **Reminders** benchmark ("For 224 reminders with 11 properties... Result: 42-60 seconds" for JXA vs "0.13 seconds" for EventKit, "462x speedup"), not a Calendar-events benchmark. The RR text (section 4.6) already conflates the two ("JXA read layer 42 to 60 s dropped to 0.13 s (about 462x)") in the same sentence as the Calendar-specific numbers, and the plan's section 2 repeats the mixed figure as if it were one Calendar measurement ("0.13 s vs 60 to 112 s on community benchmarks").
- The Calendar-specific multiplier claim, from `ical.sidv.dev` (fetched directly): "roughly 3000× slower for large calendars" for AppleScript vs EventKit, described as qualitative with **no absolute timing number attached**.
- **Net effect on the engine decision: none.** Every source, Calendar-specific or Reminders-specific, agrees on the same qualitative conclusion (AppleScript's per-property Apple Events cost is orders of magnitude slower than EventKit's in-process access), and the order-of-magnitude gap is real and well corroborated even without the exact figure. But "0.13 s" should not be quoted in shipped docs as a Calendar number; recommend either dropping the specific number in favor of the sourced ranges (61-112 s AppleScript, qualitative "essentially instantaneous"/"~3000x" for EventKit) or explicitly labeling it as a Reminders figure if kept as an illustrative aside.

## 7. AppleScript recurring-series window predicates miss occurrences whose series started earlier

**Verdict: CONFIRMED**, community-corroborated technical behavior that justifies the recurring-master-pass subsystem (`calendar_core/recurrence.py`, `RECURRING_LOOKBACK_DAYS`).

Community sources agree Calendar.app AppleScript exposes only the start date of the first occurrence in a series plus the RFC 2445 recurrence string; date-range `whose` predicates filter on that single stored start date, so any series whose first occurrence predates the query window is excluded even if it has occurrences inside the window. Multiple independent MacScripter/Apple Community threads describe the same workaround (fetch all recurring masters, then project occurrences forward in code), which is exactly the design the plan adopts (bounded recurring-master second pass + Python RRULE expansion, plan section 3.2 and 8, RR section 7.4).
(https://discussions.apple.com/thread/328378, https://www.macscripter.net/t/applescript-and-outlook-cant-get-all-occurrences-of-repeating-event/67863, https://www.macscripter.net/t/find-recurring-event-that-has-an-item-within-date-range/76198)

No live probe was attempted for this one: reproducing it would require creating a real recurring event on this machine, which the read-only/no-side-effects constraint for this verification pass rules out, and AppleScript-to-Calendar is currently hung/blocked here regardless (claim 11/12).

## 8. No native free-busy query API exists on macOS on either engine

**Verdict: CONFIRMED**, and now verified directly against the live `EKEventStore` API surface rather than only against a docs page.

- Live probe fetched the full `EKEventStore` topics list from Apple's docs JSON endpoint (`developer.apple.com/tutorials/data/documentation/eventkit/ekeventstore.json`) and confirmed: no `fetchFreeBusy`, `requestAvailability`, or similarly named method exists anywhere in the class's public surface. The only date-range query primitives are `predicateForEvents(withStart:end:calendars:)` and the Reminders equivalents.
- Live PyObjC probe independently confirmed the same absence at the Objective-C runtime level: `EKEventStore.instancesRespondToSelector_` returned `False` for `fetchFreeBusy:`, `requestAvailability:`, and `freeBusyForCalendars:`, and `True` for `predicateForEventsWithStartDate:endDate:calendars:`.
- Apple's docs for `EKEvent.availability` (fetched directly): the property "indicates how the event should be treated for scheduling purposes by CalDAV and Exchange servers," i.e. it is a per-event scheduling hint the calendar server may use, not a query API a caller can invoke across a window. This confirms `check_availability`'s design (plan 3.4: fetch the bounded window, fold busy/free intervals in Python) is the only available approach on either engine, not a missed shortcut.

## 9. `EKEvent`/`EKCalendarItem.timeZone` is a real, writable per-event property

**Verdict: CONFIRMED**, used by the plan only as a forward-queue item (documented, not shipped in 3.10.0), so this is lower stakes than claims 1-8 but worth confirming since it is the stated justification for deferring true per-event stored timezones to a later EventKit write engine.

- Apple's docs (fetched directly): `var timeZone: TimeZone? { get set }` (Swift), `@property (nonatomic, copy, nullable) NSTimeZone *timeZone;` (Objective-C), inherited from `EKCalendarItem`, with the documented floating-event semantics (nil = not tied to a zone) matching how the plan describes the AppleScript engine's own host-local wall-clock behavior as the fallback.
- Live probe: `EKEvent.instancesRespondToSelector_('setTimeZone:')` returned `True`, confirming the setter actually exists (not just documented) on `EKEvent` instances.
- One nuance for the record, not a refutation: `NSObject`'s default `-description` on a freshly constructed transient `EKEvent` printed `startTimeZone`/`endTimeZone` keys (internal instance-variable names visible only via debug description), which are not the public `timeZone` property the plan and Apple's docs describe. This is an internal implementation detail of the concrete class, not a second public API; the plan should keep citing only the documented `timeZone` property.

## 10. JXA ObjC bridge hits the same Calendars TCC category as PyObjC; does not escape Desktop denial

**Verdict: CONFIRMED**, live-reproduced independently of RR's own probe.

`osascript -l JavaScript` with `ObjC.import('EventKit')` calling `EKEventStore.authorizationStatusForEntityType($.EKEntityTypeEvent)` returned the same status code as the PyObjC probe running in the same process ancestry (see claim 12 for the actual value and its significance), executed in 0.07 s. This matches RR 4.4's community citations (galvanist.com, scriptingosx.com) that JXA's ObjC bridge reaches the real EventKit framework and is therefore gated by the real Calendars TCC category, i.e. it trades the PyObjC dependency for bridge glue without escaping the Desktop-surface denial documented in claim 4. This does not change the engine decision (JXA was never a contender in the plan; this only confirms it correctly stays out of scope), but it is worth keeping in the platform-report record since it forecloses a plausible "avoid the pip dependency" alternative someone might propose later.

## 11. "This machine has no resolved Calendar TCC grant on any path" (RR section 5 / plan Open risk 1)

**Verdict: REFUTED, as currently stated. This is the most important live finding in this pass and should update the plan before Wave 9.**

RR section 5 and plan Open risk 1 both assert that, as of the phase-1 probe, both the Automation grant (AppleScript-to-Calendar) and the Calendars full-access grant (EventKit) were unresolved on this machine, blocking any live verification. A fresh, independent live probe run in this session (same day, several hours later, same process ancestry class: Terminal.app to zsh to `claude` to the probe script) found:

```
Named constants: notDetermined=0 restricted=1 denied=2 fullAccess=3 writeOnly=4
Current process authorizationStatusForEntityType_(Event)    = 3   (fullAccess)
Current process authorizationStatusForEntityType_(Reminder) = 0   (notDetermined)
```

This was cross-checked against the framework's own named enum constants (not just the raw integer) to remove any ambiguity about which status the value `3` represents, and the process-ancestry chain was printed and matches the pattern RR itself used ("Terminal.app -> login -> zsh -> claude -> zsh").

**So: EventKit Calendars full access for the Events entity type is already granted on this host for terminal-launched processes.** Reminders access is not (still `notDetermined`). This most likely happened because RR's own probe (section 5.1) fired a real `requestFullAccessToEventsWithCompletion_` call; the completion handler had not fired within RR's 20 s harness cutoff, so RR correctly reported it as "pending, callback never fired" at the time, but the system consent dialog most plausibly remained live (or queued) after that script exited, and was answered by the console user afterward, permanently granting access for that responsible-process class.

**Automation is unaffected and still blocked**: a second live probe in this same session, reading `name of calendars` via `tell application "Calendar"` through the exact `osascript -` invocation this repo's `run_applescript` uses, hung to a 20 s timeout with no output, identical to RR's own finding. So the write path (which the plan correctly keeps 100% on AppleScript in 3.10.0) is still blocked pending a human answering the Automation prompt; only the EventKit read fast path has quietly become available on this specific host in the interim.

**Recommendation, not a plan-architecture change:** before running the Wave 9 live-verification protocol, re-run the precondition check in plan section 9 rather than assuming both grants are still unresolved. The `calendar-grant` CLI step may turn out to be unnecessary for the Events entity type on this exact host (though it would still be needed for Reminders if that entity type is ever added, and needed on any other host or account). The Automation-prompt step (running `.venv/bin/apple-mail calendars` and answering the prompt) is still required and still the actual blocker for any write testing.

## 12. `authorizationStatusForEntityType_` is a safe, synchronous, non-prompting read

**Verdict: CONFIRMED.** This underwrites a specific safety property in the plan's engine-selection design (plan section 2.1, item 2): "Under `auto`: use the EventKit read engine iff `EventKit` is importable and `EKEventStore.authorizationStatusForEntityType_(EKEntityTypeEvent)` already reports full access... No tool call ever invokes `requestFullAccessToEvents...`." If this status check itself could hang or trigger a prompt, that design would be unsafe to run inside a tool call.

- Live probe: the call completed in **1.039 s** (dominated by Python/PyObjC framework import time, not by the call itself; the call and everything around it stayed well inside the 20 s internal `SIGALRM` safety net used for every probe in this run) and returned deterministically with no dialog, no hang, and no side effect, both via PyObjC and via the JXA ObjC bridge (0.071 s, matching RR's own JXA timing).
- Apple's documentation and the RR's own citations describe `authorizationStatus(for:)` as a synchronous status read and `requestFullAccessToEvents(completion:)`/`requestWriteOnlyAccessToEvents(completion:)` as the separate, async, prompting calls (confirmed in the `EKEventStore` topics extraction under claim 8: "Requesting access to events and reminders" is a distinct topic section from anything status-only). This matches the plan's separation exactly: read status freely, never call `request*`.

---

## Supporting corroboration (not top-12, but checked)

- **Dependency weight** (plan 2.2, RR 5.1): "measured install 1.6 s / a few MB" and a 43 MB full venv with `pyobjc-core`/`pyobjc-framework-Cocoa`/`pyobjc-framework-EventKit` all at `12.2.1`. Independently reproduced in this run's scratch venv: `du -sh verify-venv` = **43M**, `pip list` shows the identical three packages at the identical version `12.2.1`. Confirmed exactly, no drift since RR's probe.
- **`core.run_applescript` implementation** (plan section 3.1, RR 3.1): read directly from `plugin/apple_mail_mcp/core/applescript.py` in this run. Confirms stdin `["osascript", "-"]` invocation (never `-e`), `effective_timeout = 120 if timeout is None else timeout` default, and the `_MAIL_LOCK` single-flight lock, exactly as both documents describe. No drift.

---

## What would need to change in the plan

1. **Correct the "0.13 s" performance citation** (claim 6) wherever it appears in shipped docs/skills: either cite the Calendar-specific, timed AppleSloth numbers (61-112 s AppleScript vs an untimed "essentially instantaneous" for Python/EventKit) or the Calendar-specific qualitative "roughly 3000x" claim, and stop presenting the Reminders-sourced "0.13 s / 462x" figure as a Calendar measurement. This is a docs-accuracy fix, not an architecture fix.
2. **Update RR section 5 and plan Open risk 1** to reflect that the EventKit Calendars full-access grant for the Events entity type is no longer unresolved on this specific dev machine (Reminders still is; Automation still is). Re-run the live-verification precondition check in plan section 9 before assuming both grants still need a fresh human answer; only the Automation prompt clearly still does.
3. No change is needed to the engine architecture decision itself (plan section 2): every claim that could have overturned it (EventKit availability inside Desktop, attendee/RSVP capability, free-busy existence, recurring-window correctness) came back **CONFIRMED** against fresh, current (within the last one to two weeks) primary sources.
