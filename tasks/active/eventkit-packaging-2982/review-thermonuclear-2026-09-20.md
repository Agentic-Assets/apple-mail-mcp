# Thermo-nuclear review — AGENTIC-2982 branch (PR #107, v3.12.3): synthesized report

Date: 2026-09-20 · Scope: `origin/main...HEAD` (42 files, +766/−54) · Method: two
independent subagent passes (A: structural/abstraction/spaghetti; B:
contracts/boundaries/duplication/tests), then lead-agent verification of every
disputed fact against current code. No files edited by reviewers.

## Verdict

The branch works and is fully green (release tier, 2772 tests, live-verified),
but it ships **one mapping + one diagnostic** as a new module, two facade
entries, a duplicated engine selector, and a third copy of the reason taxonomy.
Nothing here is a ship-blocker for the packaging fix itself — but the cleanup is
cheap, behavior-preserving, and should land on this branch before merge so the
taxonomy never gets a chance to drift. **7 action items below** (5 code, 2
test); everything else the passes raised is explicitly declined with reasons.

## What the passes agreed on (accepted as fact)

- Doctor misreports the engine under `APPLE_MAIL_CALENDAR_ENGINE=eventkit` +
  unavailable (real engine raises; doctor prints `applescript`). One-case
  diagnostic lie — fix it.
- Pane/grant/`tccutil` strings exist in 3–5 places each; `guidance.py` is the
  right canonical home (pure strings, zero imports, no cycle risk).
- CLI imports engine-private `_ENGINE_ENV`; env normalization disagrees
  (engine: `.strip().lower() or "auto"`; doctor: raw echo + one-sided compare).
- `list_calendars` docstring omits `restricted`, `status_check_failed`,
  `status_{N}`, `full_access`; `eventkit.py` docstring omits `status_{N}`.
- Doctor test has a tautological assertion (`"active" in out.out` matches
  `"inactive (...)"` too) plus stale comments; sync-contract test stubs the seam
  it should guard; offline test doesn't pin the `darwin` marker.

## Where the passes disagreed (adjudicated by lead)

| Dispute | Ruling |
|---------|--------|
| A wants `eventkit_next_step` folded into `eventkit.py` (F1); B says `guidance.py` is the correct layer | **B wins.** `guidance.py` imports nothing and is importable from engine, tools, and CLI with no cycle. Keep the module; fix the dispatch shape instead (action 1). |
| A wants the `calendars_list` default-resolution collapse reverted to helpers (F5) | **Declined.** Verified: `helpers.py` (193–215) does *different* handling, not duplicated handling (B confirms: "no duplication found"). The collapsed form is 6 lines, tested, and moving it into a file at exactly 600/600 LOC is budget-hostile. Leave it. |
| A wants both `getattr(engine, "default_calendar_id")` guards deleted (F7) | **Accepted but sequenced.** Verified `FakeReadEngine` implements `default_calendar_id` (conftest.py:111), so deletion is safe. But it's pre-existing code, not this branch's doing — fold into action 5 only if zero-risk, else defer. |
| A wants a shared `build_eventkit_diagnostic()` for tool + doctor (F3); B says payloads differ legitimately | **Declined.** The tool's nested `eventkit_available` object and the doctor's flat gate report are different contracts for different consumers; a shared builder buys a coupling neither needs. The real duplication is the *strings*, fixed by action 2. |
| A flags double-probe TOCTOU (F4); B says leave it (F12) | **B wins.** Synchronous non-prompting read; threading the reason through the `CalendarReadEngine` protocol widens a shared interface for negligible gain. No action. |
| A wants `cli/calendar_commands.py` split (F10) | **Declined.** 565 lines, two commands, mechanical wiring — a split now is motion without simplification. Revisit at the third calendar command. |

## Action items (in fix order)

**1. Single-dispatch the reason taxonomy in `guidance.py`** (A-F1/F6).
`reason.partition(":")` once, one `_NEXT_STEPS[base]` lookup, one fallback
formatter appending `({reason})`. Deletes both `startswith` branches and the
redundant `full_access` early-return. Files: `guidance.py:46-66`.
Behavior-preserving: yes. Tests: existing guidance tests must stay green
unchanged (they pin the strings, not the branches).

