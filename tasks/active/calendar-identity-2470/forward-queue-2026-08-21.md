# Forward queue after AGENTIC-2470 (2026-08-21)

Candidate follow-up work from the calendar identity fix. This is a menu, not a
roadmap; verify each item before acting.

## Robustness

- **Map Calendar.app Automation denial on read paths** (confidence: verified
  gap)
  Calendar read builders should return the same structured access-denied
  envelope as writes when macOS Automation returns `-1743`. This is adjacent
  to, but independent of, duplicate-calendar selection.

## Evaluation

- **Run a disposable-fixture Calendar.app acceptance check** (confidence:
  pending live verification)
  After approval to use a disposable calendar fixture, verify that an opaque
  identifier targets the intended same-named calendar through create and
  exact-ID readback. Do not alter an existing event while performing this
  check.
