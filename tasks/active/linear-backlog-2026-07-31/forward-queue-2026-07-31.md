# Forward queue after Linear backlog closeout (2026-07-31)

Candidate follow-up work surfaced during the backlog pass. This is a menu,
not a roadmap; verify each item before acting.

## Hardening

- **Run the protected native reply and draft-race workflows** (priority: high; confidence: verified missing proof). Exercise AGENTIC-781, AGENTIC-1192, AGENTIC-1989, and AGENTIC-1998 on a human-controlled Mail account using only draft-safe fixtures and exact post-action identity checks.
- **Capture a sanitized AGENTIC-1191 reproduction** (priority: high; confidence: verified blocker). The historical timeout report does not establish the current failure path, so record account type, bounded selector, timeout, and redacted outcome before changing scan or serialization code.

## Evaluation

- **Live-test the new Calendar participant filter** (priority: medium; confidence: unverified live integration). Use a calendar fixture with known attendee and organizer fields to confirm AppleScript and EventKit behavior agree without returning participant data in summaries.
- **Live-test EML attachment export against a disposable mailbox** (priority: medium; confidence: unit and package proof only). Confirm raw source preservation and the 25 MiB-per-file and 100 MiB-per-batch caps with non-sensitive fixture messages.

## Process

- **Obtain marketplace/UI admission evidence separately** (priority: low; confidence: known distribution boundary). The Codex runtime smoke validates installation and 41-tool registration, not marketplace UI behavior or a release promotion decision.
