# Phase 3: adversarial attack on the Apple Calendar tools plan

Reviewer role: refute the plan and the research report it rests on. Every claim below was
checked against the actual working tree on `feat/apple-calendar-tools` (not against the
plan's or report's own description of the tree). File:line citations point at the current
source unless marked SPECULATIVE. Verdicts are rated blocker, major, or minor, and marked
CONFIRMED (checked against source or live command output in this session) or SPECULATIVE
(reasoned but not independently checked).

Documents attacked:

- [`plan-2026-07-10.md`](../plan-2026-07-10.md)
- [`research-report-2026-07-10.md`](../research-report-2026-07-10.md)

---

## Summary

The plan's biggest weakness is not in the platform research (that part is careful and the
citations check out) but in the safety narrative it builds on top of it. The plan and the
report both assert, repeatedly, that calendar mode-gating "mirrors the existing mail
architecture." That claim is false for every case that matters: mail's `--draft-safe` and
`--read-only` flags gate nothing but the three actual send tools today. `manage_trash`,
`move_email`, `create_mailbox`, and `update_email_status` run unconditionally in every
mode. The calendar plan proposes real, new, stricter safety plumbing and should say so,
not present it as reuse. A second, independent problem sits in the AppleScript recurring-
event handling: the plan's own performance evidence (RR 4.6) says Calendar.app's `whose`
filter does not use an index and its cost tracks total store size, not window size, yet
the plan's recurring-master fetch pass is built on exactly the kind of non-id `whose`
predicate the mail side's own lint (`tests/core/test_no_unbounded_whose.py`) forbids for
Mail, for precisely this reason. Below are 17 findings across the five requested angles.

---

## 1. Churn / spin risk

### F1 (major, CONFIRMED) — recurring-master second pass is the calendar analogue of a pattern the mail lint forbids, and the plan's own performance evidence says why that matters

Plan section 3.2 (`plan-2026-07-10.md:191-195`) specifies: "a second bounded recurring-master
pass (`start date` within `RECURRING_LOOKBACK_DAYS` before the window and `recurrence`
non-empty) expanded in Python." `RECURRING_LOOKBACK_DAYS` is 400 days
(`plan-2026-07-10.md:474`). That AppleScript predicate ("recurrence is not empty" combined
with a date range) is not an id predicate and not a `read status` predicate.

Compare against the actual enforcement in this repo for Mail:
`tests/core/test_no_unbounded_whose.py:60-62` bans `every message of <mailbox> whose
<predicate>` unless the predicate is `id is ...` or `read status is ...` — every other
predicate (explicitly including date-range predicates) "forces Mail to materialize the
entire remote mailbox," per the module docstring at lines 10-15.

