# Native reply attachment closeout (2026-08-10)

**Branch:** `fix/native-reply-attachment-verification`
**Base:** `origin/main` at `ed9e1ee1754b58101bcffabda4b7c06578db4823`
**Verification:** The recorded 1,676-test release gate applied to an earlier working-tree snapshot. Current proof requires `PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin bash tools/gates/dev-check.sh release`; the expected collection count is single-sourced in `tools/expected_test_count.txt`. It covers the universal attachment contract through mocked Mail/AppleScript boundaries; protected live verification remains open.
**State:** Local release-ready, not live-release-complete. No Mail send occurred. Live draft mutation was not run because no disposable fixture message was available.

## Goal

Make attachments dependable across standalone compose, reply, explicit-path forward, and rich EML drafts, while retaining the native-reply quote-preservation repair.

## What shipped

- `reply_scripts.py` types the authored body first, then adds attachments immediately before save.
- `saved_draft_checks.py` requires the authored body, source-attributed native quote, and requested attachment multiset; an exact attachment miss cannot fall back to another same-subject draft.
- `verification.py` returns structured quote and attachment failures, distinguishes exact persisted identities from suspect same-subject fallback artifacts, and never authorizes deletion from a suspect id.
- `reply.py` refuses every attachment-bearing reply send until a draft has been saved and verified; missing persisted identity cannot certify a fallback draft.
- `send.py` refuses attachment-bearing direct sends and verifies one same-operation marker-bound row or immediate strict Drafts readback for plain and HTML drafts; a numeric locator is optional and not durable.
- `forward.py` accepts only explicit attachment paths, never copies source attachments implicitly, and verifies one same-operation marker-bound row or immediate strict readback of recipients, filename multiset/count, and readability.
- `rich_draft.py` embeds explicit paths in multipart EML, labels EML-only output as unverified, and delegates supported Mail drafting to the standalone compose verification contract.
- Regression tests cover ordering, quote loss, source-specific quote proof, missing, unsupported, unreadable, and zero-byte attachments, exact-identity failures, direct-send refusal, standalone plain/HTML, explicit forward, rich EML, and fallback provenance.

## Decisions

- Attachment insertion stays in the first native compose transaction, but runs after successful typing and before save. Reopening and mutating a persisted draft would add a second Exchange identity and synchronization race.
- Quote or attachment failures retain the draft for inspection. Automatic delete-and-retype remains limited to proven body-placement failures.
- Direct attachment sends fail before Mail mutation because the send path has no saved artifact to verify.

## Verification limits

- No fixture-only live draft was available across the configured Mail accounts. Testing on founder, client, academic, or personal correspondence was deliberately refused.
- Source-attributed quote verification still needs disposable-fixture coverage across localized Mail renderings before release-complete status can be claimed.
