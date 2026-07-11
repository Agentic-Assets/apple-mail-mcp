# Apple Calendar Tools: Phase 1 Live Probe (Researcher 4 of 5)

Machine: this exact Mac mini (Caymans-Mac-mini-3.local), the box that will run
these tools in production. All probes below are live, ran strictly read-only,
and never created, modified, or deleted any event or calendar, never added an
attendee, and never used `display dialog`. Every Bash call carried an explicit
timeout. Where a command hung to its timeout, it was recorded as "blocked,
likely TCC consent needed" per the run rules, retried at most once, and any
runaway `osascript` process was cleaned up with `pkill -f osascript`.

Timing method: a small helper,
`/private/tmp/claude-501/-Users-cayman-mac-mini-Documents-GitHub-apple-mail-mcp/794563c1-e8b9-4dda-822c-4523ee515dae/scratchpad/run_timed.py`,
wraps each command in `subprocess.run(..., timeout=N)` and reports wall time
with `time.monotonic()` to millisecond precision, plus the exact status
(`completed` vs `timeout`). This is a real internal timeout (Python kills the
child on expiry), not just the outer Bash-tool timeout, so the elapsed values
below are precise, not artifacts of the harness cutoff.

**Headline result: every AppleScript Apple-Event path into Calendar.app (and
even into Finder, as a control) blocked/hung on this machine for the full
probe session. EventKit (both via pyobjc and via JXA's ObjC bridge) reports a
clean, fast, consistent `notDetermined` authorization status with no hang on
the status read itself, but the actual access-request call also hangs
waiting for a completion callback that never fires. Net: this machine has
never resolved either the Automation-to-Calendar TCC grant or the
Calendars-full-access TCC grant for the identity these probes ran under, and
whatever UI would let a human resolve that is not being answered during this
session.**

---

## 1. System info

```
$ sw_vers
ProductName:    macOS
ProductVersion: 26.5.2
BuildVersion:   25F84

$ uname -a
Darwin Caymans-Mac-mini-3.local 25.5.0 Darwin Kernel Version 25.5.0: Tue Jun 9 22:26:22 PDT 2026; root:xnu-12377.121.10~1/RELEASE_ARM64_T8132 arm64

$ hostname
Caymans-Mac-mini-3.local
```

`system_profiler SPHardwareDataType`: Mac mini, Model Identifier `Mac16,10`,
chip Apple M4, 10 cores (4 performance + 6 efficiency), 16 GB memory.

Session context found via `who`/`w`: console user `cayman-mac-mini` logged in
since Wed 10:28, idle roughly 1 day of wall-clock but `HIDIdleTime` (mouse/kb
idle from `ioreg -c IOHIDSystem`) was only ~485 seconds (~8 min) at probe
time, and `pmset -g powerstate` shows the display driver state as `ON`, held
awake by an active `caffeinate -i` process (pid 1396) and `powerd`'s own
"prevent sleep while display is on" assertion. `ioreg -n Root -d1 -a` →
`IOConsoleUsers` shows `kCGSSessionOnConsoleKey = true` and no
`CGSSessionScreenIsLocked` key present. Net read: there is an active,
unlocked console session with a display that is on: the hangs below are
**not** simply "screen locked, consent dialog can't render."

---

## 2. `osascript -e 'tell application "Calendar" to get name of calendars'`

```
$ python3 run_timed.py 25 osascript -e 'tell application "Calendar" to get name of calendars'
[run_timed] STATUS=timeout ELAPSED=25.005s (limit=25.0s)
```

Retry (the one permitted retry):

```
$ python3 run_timed.py 20 osascript -e 'tell application "Calendar" to get name of calendars'
[run_timed] STATUS=timeout ELAPSED=20.007s (limit=20.0s)
```

**Result: BLOCKED both times, no output, no error text at all** (a genuine
process hang, not a fast AppleEvent error like `-1743 Not authorized`). Both
child processes were cleanly reaped by Python's `subprocess` timeout kill; a
follow-up `ps aux | grep osascript` showed nothing left running, and
`pkill -f osascript` found nothing to clean up either time.

Per the run rules, this is exactly the "blocked, likely TCC consent needed"
signature: an Automation (Apple Events) consent prompt is either pending
somewhere unresolved, or the requesting process's identity has never been
asked and the ask itself is not completing.

### 2a. Isolating whether this is Calendar-specific

Three quick control probes to characterize the hang rather than just accept
it:

```
$ python3 run_timed.py 10 osascript -e 'return 1+1'
[run_timed] STATUS=completed RETURNCODE=0 ELAPSED=0.028s
STDOUT: 2
```
`osascript` itself is fast and healthy when no Apple Event is sent to another
app (28 ms). The hang is specific to `tell application "X"` Apple-Event
delivery, not to `osascript` startup or the interpreter.

```
$ python3 run_timed.py 15 osascript -e 'tell application "Finder" to get name of home'
[run_timed] STATUS=timeout ELAPSED=15.007s (limit=15.0s)
```
**Finder hangs identically to Calendar.** This means the block is not
Calendar-specific: it is a general Automation-consent gap for whatever app
identity is issuing these Apple Events. Process-ancestry trace
(`ps -o pid,ppid,comm`, walked to PID 1) shows the chain: `Terminal.app`
(pid 1254) to `login` to `zsh` to `claude` to `zsh` (the shell these Bash calls
run in). Automation TCC grants are recorded per (controlling-app,
target-app) pair, e.g. System Settings, Privacy & Security, Automation,
Terminal, Calendar / Finder. That reads as Terminal never having a
resolved Automation grant for either target app in this environment, with
the resulting system consent prompt not being answered by anyone at the
console during the probe window.

```
$ python3 run_timed.py 20 osascript -e 'tell application "System Events" to get name of every window of every process whose name contains "TCC"'
[run_timed] STATUS=completed RETURNCODE=1 ELAPSED=13.609s
STDERR: execution error: System Events got an error: osascript is not allowed assistive access. (-25211)
```
This is a **different, third TCC bucket** (Accessibility, for GUI/System
Events scripting) and it behaves completely differently: it fails **fast**
with an explicit, named error (`-25211`, "not allowed assistive access")
rather than hanging. That is a useful operational contrast for whatever
timeout/error-handling policy the Calendar tools adopt: an Accessibility
denial surfaces immediately and unambiguously, while an Automation
consent-pending state hangs silently with no error at all until something
external kills the process.

---

## 3. Bounded single-calendar AppleScript query

Script (`-3 days` to `+4 days` window, `calendar 1`, count only):

```applescript
set winStart to (current date) - 3 * days
set winEnd to (current date) + 4 * days
tell application "Calendar"
    set targetCal to calendar 1
    set evtList to (every event of targetCal whose start date ≥ winStart and start date ≤ winEnd)
    return (count of evtList)
end tell
```

```
$ python3 run_timed.py 15 osascript probe3_bounded_single.applescript
[run_timed] STATUS=timeout ELAPSED=15.006s (limit=15.0s)
```

**Blocked, same signature as probe 2.** Given probe 2 had already been run
twice (its one permitted retry) and probe 3 reproduced the identical hang on
the first attempt, I did not spend a second retry re-confirming the same
wall here; the pattern is already unambiguous. Cleaned up with
`pkill -f osascript` (no matching process found: the internal
`subprocess.run(timeout=...)` had already killed it).

No event count was obtainable. **No timing data on a real bounded single-
calendar query exists from this machine in this session.** Everything below
about "expected" bounded-query cost is inference from the Mail.app analog in
this repo (`plugin/apple_mail_mcp/bounded_scan.py`, 120s default AppleScript
timeout, single-flight lock) and from general AppleScript/Calendar.app
folklore, not a measurement.

---

## 4. Bounded scan across ALL calendars: SKIPPED

Per the run instructions ("if this triggers a TCC prompt/timeout, report and
skip dependent probes"), this probe depends on the same Automation grant that
blocked probes 2 and 3. I did not execute it live: it would almost certainly
reproduce the identical hang for every calendar in sequence, and running it
to completion would mean deliberately waiting out N more full timeouts for
zero new information, which is not a good use of the probe budget. This is
an explicit skip, not a silent omission, flagging clearly so a later
researcher (or the implementer) knows there is **no live per-calendar
fan-out timing from this machine** and one is still needed once Automation
consent is resolved.

## 5. Unbounded-risk characterization: SKIPPED (not run to completion, as instructed)

Also skipped live execution for the same reason as §4: it is gated behind
the same blocked Automation grant, so a real "every event of the busiest
calendar" run was never reachable in this session. The one thing worth
recording here without running it: probes 2 and 3 show that even a
**7-day-wide, single-calendar** bounded query could not complete inside 15-25
seconds of wait (because the Apple Event never got delivered/answered at
all, not because Calendar.app was slow to compute the answer), so there is
no live evidence either way about actual AppleScript-side churn cost for
`every event` yet. That risk characterization has to come from a follow-up
probe once Automation consent is granted, or from the Mail.app precedent in
this repo, where unbounded AppleScript scans over large mailboxes were
exactly the failure mode `bounded_scan.py` was built to prevent (see
`plugin/apple_mail_mcp/bounded_scan.py` and the sibling codebase-map report,
`reports/phase1-codebase-map-2026-07-10.md`, for the mail analog). Treat
"Calendar.app `every event` on a busy calendar is expensive/unbounded" as a
carried-over hypothesis from the Mail precedent, not a number measured here.

---

## 6. EventKit via scratch venv (pyobjc)

Per the task's literal path (`undefined/ekprobe-venv`) containing an
unfilled template variable, the venv was created under this session's
scratchpad directory instead:
`/private/tmp/claude-501/-Users-cayman-mac-mini-Documents-GitHub-apple-mail-mcp/794563c1-e8b9-4dda-822c-4523ee515dae/scratchpad/ekprobe-venv`.

```
$ python3 -m venv .../ekprobe-venv
elapsed=1.096s rc=0

$ .../ekprobe-venv/bin/pip install --disable-pip-version-check pyobjc-framework-EventKit
elapsed=1.607s rc=0
Successfully installed pyobjc-core-12.2.1 pyobjc-framework-Cocoa-12.2.1 pyobjc-framework-EventKit-12.2.1

$ du -sh .../ekprobe-venv
43M
```

Install was fast (1.6s total, well-cached/fast network) and small (43 MB for
the full venv including the interpreter's own site-packages). Three wheels
pulled in: `pyobjc-core`, `pyobjc-framework-Cocoa` (a dependency of the
EventKit framework wheel), `pyobjc-framework-EventKit`.

Probe script (`ekprobe.py`) logic:
1. `EKEventStore.authorizationStatusForEntityType_(EKEntityTypeEvent)`: a sync
   read, no consent triggered by this call itself.
2. If `notDetermined` (0): call
   `requestFullAccessToEventsWithCompletion_` (the macOS 14+ API, present
   and used since this repo's target OS is macOS 26.5.2, so it is the correct
   call), pump `NSRunLoop.currentRunLoop().runUntilDate_()` in 0.25s slices
   for up to 20s waiting for the completion block to fire, then re-read
   status.
3. Only if status is `authorized` (3, legacy) or `fullAccess` (4) does it run
   the bounded `-3d/+4d` predicate fetch across all calendars and report
   calendar titles, `allowsContentModifications` (writable), and
   `defaultCalendarForNewEvents`.

```
$ python3 run_timed.py 45 .../ekprobe-venv/bin/python3 ekprobe.py
[run_timed] STATUS=completed RETURNCODE=0 ELAPSED=21.107s
STDOUT:
[ekprobe] initial authorizationStatusForEntityType(Event)=0 (notDetermined)
[ekprobe] status is notDetermined; requesting full access (macOS14+ API) with 20s runloop wait cap
[ekprobe] TIMEOUT waiting 20s for requestFullAccess completion callback; this itself is the TCC-consent-pending signal (dialog likely shown, unanswered).
[ekprobe] post-request authorizationStatusForEntityType(Event)=0 (notDetermined)
[ekprobe] status=notDetermined does not permit read access; skipping predicate fetch
```

**Result: initial status `notDetermined`. The access request itself never
completes.** No callback fired inside the 20-second cap, and status was
still `notDetermined` immediately afterward. The 21.107s total (vs the 20s
internal cap) confirms it was this script's own internal timeout that ended
the run, not the outer Bash-tool timeout. No calendar list, no writability
flags, no default-calendar title, and no bounded-fetch timing were obtainable:
none of that data exists from this session.

One structural note worth flagging for whoever builds the real EventKit
integration: the calling process here is a bare Mach-O executable (the
venv's `python3` is a symlink to
`/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/bin/python3.14`),
**not** a proper `.app` bundle with its own `Info.plist` /
`NSCalendarsFullAccessUsageDescription` key. That is a known rough edge for
pyobjc scripts requesting EventKit access from the command line: without a
bundle identity and usage-description string, the system consent prompt can
fail to present cleanly for the calling process, which is a second plausible
(not confirmed: TCC.db was intentionally never queried, per instructions)
explanation for the request hang, on top of "nobody is at the console to
click Allow." Both explanations point to the same practical conclusion: a
bare interpreter invoking EventKit is not a reliable way to get a resolvable
consent prompt: whatever surface ships needs either a properly bundled host
process, or to lean on `osascript`/Calendar.app's own Automation grant path
instead, or both.

---

## 7. JXA probe (ObjC bridge, no pip dependency)

```javascript
ObjC.import('EventKit');
var store = $.EKEventStore.alloc.init;
var status = $.EKEventStore.authorizationStatusForEntityType(0); // EKEntityTypeEvent
```

```
$ python3 run_timed.py 20 osascript -l JavaScript probe7_jxa.js
[run_timed] STATUS=completed RETURNCODE=0 ELAPSED=0.071s
STDERR:
[jxa] bridge loaded OK
[jxa] authorizationStatusForEntityType(Event)=0 (notDetermined)
[jxa] status does not permit read; skipping bounded fetch (read-only probe, no request made here)
```

**The ObjC bridge loads cleanly and fast (71 ms total, no hang at all).**
Reading `authorizationStatusForEntityType` is a synchronous, non-consent-
triggering call in both the pyobjc and JXA paths, and both independently
agree: **`notDetermined`**. That cross-path agreement is good corroborating
evidence this is genuine, unresolved system authorization state for this
machine/session, not an artifact of one particular binding or bundle
quirk.

Since status was `notDetermined` (not `authorized`/`fullAccess`), per the
probe instructions I did not attempt the bounded predicate fetch in JXA, and
deliberately did **not** call any access-request API from the JXA path
either (no `requestFullAccessToEventsWithCompletion_` equivalent invoked
here): the pyobjc probe in §6 had already demonstrated the request-hangs
behavior once; there was no value in re-triggering a second pending-consent
state through a second code path, and the task only asked for a status
report plus a conditional fetch, not a second request attempt.

---

## 8. AppleScript vs EventKit: comparison table and TCC-bucket inference

| Path | Call | Result | Wall time |
|---|---|---|---|
| AppleScript, no target app | `return 1+1` (control) | OK | 0.028s |
| AppleScript → Calendar | `tell application "Calendar" to get name of calendars` | **BLOCKED (hang)** | 25.005s, then 20.007s on retry |
| AppleScript → Calendar | bounded −3d/+4d single-calendar event count | **BLOCKED (hang)** | 15.006s |
| AppleScript → Finder (control) | `get name of home` | **BLOCKED (hang)** | 15.007s |
| AppleScript → System Events | GUI window enumeration | **DENIED, fast explicit error** `-25211` | 13.609s (error, not full timeout) |
| EventKit (pyobjc, bare venv script) | `authorizationStatusForEntityType` (sync read) | `notDetermined` | effectively instant (sub-second, folded into the 21.107s total dominated by the request wait below) |
| EventKit (pyobjc, bare venv script) | `requestFullAccessToEventsWithCompletion_` | **PENDING, no callback** | hit 20s internal cap, script total 21.107s |
| EventKit (JXA `osascript -l JavaScript`, ObjC bridge) | `authorizationStatusForEntityType` (sync read) | `notDetermined` | 0.071s (fast, no hang) |

Inferred TCC buckets (inferred purely from process behavior: hang vs. fast
explicit error vs. fast success; TCC.db was never queried directly, as
instructed):

- **Automation (Apple Events to other apps).** System Settings → Privacy &
  Security → Automation → Terminal → {Calendar, Finder, …}. Both Calendar
  and Finder targets hung identically for the calling identity here
  (traced to Terminal.app via process ancestry), which reads as this
  Terminal-to-target grant never having been resolved (granted or denied)
  in this environment, and the resulting system prompt not getting an
  answer during the probe window. A hang, not a fast `-1743` error, is the
  signature of a first-time/unresolved Automation prompt; a fast `-1743`
  is what you'd expect once TCC already has a recorded "No."
- **Accessibility (System Events GUI scripting).** A separate bucket
  (Privacy & Security → Accessibility → Terminal). This one is
  distinguishable from Automation by behavior alone: it failed **fast**
  with a named error (`-25211`) instead of hanging.
- **Calendars full access (EventKit).** A third, independent bucket
  (Privacy & Security → Calendars). It attaches to the actual calling
  executable rather than a controlling-app/target-app pair. Here, the
  venv's bare `python3` binary for the pyobjc path, and `osascript` itself
  for the JXA path. Both independently reported `notDetermined`, and the
  pyobjc request call hangs the same way the Automation path does (no
  resolution within the wait window), suggesting the same root condition
  applies across all three buckets on this machine right now: **no
  interactive human is answering pending consent prompts during this
  session**, whatever their exact rendering state.

Net conclusion for implementation: **this machine, as currently configured
for this calling identity, cannot complete a live Calendar automation call
through either AppleScript or EventKit inside a short session.** Every
number in §§2–5 above that would characterize actual Calendar.app query
cost (per-calendar fan-out time, unbounded-scan churn) is therefore
unmeasured. Only the EventKit install/venv footprint (§6) and the pure
bridge/interpreter overhead (§7) are real, measured, non-TCC-gated numbers
from this session. Before Phase 2 implementation work can get real
Calendar.app performance numbers on this box, someone with physical console
access needs to trigger and answer the Automation (Terminal to Calendar) and
Calendars-full-access consent prompts once, interactively. After that, a
follow-up probe session should re-run §§3–5 for the actual bounded/unbounded
timing data this report could not obtain.

---

## Appendix: cleanup and safety confirmation

- No event, calendar, or attendee was created, modified, or deleted at any
  point.
- `display dialog` was never used.
- Every `osascript`/EventKit call ran under an explicit internal timeout
  (15–45s) via `run_timed.py`'s `subprocess.run(timeout=...)`, in addition to
  the outer Bash-tool timeout.
- After each hang, `ps aux | grep osascript` was checked and
  `pkill -f osascript` was run for cleanup; in every case the process was
  already reaped by Python's own timeout-kill, so `pkill` found nothing.
- Calendar.app was already running before any probe in this session (process
  start time `Wed05PM`, well before this probe session began); it was not
  freshly launched by these probes, and it was not quit afterward, per
  instructions.
- Scratch artifacts (`run_timed.py`, `ekprobe.py`, `ekprobe-venv/`,
  `probe3_bounded_single.applescript`, `probe7_jxa.js`) live entirely under
  the session scratchpad directory, not in the repo working tree. `git
  status --short` at the end of this session shows only the new
  `tasks/active/apple-calendar-tools/` report directory as untracked; no
  repo source files were touched, and no git commands beyond `status` /
  `branch --show-current` were run.
