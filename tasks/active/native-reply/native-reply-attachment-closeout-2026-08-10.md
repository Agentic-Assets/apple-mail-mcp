# Native reply attachment closeout (2026-08-10)

**Branch:** `fix/native-reply-attachment-verification`
**Base:** `origin/main` at `ed9e1ee1754b58101bcffabda4b7c06578db4823`
**Verification:** `PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin bash tools/gates/dev-check.sh release` passed locally with 1,652 tests, strict mypy, Ruff, manifest validation, rebuilt artifacts, and offline ZIP/MCPB runtime smokes.
**State:** Verified and ready for commit/PR. No Mail send occurred. Live draft mutation was not run because no disposable fixture message was available.

## Goal

Fix native reply drafts that lost their rich quoted original or silently omitted a requested attachment when Mail added the attachment before the focus-guarded body typer.

## What shipped

- `reply_scripts.py` types the authored body first, then adds attachments immediately before save.
- `saved_draft_checks.py` requires the authored body, native quote, and requested attachment multiset; it retries transient Mail/Exchange attachment materialization.
- `verification.py` returns structured quote and attachment failures, distinguishes exact persisted identities from suspect same-subject fallback artifacts, and never authorizes deletion from a suspect id.
- `reply.py` refuses direct native sends with attachments until a draft has been saved and verified.
- Regression tests cover ordering, quote loss, missing and unsupported attachments, direct-send refusal, empty-body attachment replies, transient attachment state, and fallback provenance.

## Decisions

- Attachment insertion stays in the first native compose transaction, but runs after successful typing and before save. Reopening and mutating a persisted draft would add a second Exchange identity and synchronization race.
- Quote or attachment failures retain the draft for inspection. Automatic delete-and-retype remains limited to proven body-placement failures.
- Direct attachment sends fail before Mail mutation because the send path has no saved artifact to verify.

## Verification limits

- No fixture-only live draft was available across the configured Mail accounts. Testing on founder, client, academic, or personal correspondence was deliberately refused.
- The quote sentinel remains the English Mail attribution token `wrote:`. Localized Mail attribution requires a separate design and fixture matrix.