**2. Promote pane/grant strings to public constants + add a remediation builder**
(B-F2/F3, A-legibility). `guidance.py`: `_GRANT_COMMAND`/`_CALENDARS_PANE` →
public (`GRANT_COMMAND`, `PLUGIN_GRANT_COMMAND`, `CALENDARS_PANE`); add
`eventkit_denied_remediation(reason) -> dict[str, str]` returning the full
remediation dict. `engine.py:468-477` calls it (deletes the literal pane/grant
copies); `_cmd_calendar_grant` denied/restricted stderr (`commands.py:332-338`)
reuses the constants. `__all__` gains the new names; both facades re-export
them (they already re-export `eventkit_next_step`, so this is consistent, not
new tax). Behavior-preserving: yes — verify string-equality in tests.

**3. One shared engine-selection helper** (A-F2, B-F1/F5). `engine.py`: add
`resolve_engine_selection(*, available: bool, override: str | None = None) ->
tuple[str, str | None]` returning `(active_engine, override_warning)` —
`get_engine()` uses it for selection (logic unchanged, including the raise),
doctor uses it for reporting and surfaces `override_ineffective` in JSON + a
text line. This simultaneously fixes the forced-`eventkit` misreport, deletes
the doctor's inline ternary, removes the private `_ENGINE_ENV` import, and
unifies normalization (doctor echoes the *normalized* override). Files:
`engine.py:450-478`, `commands.py:392-410`. Behavior-preserving: yes except the
intended misreport fix (additive warning field).

**4. Doc-contract completions** (B-F4, docs-only). `calendars_list.py:40-43`:
list all eight labels (`full_access`, `dependency_missing`,
`not_determined`, `write_only`, `denied`, `restricted`,
`status_check_failed: …`, `status_{N}`). `eventkit.py:56-58`: add the
`status_{N}` fallback. `parser.py` doctor help: append "scripts must use
`--json`" (B-F10 nudge).

**5. Import hygiene** (A-F8, B-F11). Top-level imports in `commands.py` doctor
(`os`, `eventkit_status`, `load_frameworks`, `eventkit_next_step`,
`CALENDAR_BOUNDS`) and `engine.py` (`eventkit_next_step` / remediation builder
— no cycle possible, `guidance.py` imports nothing). Drop the now-dead
`getattr` guards at `calendars_list.py:63-64` **only** if the suite stays green
with zero test edits (FakeReadEngine already implements the method, verified).
`commands.py:409`: drop the defensive `int(...)` cast.

**6. Test fixes** (B-F6/F7, test-only, no count change).
- `test_calendar_doctor.py`: tighten `"active" in out.out` →
  `"EventKit fast path: active"`; inactive test asserts `"inactive
  (write_only)"`; add unknown-`status_9` JSON case, `engine_env="eventkit"` +
  unavailable case (locks in the action-3 warning), dependency-absent full
  payload assertions; delete stale comments (`:9-10`, `:22`).
- `test_list_calendars.py`: add ONE integration test without the
  `eventkit_next_step` stub asserting
  `payload["eventkit_available"]["next_step"] ==
  eventkit_next_step(reason)`; keep the stub for the other tests.
- `test_eventkit_guidance.py`: add bare `"dependency_missing"` and bare
  `"status_check_failed"` cases (different branches than the suffixed forms).
- `test_offline_runtime.py`: assert the eventkit lock entry carries
  `sys_platform == "darwin"`.

**7. Re-verify and rebuild.** `pytest` (count must stay 2772 — no tests
added/removed, only strengthened), `ruff check` + `format --check` + `mypy
--strict` on touched files, then `bash tools/gates/dev-check.sh release`
(rebuilds all three artifacts; the pre-push hook requires a fresh
`source-release-gate.sh` stamp for `plugin/` changes).

## Explicitly declined (do not implement)

- Fold `guidance.py` into `eventkit.py` (wrong layer; keep the module).
- Shared tool/doctor diagnostic builder (different contracts; couple the strings, not the shapes).
- Single-probe threading through `CalendarReadEngine` (interface churn, no gain).
- `cli/calendar_commands.py` split (premature at 2 commands).
- TypedDicts for the two payload dicts (inconsistent with repo convention).
- Revert of the `calendars_list` default-resolution collapse (no duplication; budget-hostile).

## Constraints for implementers

- `helpers.py` is at exactly 600/600 LOC — do not add lines there.
- 2772 test count must not change (strengthen, don't add/remove).
- `source-release-gate.sh` stamp is current for HEAD `83d19f1`; any new commit
  needs a re-stamp before push (pre-push hook enforces).
- Public-repo hygiene: synthetic fixtures only; the identity gate rejected one
  real address in a tasks note during this lane — check tasks files too.
