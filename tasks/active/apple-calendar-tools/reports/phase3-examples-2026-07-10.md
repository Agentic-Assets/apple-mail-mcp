# Phase 3 report: comparative research on other real-world Apple Calendar automation implementations

Scope: everything outside this repo's own reference-skill teardown
([`phase1-reference-skill-2026-07-10.md`](phase1-reference-skill-2026-07-10.md)) and platform-API
research ([`phase1-platform-apis-2026-07-10.md`](phase1-platform-apis-2026-07-10.md)). This report
hunts for other MCP servers, CLIs, and app-extension implementations of macOS Calendar
automation, reads their source and issue trackers, and pulls concrete, citable lessons
for the plan at [`../plan-2026-07-10.md`](../plan-2026-07-10.md).

Method: GitHub and web search for the categories in the task brief, then `WebFetch` on
each README plus `gh api`/`gh issue view` against each project's live issue tracker,
filtered for the specific pitfall classes the plan already worries about (slow, hang,
attendee, invite, timezone, permission). All dates and version numbers below are quoted
directly from the sources; several are from mid-2026, after this plan's own live-probe
window, so some findings postdate and sharpen RR (research-report) claims.

---

## 1. MCP servers (Apple Calendar via AppleScript, EventKit, or a hybrid)

### 1.1 `Dhravya/apple-mcp` (now `supermemoryai/apple-mcp`)

<https://github.com/Dhravya/apple-mcp> (redirects to `supermemoryai/apple-mcp`) ·
`tools.ts`: <https://github.com/Dhravya/apple-mcp/blob/main/tools.ts>

- **Engine**: JXA/AppleScript only (Bun runtime, `osascript`-style automation), no EventKit.
- **Tool surface**: one multiplexed `CALENDAR_TOOL` with an `operation` field:
  `search`, `open`, `list`, `create`. `search`/`list` take `searchText`, `limit`
  (default 10), `fromDate`/`toDate` (ISO, default today .. +30 days for search, +7 days
  for list). `create` takes `title`, `startDate`, `endDate`, `location`, `notes`,
  `isAllDay`, `calendarName`.
- **Attendees/invites**: none. No parameter exists for attendees anywhere in the schema.
- **Timezone**: implicit only. ISO date strings are accepted with no explicit timezone
  parameter or documented conversion behavior, i.e. exactly the failure mode requirement
  7 of this plan exists to close.
- **Auth/TCC**: relies on the Automation (Apple Events) grant like the rest of this
  repo's Mail tools; no EventKit path to complicate it.