The research report's own performance section makes the identical claim for Calendar:
"Calendar.app's `whose` filter evaluates by marshalling candidates rather than an index
push-down, so cost tracks total store size, not window size"
(`research-report-2026-07-10.md:422-424`, RR section 4.6). That is the calendar-domain
restatement of the exact reasoning the mail lint encodes. The plan's recurring-master pass
is therefore, by the report's own evidence, not actually bounded in cost even though it is
bounded in *result set*. A calendar with a large number of recurring masters accumulated
over years (a plausible "busy professional" calendar per the report's own return-cap
rationale, RR/plan section 4.1: "a year is roughly 250 to 2000 events for a busy
professional") could still make this second pass slow or hang-adjacent, independent of the
`RECURRING_LOOKBACK_DAYS` window.

Compounding this: plan section 7 (`plan-2026-07-10.md:706-708`) explicitly decides *not* to
extend the repo-wide static lint to calendar scripts, deferring protection entirely to
hand-written `test_calendar_scripts.py` assertions. That is a reasonable call in isolation
(the existing regex is hardcoded to `every message of`), but it means there is no
structural gate stopping a future contributor from writing exactly this dangerous pattern
during implementation — only reviewer diligence and test coverage, which is weaker than the
mechanism this repo built for Mail after getting burned by it once (the Gmail 24K-inbox
regression the lint's docstring references).

**Recommendation:** either add a calendar-specific static lint (mirroring
`DANGEROUS_WHOSE`) before Wave 1 lands, or explicitly document in Open Risks that the
recurring-master pass is a `whose`-on-non-indexed-predicate pattern whose cost is
unmeasured and potentially unbounded regardless of `RECURRING_LOOKBACK_DAYS`, with a named
follow-up to retire it once EventKit read fast-path is live-verified.

### F2 (major, CONFIRMED) — unscoped fan-out has a per-call cap but no aggregate wall-clock budget

`list_events` and `check_availability` fan out across up to `MAX_CALENDARS_PER_QUERY` (20)
calendars when `calendar=None` and `calendars=None` (`plan-2026-07-10.md:181,232`). For
`list_events` with `expand_recurring=True` (the default), each calendar in that fan-out
triggers up to two sequential AppleScript round trips: the primary bounded fetch and the
recurring-master pass (`plan-2026-07-10.md:191-195`). Worst case that is roughly 40
sequential `osascript` invocations inside one MCP tool call.

Every one of those invocations serializes behind the single process-wide
`_MAIL_LOCK` in `plugin/apple_mail_mcp/core/applescript.py:28-29,45-46` — the plan commits
to sharing this exact lock with Mail (plan section 2, RR section 7.3: "share
`core.run_applescript` and its lock in 3.10.0"). Each call is capped at the default 120s
AppleScript timeout (`core/applescript.py:44`, `effective_timeout = 120 if timeout is None
else timeout`), but nothing in the plan caps the *sum*. A single `list_events()` call with
no arguments against a dense multi-calendar account could legitimately run for many
minutes, and every Mail tool call issued by the same agent session during that window
queues behind `_LOCK_WAIT_TIMEOUT` (300s, `core/applescript.py:29`) before failing outright.

Open Risk 6 (`plan-2026-07-10.md:857-859`) acknowledges lock sharing as a general risk but
does not name this specific multiplier (fan-out × two-pass recurring expansion), and
nothing in `CALENDAR_BOUNDS` (`plan-2026-07-10.md:459-477`) caps total elapsed time the way
`SCAN_BOUNDS`'s `SEARCH_HARD_CEILING` / `INBOX_HARD_CEILING` cap mail's worst case
(`plugin/apple_mail_mcp/constants.py:82-113`, added specifically after "cold-cache property
reads... blew past wrapper timeouts" per the comment at lines 82-88).

**Recommendation:** add an aggregate per-call deadline (e.g. `CALENDAR_CALL_BUDGET_SECONDS`)
that the fan-out loop checks between calendars and returns partial results plus
`calendar_errors` for the remainder, rather than relying only on per-osascript-call
timeouts.

### F3 (minor, CONFIRMED) — default calendar scoping is opt-out fan-out, unlike mail's opt-in fan-out

`tools/CLAUDE.md` documents mail's account-scoping default precisely: "`account: Optional[str]
= None` → `server.DEFAULT_MAIL_ACCOUNT`; error if unset." Fan-out across all accounts is an
explicit opt-in (`all_accounts=True`). The calendar plan inverts this: `list_events` and
`check_availability` default `calendar=None`/`calendars=None` to "all calendars, capped
fan-out" (`plan-2026-07-10.md:163,231`) with no error path when neither a param nor
`DEFAULT_CALENDAR` is set. An agent calling `list_events()` with zero arguments gets a
silent 20-calendar scan by default, where the mail-side equivalent would refuse outright
absent a configured default. See F13 for the tool-surface-confusion angle on the same
issue.

---

## 2. Safety holes

### F4 (blocker, CONFIRMED) — the plan's central safety claim, "mirrors the existing mail architecture," is false for every destructive/write mail tool except send

Plan section 1 states the split "tools enforce, skills teach... is exactly the existing
mail architecture" (`plan-2026-07-10.md:47`). Plan section 3.9 justifies `manage_calendars`'
delete gating by saying it is "multiplexed like `manage_trash`" (`plan-2026-07-10.md:384`).
RR section 3.3 says "internal guards are mandatory defense-in-depth, not optional" as a
description of existing mail practice (`research-report-2026-07-10.md:207-208`).

Verified against source: a repo-wide grep for every reference to `READ_ONLY` or
`DRAFT_SAFE` inside `plugin/apple_mail_mcp/` (excluding tests) returns exactly these
locations:

- `plugin/apple_mail_mcp/server.py:100,104` — the flag declarations.
- `plugin/apple_mail_mcp/__main__.py:55-56,61-64` — CLI sets the flags and removes
  `SEND_TOOLS` from the FastMCP registry under `--read-only`.
- `plugin/apple_mail_mcp/tools/compose/manage.py:229,231` — one check.
- `plugin/apple_mail_mcp/tools/compose/helpers.py:254-262` (`_send_blocked`) — the only
  gating function in the codebase, and it only fires `if mode != "send": return None`, i.e.
  it gates nothing except the actual send path of `compose_email` / `reply_to_email` /
  `forward_email` (`SEND_TOOLS`, `server.py:85`).

None of `move_email` (`tools/manage/move.py`), `update_email_status`
(`tools/manage/status.py`), `create_mailbox` (`tools/manage/mailbox.py`), or
`manage_trash` (`tools/manage/trash.py`) reference `_server`, `READ_ONLY`, or
`DRAFT_SAFE` anywhere — verified by reading `tools/manage/trash.py` in full: its only
safety mechanisms are `dry_run` defaults, `max_deletes`, and `confirm_empty`, none of
which are wired to server mode. Root `CLAUDE.md`'s own description confirms this is
intentional today: "`--read-only` Disable tools that send email... Drafts can still be
created and listed" — no mention of trash, move, or mailbox creation, because those are
unaffected.

Concretely: `manage_trash(action="delete_permanent", dry_run=False, message_ids=[...])`
permanently deletes mail today whether or not the server was launched with `--draft-safe`
or `--read-only`. The plan is proposing genuinely new, stricter safety plumbing for
calendar deletes and invitation sends (which is a reasonable design on its own merits) but
is describing it as reuse of an existing pattern that does not exist for the equivalent
mail actions. Practical consequence: after this ships, `--draft-safe` and `--read-only`
will mean materially different things depending on which tool domain is called — "blocks
nothing but sends" for mail, "blocks deletes and attendee sends" for calendar, under the
identical two CLI flags and the identical two `server.py` booleans. An operator or a skill
reasoning generically about "am I safe in this mode" will get the wrong answer for one
domain or the other. See F12 for the tool-surface-confusion framing of the same fact.

**Recommendation:** state explicitly in the plan and in the calendar skills that
`--draft-safe`/`--read-only` are being *extended* to cover a class of action (destructive
calendar writes, attendee sends) that they do not cover for the equivalent mail actions
today, and flag this as a product decision for the owner rather than an implementation
detail. Separately consider whether mail's own `manage_trash`/`move_email` should get
equivalent gating in the same release for consistency, as an explicit, separately-scoped
decision, not a silent scope expansion mid-implementation.

### F5 (major, CONFIRMED as a spec gap) — `update_event`'s attendee-gating is unspecified for the PATCH-diff case and can force self-inflicted re-invites

`update_event`'s attendee parameter is documented only as "same gating as `create_event`"
under PATCH semantics where "`None` = leave unchanged" (`plan-2026-07-10.md:336-338`). The
spec does not say what happens when a caller passes the *unchanged* current attendee list
(e.g., because some other field also needs to change and the caller wants to be safe and
explicit). Since AppleScript exposes no delivery-tracking state (RR 4.2,
`research-report-2026-07-10.md:298-318`: attendee attachment is possible but delivery is
never guaranteed and never distinguishable as "first send" vs "resend" from the object
model alone), a naive implementation that gates on "attendees is not None and non-empty"
will force every edit that merely echoes the current attendee list through
`INVITE_SEND_REQUIRES_CONFIRM` / `send_invitations=True`, the identical path a brand-new
invite takes. An agent that reflexively passes `send_invitations=True` to get past that
gate on a routine reschedule risks Calendar.app re-transmitting invitations to attendees
who already have the event — a real annoyance/spam vector produced by the gate's own
design, not by malicious input. This needs to be resolved (e.g., gate only on an actual
diff against the previously-stored attendee set) before `calendar_core/validation.py` and
`tools/calendar/events_update.py` can be implemented as specified.

### F6 (minor, CONFIRMED as spec ambiguity) — the fuzzy calendar-name resolution rule is self-contradictory as written

Section 3 preamble states: "Calendar name params are fuzzy-resolved for reads and create
targets only (exact case-insensitive match wins; multiple candidates return
`AMBIGUOUS_CALENDAR_SELECTOR`... no match returns `CALENDAR_NOT_FOUND` with close
candidates)" (`plan-2026-07-10.md:137-141`). "Exact case-insensitive match" cannot, by
itself, ever produce "multiple candidates" (two calendars differing only by case is an
edge case, not the general mechanism implied) or "close candidates" on no-match — those
outcomes require actual fuzzy matching (substring, prefix, or edit-distance), which is
never named. There is no fuzzy-matching helper anywhere in
`plugin/apple_mail_mcp/core/` today (confirmed: grep for `fuzzy|difflib|SequenceMatcher`
across `core/*.py` returns nothing), so this would be new code with an unspecified
algorithm. Severity is contained because section 4.4/3.9 correctly disable fuzzy resolution
entirely for destructive targets (`manage_calendars` rename/delete require exact name or
`calendar_id`), so this ambiguity cannot itself become a safety hole, only an
implementation blocker for reads/creates.

---

## 3. API misassumptions verified against repo source

### F7 (blocker, CONFIRMED) — "internal guard... exactly like `SEND_TOOLS`" describes code that does not exist

This is the same underlying fact as F4, restated because it is independently a wrong claim
about the codebase, not just a safety-framing problem. Plan section 4.2
(`plan-2026-07-10.md:499-502`): "Every write/delete tool keeps an internal
`_server.READ_ONLY` / `_server.DRAFT_SAFE` guard (the `_send_blocked` pattern) because the
CLI bypasses registry removal." In reality `_send_blocked`
(`tools/compose/helpers.py:254-262`) is the *only* such guard in the repository, and it
exists solely to gate `mode="send"` inside the three `SEND_TOOLS`. There is no generic
destructive-tool CLI guard to copy; implementing `_calendar_write_blocked` /
`_calendar_delete_blocked` (`plan-2026-07-10.md:754-755`) is 100% new code, not a port of an
existing helper. This changes the actual scope/risk of Wave 3 item 13
(`tools/calendar/helpers.py`) from "adapt an existing pattern" to "design and test a new
safety primitive," which deserves its own test-coverage attention beyond what the plan's
test plan implies by analogy.

### F8 (minor, CONFIRMED) — "text is mail's default `output_format`" overstates uniformity

Plan section 3 preamble: json-as-default for calendar reads is called "a deliberate
deviation from mail's text default" (`plan-2026-07-10.md:132-133`). True for most tools, but
not universal: `get_email_by_ids` already defaults to `"json"`
(`plugin/apple_mail_mcp/tools/search/by_id.py:453`), as does the disabled-refusal shim
`full_inbox_export` (`plugin/apple_mail_mcp/tools/analytics/full_export.py:28`), and
`inbox_dashboard` defaults to `"ui"`, not `"text"`
(`plugin/apple_mail_mcp/tools/analytics/dashboard.py:173`). Low stakes (doesn't change any
implementation decision) but worth correcting in the final docs so "json default" isn't
framed as the sole exception in the codebase.

### F9 (minor, CONFIRMED) — the "chunking" language for `get_events_by_id` describes a code path that can never execute

Plan section 3.3 (`plan-2026-07-10.md:205,221-222`) caps `event_ids` at
`MAX_EVENT_IDS_PER_CALL` (25) as the *input* limit, then separately says
"`WHOSE_ID_LIST_TOO_LARGE` semantics enforced by chunking ids 25 per osascript call." Since
the input cap and the chunk size are the same number (25), a single call can never produce
more than one chunk, so the "chunking" framing (borrowed from mail's real multi-batch
`iter_id_chunks` pattern, which fires above `MAX_WHOSE_IDS`=50 and genuinely loops,
`tools/CLAUDE.md` "Forbidden AppleScript patterns" table) doesn't apply to this tool as
specified. Cosmetic, but worth tightening so the eventual docstring doesn't claim batching
behavior nothing exercises.

### F10 (minor, CONFIRMED) — release plan's skill-count hand-edit list misses one live location

Plan section 6.4 (`plan-2026-07-10.md:636-640`) lists seven files to hand-edit for the
skill-count claim and provides a re-grep command
(`grep -rn "nine\|9 workflow" --include="*.md"`). Running that exact command against the
current tree surfaces an eighth, uncounted hit the plan's list omits:
`tasks/reference/phase-plan-3.1.7.md:17` and `:126`. Line 126 names all nine skills
individually ("nine skills under `plugin/skills/` (operator, triage, management, taxonomy,
archive, rules advisor, drafting, style profile, attachments)"). Per `tasks/CLAUDE.md`,
`reference/` files are "durable specs/backlogs still cited by code, CHANGELOG, or docs" and
are explicitly *not* archive — this file is live, not historical scrap. Since skill-count
claims are hand-maintained with no automated gate (RR section 3.5 confirms: "None of these
are enforced by an automated gate"), this location will silently go stale at "nine" once
the calendar skills ship, and nothing in CI will catch it.

### F11 (minor, CONFIRMED — process hygiene, not a code defect) — every phase-1/phase-2 artifact filename carries a literal "-undefined" suffix, and `tasks/todo.md` now links to it

`plan-2026-07-10.md`, `research-report-2026-07-10.md`, and all five files under
`tasks/active/apple-calendar-tools/reports/phase1-*-2026-07-10.md` share the same broken
naming pattern, almost certainly a failed date/variable substitution (`tasks/CLAUDE.md`'s
convention is dated files like `handoff-YYYY-MM-DD.md`). `tasks/todo.md`'s pending diff
(uncommitted, checked via `git diff`) already hard-links these exact filenames as the
durable pointer for the workstream ("research consolidated in
[`research-report-2026-07-10.md`]... implementation plan... in [`plan-2026-07-10.md`]").
`python3 tools/validators/validate_tasks_layout.py` passes clean against the current tree
(bucket-only enforcement, no filename-dating check), so nothing blocks this from shipping
as-is. Recommend renaming to dated files before this becomes the permanent cross-linked
record; low severity but avoidable and already load-bearing in `todo.md`.

Separately, `tasks/active/apple-calendar-tools/verify-venv/` is a 43 MB PyObjC virtualenv
(compiled `.so` binaries for `AppKit`/`Cocoa`/`EventKit`) sitting inside the tracked
workstream directory from the live-probe work. It carries its own nested `*` `.gitignore`
and `git check-ignore -v` confirms it is currently excluded, so it is not an active risk,
but a compiled-binary venv living inside a documentation folder is a landmine for a future
`git add -A -f` or an agent that doesn't notice the nested ignore rule. Move it outside the
repo tree (scratchpad or `/tmp`) rather than relying on the nested `.gitignore`.

### Claims independently verified accurate (listed for balance, not defects)

- Tool count 31 (recursive `^@mcp.tool` scan) and expected test count 1053 both match
  live command output in this session.
- `tests/fixtures/module_line_budget/baseline.json` is exactly `{"threshold": 600,
  "modules": {}}` as claimed.
- `ACTIVE_DOC_TOOL_COUNT_REQUIRED` in `tools/manifest_checks/common.py:43-59` matches the
  plan's 11-file list exactly, including the two non-obvious entries
  (`apple-mail-mcpb/build-mcpb.sh`, `tools/manifest_checks/artifacts.py`).
- `TOOL_COUNT_CLAIM_PATTERNS` (`tools/manifest_checks/common.py:64-68`) is indeed the blunt
  digit-before-"tool(s)" regex the plan's Open Risk 8 warns about.
- All four `ToolAnnotations` presets the plan cites (`READ_ONLY_TOOL_ANNOTATIONS`,
  `WRITE_TOOL_ANNOTATIONS`, `IDEMPOTENT_WRITE_TOOL_ANNOTATIONS`,
  `DESTRUCTIVE_TOOL_ANNOTATIONS`) exist verbatim in `server.py:57-83`.
- The hook `.claude/hooks/check_applescript_compiles.py:20,162` does hardcode the literal
  `tell application "Mail"` string as its full-script marker, exactly as RR 3.4 claims.
- Skill-authoring convention (directory name == frontmatter name, third-person description
  ending "Do NOT use for X") matches `docs/CLAUDE-conventions.md:230-231` verbatim.
- PR #70 (`fix/skill-example-accuracy`) is real, currently OPEN, targets `main`, and bumps
  to v3.9.4 as the plan describes (`gh pr view 70`: state OPEN, mergeable MERGEABLE);
  current `pyproject.toml` on this branch is still 3.9.3, consistent with the plan's
  conflict-handling note in section 6.8.
- `normalize_message_ids` (`core/normalization.py:41-52`) is indeed numeric-only
  (`.isdigit()`), confirming RR's claim that mail id primitives cannot be reused verbatim
  for string calendar UIDs.
- `bounded_inbox_scan` (`bounded_scan.py:62-120`) is a real, working precedent for the
  "sanctioned window producer" pattern the plan wants to mirror with
  `bounded_calendar_window`.

---

## 4. Tool-surface critique

### F12 (major) — same fact as F4, framed as a cross-domain confusion risk rather than a safety-narrative error

Once shipped, the two flags `--draft-safe` / `--read-only` and the two booleans
`server.DRAFT_SAFE` / `server.READ_ONLY` will carry different semantics depending on which
tool domain is in play: mail (blocks only the 3 send tools) versus calendar (blocks 5
write/destructive tools plus attendee sends). This is a materially harder mental model for
an agent (or a human skimming server instructions) than either domain alone. The FastMCP
server's own `instructions` string (`server.py:44-52`) currently describes only the
single-threaded/serialization behavior, not mode semantics at all, so there is no single
place a caller can read "here is what draft-safe actually blocks in this server" — it would
now require domain-specific knowledge from two different skills. Recommend either
documenting the domain split explicitly in the server's `instructions` string and both new
skills, or reconsidering scope so mail's equivalent destructive actions get the same
treatment in the same release (a bigger, separately-scoped decision, not to be smuggled in
silently).

### F13 (minor) — restated from F3: default-scoping semantics diverge from the mail-tool convention an agent will already have learned

`account=None` on mail tools means "use `DEFAULT_MAIL_ACCOUNT`, else error." `calendar=None`
on `list_events`/`check_availability` means "fan out across up to 20 calendars." Same
parameter shape (`Optional[str]`, same env-var-default pattern per `DEFAULT_CALENDAR`),
opposite default failure mode. An agent that has internalized "no default configured means
I need to be explicit or I'll get an error" from working with mail tools will be
legitimately surprised that the calendar equivalent silently scans everything instead.

### F14 (minor, SPECULATIVE) — tool count itself is defensible, no material objection

Ten new tools covering list/get/availability/create/batch-create/update/delete/manage/rsvp
is a reasonable count given mail ships 31 tools across 6 domains for a comparable
capability surface, and the plan's own requirement-coverage table
(`plan-2026-07-10.md:433-445`) maps every owner requirement to a specific tool with no
obvious gaps or redundant overlaps. This is listed as an explicit non-finding: reviewers
should not spend further cycles second-guessing tool count without new evidence.

---

## 5. Release-gate misses

### F15 — folded into F10 above (skill-count hand-edit list gap).

### F16 (minor, CONFIRMED — verification, not a gap) — PR #70 interaction claims hold up

Covered under "Claims independently verified accurate" above; included here because the
task specifically asked about release-gate misses and this is the one release-adjacent
claim that could plausibly have been stale or wrong. It is not.

### F17 (minor, CONFIRMED — process hygiene) — same as F11's second half: `verify-venv/` inside the tracked workstream directory

Restated here because it is also a release-gate-adjacent risk: `validate_repo_root.py`
(per `tools/CLAUDE.md`) enforces a tight allowlist at the *repository root*, not inside
`tasks/`, so a 43 MB compiled-binary venv under `tasks/active/apple-calendar-tools/` would
not be caught by that gate if it were ever force-added. It currently is not force-added
and is correctly ignored; the residual risk is purely "someone runs `git add -A -f` or
disables the nested `.gitignore` without noticing." Move it out of the repo tree.

### No other release-gate items were missed

Verified and found accurate/complete: the six version-file list
(`plan-2026-07-10.md:618-622`) matches every version-bearing manifest found by grep in this
session (`pyproject.toml:7`, `plugin/.claude-plugin/plugin.json:4`,
`plugin/.codex-plugin/plugin.json:3`, `.claude-plugin/marketplace.json:18`,
`server.json:9,14`, `apple-mail-mcpb/manifest.json:4`), correctly excluding
`.claude-plugin/marketplace.json`'s separate `metadata.version` field
(`.claude-plugin/marketplace.json:9`, value `1.0.0`, unrelated to the package version) as
the plan explicitly notes it should. `dev-check.sh`'s tier list, the CHANGELOG heading-match
enforcement in `tools/manifest_checks/version.py:30-56`, and the module-line-budget
regression gate all match the plan's description exactly. `tasks/` layout compliance
passes clean today (`validate_tasks_layout.py` exit 0).

---

## Severity roll-up

| # | Finding | Category | Severity | Status |
|---|---|---|---|---|
| F1 | Recurring-master `whose` pass may not be cost-bounded despite date window | Churn/spin | Major | CONFIRMED |
| F2 | No aggregate wall-clock budget on multi-calendar fan-out | Churn/spin | Major | CONFIRMED |
| F3 | Calendar scoping defaults to opt-out fan-out, unlike mail | Churn/spin | Minor | CONFIRMED |
| F4 | "Mirrors mail architecture" is false for destructive/write gating | Safety | Blocker | CONFIRMED |
| F5 | `update_event` attendee-gating undefined for PATCH-diff/no-op case | Safety | Major | CONFIRMED (spec gap) |
| F6 | Fuzzy calendar-name rule is self-contradictory as written | Safety | Minor | CONFIRMED (spec gap) |
| F7 | "Internal guard exactly like SEND_TOOLS" describes nonexistent code | API misassumption | Blocker | CONFIRMED |
| F8 | "Text is mail's default output_format" overstates uniformity | API misassumption | Minor | CONFIRMED |
| F9 | `get_events_by_id` "chunking" language describes unreachable code path | API misassumption | Minor | CONFIRMED |
| F10 | Skill-count hand-edit list misses `tasks/reference/phase-plan-3.1.7.md` | Release gate | Minor | CONFIRMED |
| F11 | "-undefined" filenames already cross-linked from `tasks/todo.md`; stray venv in tracked folder | Process hygiene | Minor | CONFIRMED |
| F12 | `--draft-safe`/`--read-only` now mean different things per tool domain | Tool surface | Major | CONFIRMED (same fact as F4) |
| F13 | `calendar=None` default diverges from mail's `account=None` convention | Tool surface | Minor | CONFIRMED (same fact as F3) |
| F14 | Tool count (10 new) is defensible | Tool surface | N/A | Non-finding |
| F16 | PR #70 interaction claims verified accurate | Release gate | N/A | Verified, not a defect |
| F17 | `verify-venv/` inside tracked workstream folder | Release gate | Minor | CONFIRMED |

Two blockers (F4/F7, the same underlying misrepresentation of existing safety plumbing
told twice), two majors that are really one fact told twice from different angles (F4/F12),
and two independent majors on churn/spin (F1, F2) that both trace back to the same root
cause the report itself already flagged in RR 4.6 but did not carry through to its own
mitigation design. Recommend resolving F4/F7/F12 as a single named decision (new gating
policy, explicitly scoped and documented, not "reuse") before Wave 2/3 implementation
starts, and resolving F1/F2 by adding either a calendar-specific static lint or an
aggregate per-call time budget before Wave 1 item 8 (`scripts_read.py`) is written.