- **Issues found** (`gh api search/issues?q=repo:supermemoryai/apple-mcp`):
  - [#74 "Can't find any calendar entries"](https://github.com/supermemoryai/apple-mcp/issues/74)
    and [#70 "Not able to get reminders, calendar, mail working"](https://github.com/supermemoryai/apple-mcp/issues/70)
    (open): generic permission/visibility failures with no diagnostic surfaced back to
    the user, i.e. no structured error code, just silence.
  - [#25 "Not seeing all events in my calendar"](https://github.com/supermemoryai/apple-mcp/issues/25)
    (closed, unresolved in thread): the user's multi-calendar setup only returns events
    from one calendar; no fan-out across all calendars was implemented.
  - [#53 "Listing Reminders causes 'Request timed out' error"](https://github.com/supermemoryai/apple-mcp/issues/53)
    (closed): confirms unbounded/slow AppleScript calls against a large personal
    database hit MCP host-side timeouts with zero diagnostic beyond "Request timed out".
  - [#6 "Calendar integration"](https://github.com/supermemoryai/apple-mcp/issues/6) and
    [#21 "Add calendar tool integration..."](https://github.com/supermemoryai/apple-mcp/pull/21)
    (closed): the original feature PR, worth reading as the minimal-viable-tool baseline
    this plan is deliberately going well beyond.

### 1.2 `griches/apple-mcp`

<https://github.com/griches/apple-mcp>

- **Engine**: explicit hybrid, stated in the README itself: "EventKit, fast, reliable,
  and no dependency on the Calendar app being open, for reads. Write operations
  (create, update, delete) use AppleScript." This is the closest published precedent to
  this plan's section 2 engine decision, and its issue tracker is the single most
  important source in this report.
- **Tool surface**: `list_calendars`, `list_all_events`, `list_events`, `get_event`,
  `search_events` (reads); `create_event`, `update_event`, `delete_event` (writes, via
  AppleScript).
- **Attendees/invites**: not documented; absence from the tool list implies unsupported.
- **The core finding**, [issue #10 "apple-calendar: reads fail with 'Calendar access
  denied' under MCP hosts (EventKit prompt never shows)"](https://github.com/griches/apple-mcp/issues/10)
  (open): under Claude Desktop or Claude Code as the spawning host, EventKit reads
  **always** fail with `Calendar access denied`, **no permission dialog ever appears**,
  and the host app never even shows up in System Settings -> Privacy & Security ->
  Calendars for the user to grant. Root cause, diagnosed by the reporter via
  `codesign -dv`: `EKEventStore.requestFullAccessToEvents` on macOS 14+ only presents
  the consent prompt if the *responsible app bundle* declares
  `NSCalendarsFullAccessUsageDescription`; here **neither side** declares it (the bundled
  `calendar-helper` binary is `adhoc, linker-signed` with `Info.plist=not bound`, and the
  MCP host itself lacks the usage string). Result: `requestAccess` returns `denied`
  synchronously, `notDetermined` never flips, and there is no user-visible path to grant
  access at all. Reminders in the same server sidesteps this because it goes through
  AppleScript/Automation instead of EventKit, and Automation prompts work fine for these
  same hosts. This is a direct, dated (2026), first-party confirmation of RR section 4.4
  and this plan's section 2 rationale for AppleScript being the only guaranteed engine.
  The reporter's **suggested and locally-implemented fix** is exactly this plan's
  architecture: "keep EventKit as the fast path and fall back to AppleScript when
  EventKit access is denied." They documented two AppleScript-fallback caveats worth
  carrying into this plan's recurrence handling: recurring events report the *series'*
  start date rather than the in-range occurrence (matches RR 4.6/7.4, i.e. why this plan
  does a separate Python-side recurrence expansion pass), and querying across all
  calendars is slower and can produce duplicate rows for recurring events that must be
  deduplicated client-side.
  - Companion comment on the same issue: a user reports they could force the permission
    prompt to appear by asking the LLM to *create* an event rather than list/find one,
    i.e. write-triggered EventKit calls sometimes succeed in raising the prompt where
    read-triggered calls silently fail. Anecdotal, but consistent with write paths
    invoking a different EventKit code path than read predicates.
  - [Issue #6 "Unable to trigger Calendars permissions"](https://github.com/griches/apple-mcp/issues/6)
    (open): a second, independent report of the same wall, plus a sharp diagnosis of
    *why* signing/entitlement fixes are hard for `npx`-distributed servers: `npm exec`
    spawns a nested `node` binary buried inside the npm cache directory
    (`~/.npm/_npx/<hash>/node_modules/.bin/...`), and macOS TCC binds permissions to
    signed binary identities, not to scripts, symlinks, or the parent app. Dragging the
    parent app, Terminal, or an alias into System Settings does nothing because the
    actual executing binary (the cached `node`) is never the thing macOS is tracking.

### 1.3 `EgorKurito/apple-calendar-mcp`

<https://github.com/EgorKurito/apple-calendar-mcp>

- **Architecture**: three layers, Claude Code -> TypeScript MCP server (Zod-validated,
  JSON-RPC/stdio) -> Swift CLI binary (`apple-bridge`) -> EventKit. No AppleScript
  anywhere; this is a "go all-in on EventKit, ship a compiled helper" design, the exact
  approach this plan's section 2 explicitly declines for 3.10.0.
- **Rationale stated in their own README** (direct quote via WebFetch): "AppleScript-based
  calendar integrations fail to correctly expand recurring events, they return the
  master event instead of individual occurrences." They use `predicateForEvents` to
  expand occurrences natively, avoiding this plan's need for a Python-side recurrence
  expander, at the cost of needing a Swift toolchain and shipping a compiled artifact.
- **Tools**: `get_calendars`, `get_events`, `get_today_events`, `search_events`,
  `create_event`, `delete_event` (six total; no update tool, no attendees).
- **Auth/TCC**: ships a `doctor` command that prompts for Full Access grants, and
  documents `tccutil reset Calendar` as the recovery path when a prior denial has stuck.
  No mention of the disclaim technique in section 1.4 below, i.e. it inherits the same
  responsible-process fragility as `griches/apple-mcp`, just without a public issue
  reproducing it (repo shows 0 open issues at time of writing, likely low install base
  rather than absence of the bug).
- **Attendees/invites**: not documented; absent from the tool list.

### 1.4 `FradSer/mcp-server-apple-events` and its vendored `FradSer/event` Swift CLI

<https://github.com/FradSer/mcp-server-apple-events> ·
<https://github.com/FradSer/event>

This is the deepest and most consequential source in this report: a live, multi-month
public engineering saga (issues #1, #26, #38, #41, #71, #77, #80, #83, #93, #108, PRs
#39, #40, #67, #77, #79, #80, #82, #98, #102, #104, #110) working through almost every
failure mode this plan's section 2 and section 10 anticipate, on a shipping npm package
(`mcp-server-apple-events`, MIT, v1.5.0+) that covers both Reminders and Calendar via
EventKit with full CRUD.

- **Engine**: EventKit only, via a standalone Swift CLI (`event`) vendored as a git
  submodule and built to `bin/event` at `pnpm install` time. The published npm package
  ships a pre-built, universal, **code-signed** `bin/event` binary so `npx` users need no
  Xcode/Swift toolchain at all.
- **Tool surface**: two service-scoped, action-multiplexed tools,
  `calendar_events` (`read`/`create`/`update`/`delete`, all-day inferred from date
  format, `span` parameter scopes recurring deletes, cross-calendar moves unsupported)
  and `calendar_calendars` (`read`, restricted to calendars holding >=1 event in an
  optional window). This mirrors this plan's `manage_calendars`/`delete_events` `span`
  and multiplex design closely.
- **Attendees/invites**: not exposed as a write capability. The README explicitly lists
  **alarms, recurrence rules, location triggers, and cross-calendar moves as read-only
  via this server**, i.e. they round-trip from values set in Calendar.app/Reminders.app
  but the vendored `event` CLI does not yet expose write access to them (tracked as a
  CLI-level gap, not an EventKit-level one; contrast with this plan's section 2.3, which
  correctly notes EventKit itself *can* write these fields; this project just has not
  wired that surface up yet).
- **The TCC saga, condensed** (all quotes from the linked issues, `gh issue view`):
  1. [#77, "fix(swift): enable hardened runtime for macOS 26 calendar TCC dialogs"](https://github.com/FradSer/mcp-server-apple-events/pull/77)
     (merged): "macOS 26 requires CLI binaries to be code-signed with hardened runtime
     (`--options runtime`) for the TCC system to show calendar permission dialogs.
     Without it, `requestFullAccessToEvents()` silently returns `granted=false` with no
     error, and no dialog is shown." This is a distinct, additional failure mode from
     the responsible-process problem in section 1.2: **even an entitled, correctly
     attributed caller gets a silent, dialog-free denial on macOS 26 if the binary
     itself lacks hardened-runtime signing.**
  2. [#93, "Codex Desktop cannot prompt for Reminders/Calendar permissions..."](https://github.com/FradSer/mcp-server-apple-events/issues/93)
     (closed, now the canonical reference issue, still pinned open by the maintainer "as
     several other users will hit the same wall"): a rigorous TCC-log-backed
     investigation showing macOS attributes the EventKit request to the *responsible*
     process at spawn time (`com.openai.codex`/`com.anthropic.claudefordesktop`, not the
     Swift helper or `node`), and refuses the request outright when that responsible app
     lacks `NSRemindersUsageDescription`/`NSCalendarsUsageDescription` in its own
     `Info.plist`, regardless of what the child binary declares. Multiple dead-end fixes
     were tried and rejected in the thread: re-signing Homebrew `node` with calendar
     entitlements (doesn't help, TCC still points at the desktop app, not `node`);
     pre-prompting once via a plain `osascript -e 'tell application "Calendar" to get
     name of calendars'` from inside the host (initially proposed as a workaround, later
     **retracted by the maintainer as overstated**: it only grants `kTCCServiceAppleEvents`
     [Automation], never `kTCCServiceCalendar`/`kTCCServiceReminders`, so it does not
     actually fix anything for a server that never itself calls `tell application`).
  3. **The actual, shipped fix** ([PR #110](https://github.com/FradSer/mcp-server-apple-events/pull/110),
     merged 2026-07-06, i.e. days before this report): `bin/event` is now launched
     through a small `bin/event-disclaim` shim compiled from `scripts/disclaim.c`, which
     re-execs `event` via `posix_spawn` + `POSIX_SPAWN_SETEXEC` + the private
     `responsibility_spawnattrs_setdisclaim` API, breaking the TCC responsibility chain
     at spawn time so `event` becomes its **own** TCC-responsible process. The
     maintainer's own words: this is "the same mechanism Chromium, LLDB, and Claude
     Desktop's own `Helpers/disclaimer` binary use." Combined with an embedded
     `Info.plist` (injected via `-sectcreate __TEXT __info_plist`, all six
     Reminders/Calendar usage strings, stable bundle id `me.frad.event`) and hardened
     runtime signing with `com.apple.security.personal-information.{calendars,reminders}`
     entitlements, the EventKit prompt now appears (attributed to `event`, not the host)
     **no matter which client launched the server**, and **one approval covers every MCP
     client on the machine**. This is a direct, working, dated existence proof against
     this plan's RR 4.4 framing that "a signed, notarized, self-disclaiming Swift helper
     ... is out of scope for a pure-Python release" being effectively unbuildable; it is
     buildable, it is open source, and someone else has already shipped it, though it
     does require Apple Developer signing and a compiled artifact this plan correctly
     defers past 3.10.0.
  4. A companion issue on the `event` CLI repo itself,
     [`FradSer/event#12`](https://github.com/FradSer/event/issues/12), documents that
     the disclaim/entitlement/signing logic currently lives entirely in the *consumer*
     repo's build pipeline (`scripts/build-event.mjs`) and is **not** available to
     someone building `event` standalone from source, i.e. even the reference
     implementation of this technique is not yet a drop-in library.
  - [Issue #108, "perf: by-id lookups do a full-scan over the event CLI"](https://github.com/FradSer/mcp-server-apple-events/issues/108)
    (open): both `findReminderById` and `findEventById` currently pull the **entire**
    result set (reminders: full list incl. completed; events: a +-4-year window, "capped
    at EventKit's 4-year predicate limit") and do an in-memory linear `.find(id)`,
    because the vendored `event` CLI has no `--id` filter at the shell level. Direct,
    dated confirmation that a UID-lookup tool without a bounded, indexed fetch path is
    the single heaviest operation such a server performs, exactly the anti-pattern RR
    2.4 flags in the reference skill and exactly why this plan's `get_events_by_id`
    (3.3) makes the calendar/window hint load-bearing rather than optional.
  - [Issue #71, "The Calendar permission dialog never appears on macOS 26"](https://github.com/FradSer/mcp-server-apple-events/issues/71)
    and [Issue #83, "Unable to access calendar from Claude Desktop"](https://github.com/FradSer/mcp-server-apple-events/issues/83)
    (both closed, folded into the #93/#77 fixes above): earlier, less-diagnosed reports
    of the same two failure classes.
  - [PR #80, "fix: parse date-only strings as local time instead of UTC"](https://github.com/FradSer/mcp-server-apple-events/pull/80)
    (merged): a date-only string (`2026-02-11`) fed to `new Date()` was parsed as UTC
    midnight; in any timezone behind UTC this silently shifted the effective date back
    one day, corrupting the default 14-day window in `findEvents`. Direct precedent for
    this plan's requirement 7 discipline (zoneinfo-based conversion in Python before any
    string ever reaches the engine, never trust an ambiguous date-only string).
  - [PR #98, "fix: reject malformed date inputs"](https://github.com/FradSer/mcp-server-apple-events/pull/98)
    and [PRs #39/#40, timezone-metadata and due-date-logic fixes for reminders](https://github.com/FradSer/mcp-server-apple-events/pull/39):
    further, independent timezone/date-validation bugs found and fixed over the
    project's life, i.e. this class of bug recurs even after the first fix lands.
  - [Issue #26, "execution error: Reminders got an error: AppleEvent timed out."](https://github.com/FradSer/mcp-server-apple-events/issues/26)
    (closed): confirms unbounded/slow AppleEvent calls can simply time out with no
    structured recovery, same class as apple-mcp#53 above.
  - [Andrew's attendee-feature planning issue](#15-andrewbergsmaapple-calendar-mcp)
    (next section) documents the AppleScript verbs for attendee *reading* that this
    plan's `list_events`/`get_events_by_id` attendee field already plans to use.

### 1.5 `andrewbergsma/apple-calendar-mcp`

<https://github.com/andrewbergsma/apple-calendar-mcp>

- **Engine**: FastMCP (Python), same framework family as this repo. Built with
  Claude-Desktop-first phased delivery (visible in its open issues, which are literally
  its own roadmap: Phase 2 Search, Phase 3 Create, Phase 4 Modify, Phase 5 Attendees).
- **[Issue #5, "Phase 5: Advanced Features (Attendees, Reminders, Analytics, Export)"](https://github.com/andrewbergsma/apple-calendar-mcp/issues/5)**
  (open, unimplemented): the planning doc for a `list_event_attendees` tool and an
  `invite_attendees` tool, with the exact AppleScript verbs it intends to use:
  ```applescript
  set attendeeList to attendees of targetEvent
  repeat with anAttendee in attendeeList
      set attendeeName to display name of anAttendee
      set attendeeEmail to email of anAttendee
      set attendeeStatus to participation status of anAttendee
  end repeat
  ```
  and, separately, for adding an alarm:
  ```applescript
  make new display alarm at end of display alarms of targetEvent with properties {trigger interval: -15}
  ```
  Confirms this plan's read-only attendee shape (`name`, `email`, `participation
  status`) is exactly what the scripting dictionary can hand back, and that this
  project, like every other one surveyed, has never actually shipped the write side
  (`invite_attendees`, described only as a plan, still open, no PR).
- **[Issue #6, "Question: iCalendar CLASS/CATEGORIES marker exposure..."](https://github.com/andrewbergsma/apple-calendar-mcp/issues/6)**
  (open): a downstream integrator building a privacy-tier aggregation pipeline reports
  that EventKit **silently drops** the iCalendar `CLASS`, `CATEGORIES`, and
  `X-MICROSOFT-CDO-IMPORTANCE` markers; EventKit exposes title, times, location, notes,
  attendees, and `availability`, but not those three. Minor for this plan (nothing in
  the tool surface currently promises them), but worth a one-line doc note if a future
  privacy-classification feature is ever proposed on top of these tools.

### 1.6 `androidStern-personal/openclaw-apple-calendar` (OpenClaw plugin, not an MCP
server, but same underlying pattern)

<https://github.com/androidStern-personal/openclaw-apple-calendar>

- **Engine**: a compiled Swift binary calling EventKit directly, replacing AppleScript
  entirely for both reads and writes (unlike `griches/apple-mcp`'s hybrid). The binary
  **auto-compiles on first use** via `swiftc -O` and caches itself, so there is no
  separate build step or npm-bundled artifact, at the cost of requiring a Swift
  toolchain present on the runtime machine.
- **Performance claims**: "Date-range queries use `predicateForEvents` (indexed), <100ms
  even with thousands of events" versus "30+ seconds with AppleScript" for the same
  query; UID lookups are O(1) via `store.event(withIdentifier:)`. These numbers roughly
  match RR 4.6's "0.13s vs 60-112s" community benchmark and reinforce this plan's
  decision to make EventKit the fast path wherever it is actually reachable.
- **Auth/TCC**: no disclaim shim, no embedded Info.plist trick. On first calendar tool
  call the compiled helper launches as a small `calendar-helper.app` bundle specifically
  so it can trigger the native permission modal (an app bundle, unlike a bare CLI, can
  carry the usage-description Info.plist keys needed to prompt). The docs are blunt
  about the consequence: "If you do not accept that modal, the plugin will keep
  returning permission denied," with no fallback path documented. Three execution modes
  (`auto`, `app`, `direct`) exist specifically to work around headless/CI environments
  where the app-bundle launch cannot show UI.
- **Tools**: `apple_calendar_list`, `apple_calendar_events`, `apple_calendar_read` (by
  UID), `apple_calendar_create`, `apple_calendar_update`, `apple_calendar_delete`,
  `apple_calendar_search`, seven total, no attendee support documented.

---

## 2. CLIs (icalBuddy lineage and modern EventKit replacements)

### 2.1 `icalBuddy` (`ali-rantakari/icalBuddy`)

<https://github.com/ali-rantakari/icalBuddy>

The read-only, twenty-year-old baseline every modern tool in this space explicitly
positions itself against. Objective-C, reads through the private Calendar database
layer (not the public EventKit authorization surface used by modern tools), MIT
licensed, "active development ended in 2014" per multiple downstream READMEs.

- **[Issue #41, "Does not request permissions on Sequoia"](https://github.com/ali-rantakari/icalBuddy/issues/41)**
  (open): a clean Sequoia install, tried across Terminal, Übersicht, GeekTool,
  Automator, and AppleScript, never triggers a permission prompt; stderr just says
  `error: No calendars.` A commenter's diagnosis, later confirmed by a maintainer of a
  successor project: Apple deprecated the `NSCalendarsUsageDescription` plist key in
  Sonoma in favor of the finer-grained `NSCalendarsFullAccessUsageDescription`, and
  icalBuddy (like most pre-Sonoma tools) still only declares, or lacks entirely, the old
  key, so the OS never surfaces a request at all. A reply links a from-scratch Swift/
  EventKit rewrite, `MartinGross/calbuddy`, built specifically to "solve the common 'No
  calendars' error on modern macOS by using Apple's EventKit framework, which correctly
  handles calendar permissions." This is independent, dated confirmation that a
  private-database or legacy-usage-string approach has a **hard expiration date** as
  macOS privacy requirements tighten, i.e. further support for this plan's choice to
  build only on public, documented AppleScript/EventKit surfaces.
- Other open issues of note: [#19 "error: No calendars. when running from
  AppleScript"](https://github.com/ali-rantakari/icalBuddy/issues/19) and
  [#5 "Using iCalBuddy in a cron process"](https://github.com/ali-rantakari/icalBuddy/issues/5),
  both circling the same permission-attribution problem in different host contexts
  (scripted vs. headless/launchd), reinforcing that this is a category of bug, not a
  one-off.

### 2.2 `icalPal` (`ajrosen/icalPal`)

<https://github.com/ajrosen/icalPal>

A Ruby rewrite explicitly positioned as icalBuddy's replacement, but architecturally the
**opposite** direction from every EventKit-based tool in this report: it reads
**directly from the Calendar and Reminders SQLite database files** rather than through
any public API, which the README frames as a feature ("platform-agnostic, works on
Linux/Windows where the database files are accessible") but which two of its own open
issues show is a serious liability on macOS itself:

- **[Issue #27, "icalPal doesn't work with macOS Sequoia"](https://github.com/ajrosen/icalPal/issues/27)**:
  a private database schema change on a macOS point release broke the tool outright.
- **[Issue #45, "Datetime fields are shifted when queried"](https://github.com/ajrosen/icalPal/issues/45)**
  (open): a recurring event on an Exchange-backed calendar returns start/end/recurrence
  timestamps that are wrong by an hour (`sctime`/`ectime`) and by a full week
  (`rctime`), i.e. the private DB's stored representation of recurring/cross-timezone
  events does not map cleanly to wall-clock time without logic the tool has not
  fully reproduced.
- **[Issue #35, "eventsNow is listing additional entries (ignoring timezones?)"](https://github.com/ajrosen/icalPal/issues/35)**
  and **[#28, "eventsNow does not display all current events"](https://github.com/ajrosen/icalPal/issues/28)**:
  further timezone-adjacent correctness bugs in the same "reimplement Apple's private
  time math yourself" territory.
- **[Issue #56, "tasks command: ...JOIN explodes result set ~600x and dominates
  cold-start time"](https://github.com/ajrosen/icalPal/issues/56)**: a specific,
  measured performance pathology from an unbounded join against the private schema.

Taken together, this is the single strongest piece of evidence in this report **against**
ever reading Calendar's underlying SQLite store directly, even as a fast path: the
private schema shifts under macOS updates (Sequoia broke it outright), and reproducing
Apple's own timezone/recurrence math against raw rows is a real, ongoing, unsolved
source of silent data corruption. This plan never proposes a DB-direct engine, and this
evidence is a clean argument for keeping it that way permanently, not just for 3.10.0.

### 2.3 `ical` / go-eventkit (`BRO3886/ical`, docs at `ical.sidv.dev`)

<https://github.com/BRO3886/ical> · <https://ical.sidv.dev/docs/getting-started/>

- **Engine**: `go-eventkit`, native EventKit bindings via cgo, single static binary, no
  subprocess/AppleScript dependency at all.
- **Performance claim**: "3000x faster than AppleScript" (unsourced in the README
  itself, but directionally consistent with the ~500-1000x factor implied by RR 4.6 and
  the openclaw-apple-calendar numbers in section 1.6 above).
- **Feature surface is the widest of anything surveyed**: full CRUD, natural-language
  date parsing ("tomorrow 9am", "next friday"), recurrence with span-aware
  update/delete (single/future/all, matching this plan's `span` parameter design almost
  exactly), **attendee invites on create**, **RSVP** (accepted/declined/tentative, i.e.
  the exact capability this plan's `respond_to_invitation` documents as unsupported on
  every engine considered here), free/busy lookup, and conference-link joining.
- **Two attendee/invite caveats worth carrying into the skill docs**: free/busy "only
  works with Exchange or Google Workspace; iCloud unsupported" (i.e. even a tool that
  claims free-busy support cannot deliver it on the account type this plan's owner most
  likely uses), and, more sharply, **"No dry-run for invites: sending invitations
  immediately triggers real email notifications."** This is a direct, dated warning from
  a shipping tool that attendee/invite writes are irreversible and side-effecting in a
  way this plan's own `send_invitations=True` gate and live-verification protocol
  (section 9.4, reserved-domain-only, delete-immediately) already treats with equivalent
  seriousness; it is good confirmation the caution is warranted, not excessive.
- **[Issue #34, "Unable to grant Calendar permission"](https://github.com/BRO3886/ical/issues/34)**
  (open, extensively investigated by the community over months): the single richest
  TCC thread found in this whole survey. Two **independent, additive** failure modes
  were isolated by different commenters and then unified by the maintainer:
  - **Mode A, responsible-process entitlement gap**: TCC attributes the request to the
    top-of-process-chain GUI app (`p_responsible_pid`). If that app is not signed with
    the `com.apple.security.personal-information.calendars` entitlement plus a usage
    description, `tccd` disallows the prompt outright before EventKit is ever reached.
    A live comparison table shows Terminal.app, iTerm2, Zed, and kitty all carry the
    entitlement and work; VS Code's integrated terminal does not and fails, confirmed
    against a captured TCC log: `Policy disallows prompt for
    Sub:{com.microsoft.VSCode}...; access to kTCCServiceCalendar denied`. Critically,
    machines with a *pre-existing* TCC.db row for the app are grandfathered in and keep
    working, while brand-new machines can never create that row, i.e. this bug is
    invisible on a maintainer's or early adopter's own long-lived dev machine and
    reproduces only on a fresh install, exactly the class of bug this plan's live-probe
    caveats (RR 5, plan section 10.1) already flag as unmeasured risk.
  - **Mode B, hardened-runtime requirement**: matches FradSer PR #77 in section 1.4
    above almost verbatim, independently discovered: on macOS 26, an unsigned or
    non-hardened-runtime binary gets a silent `granted=false` with **no dialog at all**,
    regardless of how well-entitled the calling terminal is.
  - The maintainer's cross-referenced research links three other trackers hitting the
    identical wall (`BRO3886/rem#41`, `manaflow-ai/cmux#5419`,
    `ghostty-org/ghostty#9263`), and reports that the "correct" host-side fix
    (`responsibility_spawnattrs_setdisclaim()` applied per-spawned-shell by the
    *terminal emulator itself*) was **prototyped and then explicitly rejected by
    Ghostty's maintainers for security reasons**: "shells are literally arbitrary code
    execution as a service," i.e. a terminal emulator disclaiming TCC responsibility for
    every child process it spawns would let any subprocess silently inherit or
    escalate the terminal's own Calendar/Reminders grant. This is an important
    asymmetry versus FradSer's fix in section 1.4: disclaiming is safe and appropriate
    when a *specific, narrow, single-purpose* helper binary (like `event` or a future
    calendar helper) does it for itself at its own spawn point, but is considered unsafe
    when a *general-purpose* process host (a shell, a terminal emulator) does it on
    behalf of arbitrary children it does not control. Directly relevant if this plan's
    forward-queue Swift helper is ever built: disclaim narrowly, in the helper's own
    spawn path, never in `core.run_applescript()`'s generic subprocess launch.
  - The maintainer's stated remediation is exactly FradSer's playbook: sign and notarize
    release binaries with hardened runtime under a real Developer ID ("yes, the $99
    tax"), plus a `doctor`-style subcommand that prints authorization status, the
    responsible app, and its entitlement state so the failure stops being silent.
- **[Issue #50, "Non-JSON string returned outside object"](https://github.com/BRO3886/ical/issues/50)**
  (open): with `-o json` selected, a `stderr`-style "skills outdated, run: ical skills
  install" notice is printed to the **same stream** as the JSON payload, breaking any
  downstream `jq`/script consumer that expects the output to be valid JSON and nothing
  else. Direct, dated confirmation of this plan's section 3 preamble rule ("structured
  errors always return the JSON envelope," implicitly: **and nothing else ever shares
  stdout with it**) and a concrete reason to keep any future CLI diagnostics/banners on
  stderr only, never interleaved with a tool's JSON stdout.

### 2.4 `ekctl` (`schappim/ekctl`)

<https://github.com/schappim/ekctl>

- **Engine**: native Swift/EventKit CLI, JSON-first output by design (every command
  defaults to JSON; `--format` opts into CSV/plain-text as the exception, not the rule,
  matching this plan's own json-default deviation from the mail tools' text-default
  convention).
- **Command surface**: hierarchical (`calendars list/create/update/delete`,
  `events list/show/add/update/delete`, `reminders ...`, plus convenience shortcuts
  `today`/`tomorrow`/`next`). Filtering supports substring search over title/location/
  notes and an availability-status filter (`busy`/`free`/`tentative`/`unavailable`/
  `notSupported`), i.e. it surfaces the same `availability=free` concept this plan's
  `check_availability` `ignore_free_availability` flag depends on, confirming it is a
  real, queryable EventKit field rather than an invented one.
- **Exit codes as a structured-error substitute**: `0` success, `1` failure, `2`
  permission denial, `64` invalid usage, a lightweight and useful pattern for
  any CLI wrapper this plan's Wave 4 (`calendar-grant`, `calendar-events` CLI
  subcommands) ships: reserve a dedicated exit code specifically for permission denial
  so scripts/agents calling the CLI directly can branch on it without parsing text.
- **Attendees**: read-only. Attendee fields appear in event output (empty arrays shown
  in the docs' own examples) but there are no commands to add, remove, or modify
  attendees, i.e. a third independent confirmation (after apple-mcp and
  openclaw-apple-calendar) that no CLI/MCP tool surveyed in this report has shipped
  attendee **write** support through EventKit; `BRO3886/ical` (section 2.3) is the only
  exception, and it goes through the same platform-dependent, no-guaranteed-delivery
  territory this plan already documents.

### 2.5 `apple-calendar-cli` (`sichengchen/apple-calendar-cli`)

<https://github.com/sichengchen/apple-calendar-cli>

- **Engine**: EventKit. Full CRUD, recurrence, alerts, JSON output via `--json` on every
  command.
- **The one feature worth calling out specifically**: the repo ships its own
  **agent skill document** at `skills/apple-calendar-cli`, described as containing "full
  command reference, JSON schemas, and common workflows," explicitly designed for AI
  agents, and separately republished to ClawHub/LobeHub skill marketplaces. This is a
  second, independent confirmation (after this repo's own phase-1 reference-skill
  teardown of the `sundial-org`/OpenClaw skill) that shipping a purpose-built agent
  skill alongside the raw tool/CLI surface, rather than expecting the agent to infer
  usage from a generic `--help`, is an emerging convention in this exact space, not
  something unique to this plan's section 5 approach.

### 2.6 `ical-guy` (`itspriddle/ical-guy`)

<https://github.com/itspriddle/ical-guy>

Modern Swift/EventKit read-only CLI, explicitly framed as an icalBuddy replacement.
Notable feature not seen elsewhere in this survey: built-in **meeting-link detection**
(Zoom, Google Meet, Teams, WebEx) parsed out of event location/notes/URL fields, plus
JSON and ANSI-colored text output. No write support, so no TCC-for-writes complexity;
worth a one-line mention in this plan's `list_events` docs as a "nice to have someday"
(conference-link extraction from `location`/`notes`/`url`) rather than a 3.10.0 scope
item.

---

## 3. Shane Stanley's `CalendarLib EC` and the native `use framework "EventKit"` route

Forum threads: <https://forum.latenightsw.com/t/calendarlib-ec-update/2954> ·
background: <https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/>
· worked examples: <https://www.macscripter.net/t/script-eventkit-to-register-a-new-calendar-event/71427>

`CalendarLib EC` is a maintained (through at least 2024) AppleScript scripting library
by Shane Stanley that wraps EventKit via AppleScriptObjC (ASObjC) so plain AppleScript
can call EventKit methods Calendar.app's own scripting dictionary does not expose.
Distributed as a `.zip` from Late Night Software, requires El Capitan or later.

**The finding that matters more than the library itself**: ASObjC's `use framework
"EventKit"` syntax is a **built-in AppleScript language feature since Yosemite
(10.10)**, usable directly inside any script text handed to plain `osascript`, with no
compiled helper, no third-party library, and no PyObjC/Swift toolchain required. A
worked MacScripter example (fetched and confirmed above) shows the full pattern:

```applescript
use framework "EventKit"
set theEKEventStore to current application's EKEventStore's alloc()'s init()
theEKEventStore's requestAccessToEntityType:0 completion:(missing value)
...
theEKEventStore's saveEvent:someEvent span:0 commit:true |error|:(reference)
```

This runs as plain AppleScript text through `osascript`, i.e. it is deliverable through
**exactly this repo's existing `core.run_applescript()` code path**, with no new engine,
no new dependency, no packaging change on any of the four distribution surfaces, and no
change to how this repo already ships Mail automation. Two consequences worth weighing
against this plan's section 2 and section 10.11:

1. **It does not solve the TCC-responsible-process problem** documented exhaustively in
   sections 1.2, 1.4, and 2.3 above: whatever spawns `osascript` (Claude Desktop, Codex
   Desktop, or a terminal) is still the responsible process macOS attributes the
   Calendars grant to, and `use framework "EventKit"` still requests the **separate**
   `kTCCServiceCalendar` permission (distinct from the `kTCCServiceAppleEvents`/
   Automation grant this repo's `tell application "Calendar"` verbs already use), so it
   is subject to the identical "silently denied, no prompt appears" failure mode under
   a GUI host that this plan's section 2.2 (`get_engine()`, never auto-request) and
   section 4.4 (Automation-pane remediation) already design around.
2. **It could close the one capability gap this plan explicitly punts to a
   future EventKit write engine**: `RECURRING_SPAN_UNSUPPORTED` for
   `this_occurrence`/`future_occurrences` (plan section 3.7/3.8, "AppleScript engine
   supports only `span='all_occurrences'`"). That limitation is a property of
   Calendar.app's own `tell application "Calendar"` scripting dictionary, not of
   AppleScript as a language: EventKit's own `save:span:error:`/`remove:span:error:`
   methods, reachable from **the same script** via `use framework "EventKit"` mixed
   into the existing AppleScript write path, natively support `span` values for
   this-event-only vs. this-and-future. Because it still runs through
   `core.run_applescript()` with no packaging change, it is a materially lower-cost
   route to span-aware recurring writes than the Swift-helper forward item this plan
   already tracks in section 10.11, though it inherits every EventKit TCC caveat in
   section 4.4 and would need the same "never trigger the consent prompt from inside a
   tool call" discipline this plan already specifies. Recommended as a named forward-
   queue item distinct from (and cheaper than) the Swift-helper item, not as an
   in-scope change for 3.10.0; the live-probe TCC state on this dev machine (RR 5, plan
   section 10.1) blocks verifying it either way before the EventKit read grant itself
   is resolved.

---

## 4. Raycast and Alfred (calendar UI extensions, not agent tools, but real production
EventKit consumers at scale)

### 4.1 Raycast Calendar

<https://manual.raycast.com/calendar> · extensions repo: <https://github.com/raycast/extensions>

Raycast's built-in Calendar features (My Schedule, Quick Event, join-a-call actions) are
built on native macOS Calendar via EventKit, so every account already configured in
Calendar.app (iCloud, Google, Exchange) works automatically with no separate OAuth flow.
This is the largest-install-base EventKit consumer in this survey by a wide margin
(shipped to Raycast's full user base, not a hobby project), and its own extension issue
tracker is comparatively quiet on Calendar-specific EventKit failures, i.e. weak
evidence that a properly signed, properly entitled, Apple-notarized **app** (as opposed
to a bare CLI or an `npx`-spawned subprocess) does not hit the responsible-process wall
documented throughout section 1 and section 2 above. One representative issue,
[`raycast/extensions#14162`, "[Quick Event] does not add to calendar"](https://github.com/raycast/extensions/issues/14162),
is a generic unreproduced failure report (closed stale, no root cause published), too
thin to draw a conclusion from beyond "even a mature, signed app occasionally hits
opaque Calendar-write failures with no diagnostic surfaced to the user," a reminder that
this plan's structured `CALENDAR_ACCESS_DENIED`/`CALENDAR_WRITE_BLOCKED` error codes
with pane-specific remediation (section 4.3) are worth the design effort precisely
because the alternative, observed even in a well-resourced commercial product, is a
silent dead end.

### 4.2 Alfred workflows

- **`temochka/macos-automation`** (<https://github.com/temochka/macos-automation>):
  reads calendar data via EventKit and surfaces it through Alfred Script Filters
  (`nowc` for current/upcoming events, `nowl` to extract links from event descriptions),
  a read-only, personal-productivity pattern rather than a general tool surface.
- **`tresni/Alfred-iCal`** (<https://github.com/tresni/Alfred-iCal>) and
  **`rtoshiro/alfred-workflows-addcalendarevent`**
  (<https://github.com/rtoshiro/alfred-workflows-addcalendarevent>): both are pure
  AppleScript, natural-language quick-add workflows ("cal [description] at [time] on
  [date]"), predating EventKit's modern authorization model entirely. Neither exposes
  anything this plan's tool surface does not already cover more rigorously; included
  here only because they were named targets in the task brief and confirm that
  AppleScript quick-add-style event creation has been a stable, working pattern in the
  Alfred ecosystem for over a decade, further evidence that this plan's choice of
  AppleScript as the guaranteed write engine is well-trodden ground.

---

## 5. Cross-cutting patterns (seen independently across 3+ unrelated projects)

1. **TCC responsible-process attribution is the single most reproduced failure in this
   entire survey.** Independently rediscovered and root-caused in `griches/apple-mcp`
   (section 1.2), `FradSer/mcp-server-apple-events` (section 1.4), and `BRO3886/ical`
   (section 2.3), by different authors, on different toolchains (Bun/TypeScript, Node/
   Swift, Go), months apart. It is not a quirk of any one implementation; it is a
   property of how macOS 14+ TCC works for any subprocess spawned by a GUI host app
   that lacks its own `NSCalendarsUsageDescription`/`NSCalendarsFullAccessUsageDescription`
   entitlement. This plan's decision to never call `requestFullAccessToEvents` from a
   tool call, and to gate the EventKit fast path strictly behind an already-granted
   status check (section 2.2), is the only approach in this survey that avoids the bug
   class entirely rather than working around it after the fact.
2. **Hardened-runtime code signing is a second, additive, and separately silent failure
   mode on macOS 26**, discovered independently in `FradSer/mcp-server-apple-events`
   (PR #77) and `BRO3886/ical` (#34, "Mode B"): even a correctly entitled, correctly
   attributed caller gets `granted=false` with **zero dialog** if the binary being
   called lacks `codesign --options runtime`. Directly relevant if this plan's
   forward-queue Swift helper is ever built; irrelevant to 3.10.0 since it ships no
   compiled binary.
3. **Unbounded UID/by-id lookups are the most expensive operation every project that
   has one eventually has to fix.** `FradSer/mcp-server-apple-events#108` is a live,
   open, unresolved instance of precisely the anti-pattern this repo's own RR 2.4
   already flagged in the reference skill; this plan's calendar/window-hint-bounded
   `get_events_by_id` (3.3) is the right design, and this is a second, independent
   real-world confirmation of why.
4. **Date-only and cross-timezone string parsing breaks in the same specific way
   (UTC-midnight coercion causing an off-by-one day) across independent codebases**:
   `FradSer/mcp-server-apple-events` PR #80, plus the timezone-shift bugs in `icalPal`
   #35/#45. This plan's requirement-7 discipline (parse and convert entirely in Python
   with `zoneinfo` before any string reaches AppleScript, never let the engine coerce a
   date-only or ambiguous string) is validated by three unrelated bug reports, not
   theoretical caution.
5. **No project surveyed, across MCP servers, CLIs, or app extensions, ships a working,
   reliable attendee-invite-with-guaranteed-delivery feature.** `BRO3886/ical` is the
   only one that even attempts invite-sending, and its own docs warn there is no
   dry-run and invites "immediately trigger real email notifications." Every other
   project either omits attendees entirely (apple-mcp, EgorKurito, openclaw-apple-
   calendar, ekctl) or documents them read-only (`FradSer/mcp-server-apple-events`,
   `andrewbergsma/apple-calendar-mcp`'s still-open Phase 5 plan). This plan's gated,
   confirmed, `"invitation_delivery": "platform_dependent"`-labeled approach (section
   3.5) is, if anything, more honest than the field average, and the RSVP-refusal shim
   (3.10) matches universal practice: nobody in this survey has shipped programmatic
   RSVP either.
6. **A private-database read path (bypassing both AppleScript and EventKit) is the one
   architecture in this survey with a demonstrated, dated history of breaking outright
   on macOS point releases** (`icalPal`'s Sequoia breakage, `icalPal`'s ongoing
   timezone/recurrence-math drift bugs). No tool surveyed recommends this route for new
   work; several exist specifically to migrate *away* from it. This plan never proposes
   it, and this section is included only to close the loop on why that is correct.
7. **JSON-only output discipline needs enforcement at the stream level, not just the
   schema level**: `BRO3886/ical#50` shows a stray stderr-style notice leaking onto the
   same stream as a `--json` payload broke real downstream `jq` consumers in
   production. This plan's "JSON-only tool output" rule (section 4.4) should be read to
   include "no other diagnostic text ever shares the tool's return channel," which is
   already implicit in this repo's `ToolError`/envelope pattern but worth stating
   explicitly in the calendar tool implementation notes.

---

## 6. Top 10 concrete lessons for this implementation

1. **The hybrid engine architecture (AppleScript guaranteed, EventKit an
   already-granted-only fast path) is independently validated, not a guess.**
   `griches/apple-mcp` documents the exact same design being *retrofitted* after
   shipping EventKit-only reads and discovering they fail silently under every GUI MCP
   host. Building the fallback in from day one, as this plan does, avoids the two-issue,
   multi-week debugging cycle visible at
   <https://github.com/griches/apple-mcp/issues/10> and
   <https://github.com/griches/apple-mcp/issues/6>.
2. **Never call `requestFullAccessToEvents`/`requestAccess` from inside a tool call, on
   any code path, including a `use framework "EventKit"` AppleScript snippet.** Every
   silent-denial bug in this survey (sections 1.2, 1.4, 2.1, 2.3) traces back to a
   request being fired from a process the GUI host does not consider itself responsible
   for authorizing. Source:
   <https://github.com/griches/apple-mcp/issues/10> and
   <https://github.com/FradSer/mcp-server-apple-events/issues/93>.
3. **Bound every UID lookup to a calendar and/or date window; never let "read by id"
   degenerate into "scan everything and filter in memory."** A shipping tool with real
   users still has this as an open, unfixed performance issue today. Source:
   <https://github.com/FradSer/mcp-server-apple-events/issues/108>.
4. **All date/timezone conversion belongs entirely in Python (`zoneinfo`) before any
   string reaches AppleScript; never let a date-only or ambiguous string cross the
   engine boundary uninterpreted.** Three independent projects shipped the identical
   UTC-midnight-coercion bug. Source:
   <https://github.com/FradSer/mcp-server-apple-events/pull/80> and
   <https://github.com/ajrosen/icalPal/issues/45>.
5. **Treat attendee/invite writes as irreversible and side-effecting even in a test
   environment; there is no dry-run anywhere in this ecosystem.** The one tool that
   ships invite-sending says so explicitly in its own docs. Source:
   <https://github.com/BRO3886/ical> (Getting Started / attendee section).
6. **`use framework "EventKit"` inside plain AppleScript, deliverable through this
   repo's existing `core.run_applescript()`, is a real, working, dependency-free route
   to `save:span:error:`/`remove:span:error:`, and is worth a named forward-queue entry
   as a cheaper alternative to a compiled Swift helper for closing the
   `RECURRING_SPAN_UNSUPPORTED` gap**, though it does not remove the TCC
   responsible-process problem in lesson 2. Source:
   <https://www.macscripter.net/t/script-eventkit-to-register-a-new-calendar-event/71427>
   and the CalendarLib EC forum thread at
   <https://forum.latenightsw.com/t/calendarlib-ec-update/2954>.
7. **If this plan's Swift-helper forward item is ever built, the disclaim technique
   (`responsibility_spawnattrs_setdisclaim` via `posix_spawn`/`POSIX_SPAWN_SETEXEC`) is
   a proven, shipping fix for the responsible-process problem, but must be scoped
   narrowly to the helper's own spawn point, never applied generically to
   `core.run_applescript()`'s subprocess launch** (a general disclaim there would let
   any child process silently inherit this server's Calendar grant, exactly the design
   Ghostty's maintainers rejected on security grounds for a general-purpose terminal
   emulator). Source:
   <https://github.com/FradSer/mcp-server-apple-events/pull/110> and
   <https://github.com/BRO3886/ical/issues/34#issuecomment-4670758247>.
8. **On macOS 26, a bare or ad-hoc-signed helper binary can get a silent
   `granted=false` with zero dialog even when the caller is fully entitled; hardened
   runtime signing (`codesign --options runtime`) is a hard requirement for any future
   compiled artifact, not an optional hardening pass.** Source:
   <https://github.com/FradSer/mcp-server-apple-events/pull/77>.
9. **Keep any CLI/tool diagnostic banners, version-outdated notices, or skill-update
   nags strictly off the JSON return channel.** A real downstream integration broke
   because a notice string shared stdout with a `--json` payload. Source:
   <https://github.com/BRO3886/ical/issues/50>.
10. **Never build a Calendar-reading engine against the private SQLite database, even
    as an optional fast path; the schema is unversioned and has broken outright on a
    macOS point release, and its stored recurrence/timezone representation has open,
    unresolved drift bugs against Exchange-backed calendars.** Source:
    <https://github.com/ajrosen/icalPal/issues/27> and
    <https://github.com/ajrosen/icalPal/issues/45>.

---

## 7. What contradicts or refines the current plan

- **RR 4.4's framing that a signed, notarized, self-disclaiming Swift helper "is out of
  scope for a pure-Python release" reads more like a scoping decision than a technical
  ceiling once `FradSer/mcp-server-apple-events` PR #110 is accounted for.** It is
  buildable, it is shipping, and it is fully open source (MIT). This plan's decision to
  defer it past 3.10.0 remains reasonable on cost/schedule grounds (Apple Developer
  signing, a compiled artifact, a new packaging story across four distribution
  surfaces), but the section 10.11 framing ("the EventKit engine cannot be
  live-verified... move `eventkit.py` to the forward queue") should be read as a
  scheduling call, not as "nobody has solved this." A pointer to PR #110 in the forward-
  queue note would save whoever picks this up later a rediscovery cycle.
- **The plan currently treats `RECURRING_SPAN_UNSUPPORTED` as strictly tied to the
  future EventKit write engine** (section 3.7: "remediation naming the EventKit write
  engine as the future unlock"). Section 3 of this report shows a second, cheaper,
  dependency-free path to the same capability via `use framework "EventKit"` inside the
  existing AppleScript write path. This does not have to change 3.10.0's scope, but the
  remediation text and the forward-queue item in section 10.11 of the plan should
  probably name both options rather than pointing solely at "the EventKit write engine"
  as if a compiled helper were the only route.
- **The plan's section 2 rationale correctly anticipates the TCC responsible-process
  problem in the abstract ("the server must never trigger the EventKit consent prompt
  from a tool call"), but section 10.1's risk framing ("until a human answers the
  prompts, no live verification or real timing is possible... the risk is schedule, not
  safety") undersells how easily this can become a permanent dead end rather than a
  one-time schedule delay.** `griches/apple-mcp#10` documents a host (Claude Desktop/
  Claude Code as of 2026) where the EventKit prompt **structurally cannot appear at
  all**, with no user-visible remediation, because neither the host nor the helper
  declares the usage string; this is not "a human hasn't clicked yet," it is "there is
  currently nothing to click." If the live-verification pass in this plan's section 9
  hits the same wall on this dev machine, the correct read is not "wait for the human,"
  it is "confirm whether this specific host/macOS combination can ever grant it, and if
  not, document that the EventKit fast path is unreachable on this install regardless
  of `auto` detection working correctly." `eventkit_available` (already planned as a
  diagnostic field in `list_calendars`, section 3.1) is the right place to surface this
  distinction (`not_determined_but_grantable` vs. `structurally_unreachable_on_this_host`)
  if the live probe confirms it.
- **Nothing in this survey found any project offering a working free-busy API for
  iCloud calendars**; `BRO3886/ical`, the most feature-complete tool surveyed, only
  supports free/busy on Exchange/Google Workspace. This is a direct, independent
  confirmation of RR 4.3 and this plan's section 3.4 choice to compute availability by
  folding a bounded event fetch in Python rather than looking for a native free-busy
  call; no evidence surfaced anywhere that a native API exists to use instead.
- **One small, additive documentation opportunity, not a contradiction**: EventKit
  silently dropping iCalendar `CLASS`/`CATEGORIES`/`X-MICROSOFT-CDO-IMPORTANCE` markers
  (section 1.5) is not mentioned anywhere in this plan or in the phase-1 platform-API
  report. It does not block anything in the current tool surface, but if a future
  privacy-tier or classification feature is ever proposed on top of `list_events`, this
  is a documented, dated gap worth citing rather than rediscovering.

---

## Sources consulted (recap)

- MCP servers: <https://github.com/Dhravya/apple-mcp> (`supermemoryai/apple-mcp`),
  <https://github.com/griches/apple-mcp>,
  <https://github.com/EgorKurito/apple-calendar-mcp>,
  <https://github.com/FradSer/mcp-server-apple-events>,
  <https://github.com/FradSer/event>,
  <https://github.com/andrewbergsma/apple-calendar-mcp>,
  <https://github.com/androidStern-personal/openclaw-apple-calendar>,
  <https://github.com/shadowfax92/apple-calendar-mcp>,
  <https://github.com/JonathanRReed/Apple-MCPs>,
  <https://github.com/lucasheight/mcp-calendars>.
- CLIs: <https://github.com/ali-rantakari/icalBuddy>,
  <https://github.com/ajrosen/icalPal>,
  <https://github.com/BRO3886/ical> / <https://ical.sidv.dev/>,
  <https://github.com/schappim/ekctl>,
  <https://github.com/sichengchen/apple-calendar-cli>,
  <https://github.com/itspriddle/ical-guy>,
  <https://github.com/MartinGross/calbuddy> (pointer only).
- AppleScriptObjC/EventKit: forum threads at
  <https://forum.latenightsw.com/t/calendarlib-ec-update/2954>,
  <https://www.macscripter.net/t/script-eventkit-to-register-a-new-calendar-event/71427>,
  background at <https://mjtsai.com/blog/2024/10/23/the-sad-state-of-mac-calendar-scripting/>.
- Raycast/Alfred: <https://manual.raycast.com/calendar>,
  <https://github.com/raycast/extensions>,
  <https://github.com/temochka/macos-automation>,
  <https://github.com/tresni/Alfred-iCal>,
  <https://github.com/rtoshiro/alfred-workflows-addcalendarevent>.
- Issue trackers queried directly via `gh api search/issues` and `gh issue view`
  against `supermemoryai/apple-mcp`, `griches/apple-mcp`,
  `FradSer/mcp-server-apple-events`, `FradSer/event`, `andrewbergsma/apple-calendar-mcp`,
  `BRO3886/ical`, `ajrosen/icalPal`, `ali-rantakari/icalBuddy`, and
  `raycast/extensions`, filtered for slow/hang/timeout/attendee/invite/timezone/
  permission language; every issue cited above was read in full via `gh issue view
  --json title,body,comments`, not inferred from a search snippet.
