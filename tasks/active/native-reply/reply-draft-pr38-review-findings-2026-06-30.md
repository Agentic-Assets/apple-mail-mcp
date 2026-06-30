# PR 38 Reply Draft Review Findings

Date: 2026-06-30
Branch: `fix/reply-draft-verification-consolidated`
Original reviewed head: `7770fa3229f2bb5ae78848fc994a30208997e1e9`
Base reviewed: `origin/main` at `1482949`
PR: https://github.com/Agentic-Assets/apple-mail-mcp/pull/38

Status note: this review artifact is retained as historical context for PR #38.
The implementation has since addressed several items below, including verifier
timeout/error preservation of the known `draft_id`, fallback exactness metadata,
JSON send-mode rejection, and concrete default-signature tests. Treat the current
source and tests as authoritative.

## Scope

This report reviews the remaining branch delta after merging current `origin/main`
into `fix/reply-draft-verification-consolidated`.

The branch is now aligned with the ID-first mainline. It does not restore
keyword-targeted reply behavior. The remaining branch value is concentrated in:

- `reply_to_email` verified draft/open JSON output.
- reply draft verification metadata for body, signature, attachments, draft id,
  and verified draft id.
- exact-id reply draft regression tests.
- additive `get_email_by_id` content status fields.
- additive `get_needs_response` numeric Mail id plus Internet Message-ID output.

## Skills And Review Lanes Used

- `mail-scripting-dictionary`: checked Mail dictionary facts for `reply`,
  `outgoing message`, writable outgoing `content`, and `message signature`.
- `python-development:python-resilience`: reviewed timeout and verifier failure
  handling.
- `python-development:python-testing-patterns`: reviewed mocked test coverage and
  remaining proof gaps.
- `superpowers:dispatching-parallel-agents`: four read-only review lanes were
  dispatched:
  - signature and Mail dictionary semantics,
  - reply draft JSON and verification contract,
  - exact-id/search/smart-inbox branch delta,
  - resilience and live-proof gaps.

Fresh review pass on 2026-06-30:

- three additional read-only subagent lanes reviewed the current branch against
  current `origin/main`,
- one lane checked Mail dictionary and signature semantics,
- one lane checked reply draft JSON output and verifier resilience,
- one lane checked ID-first and performance risk in search and smart inbox,
- the lead agent rechecked the current branch, canonical PR state, diff,
  Mail `.sdef`, focused tests, manifest validation, and release gate.

All fresh lanes agreed that PR #38 remains valuable and aligned with current
`main`, but should not merge yet if the goal is the strongest reply draft
contract.

## Current Signature Behavior

`reply_to_email` defaults to `include_signature=True`.

When `DEFAULT_MAIL_SIGNATURE` or per-call `signature_name` resolves to a concrete
Apple Mail signature name, the tool:

- validates the signature before creating the reply,
- emits `set message signature of replyMessage to signature "..."`,
- saves the reply draft,
- verifies that some configured Mail signature text appears above the quoted
  original.

When `include_signature=True` and no concrete signature name is configured, the
tool does not set `message signature` to `missing value`. It lets Mail's own
account default behavior apply, then verifies whether any configured Mail
signature text is present above the quote.

When `include_signature=False`, the tool explicitly emits:

```applescript
set message signature of replyMessage to missing value
```

and still inserts `reply_body` above the quoted original. This remains a useful
explicit opt-out for tests and one-off calls, but Cayman preference should be to
leave the default as `include_signature=True`.

### Signature Decision

Cayman wants signatures included by default. The safest operational setting is:

```text
DEFAULT_MAIL_SIGNATURE=<exact Apple Mail signature name>
```

Do not rely on Mail's implicit account default if a reliable signature guarantee
is required. The Mail dictionary supports setting a message signature by exact
signature object name, but it does not expose a clear account-default signature
contract for quiet replies.

## No Blocking Findings

The branch is directionally correct and still adds value beyond `main`.

Validated locally before this report:

- PR #38 is open and mergeable.
- `bash tools/dev-check.sh release` passed.
- `python3 tools/validate_manifests.py` passed.
- `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh` passed.
- Focused compose/search/read-only/id-first tests passed.
- No live Mail drafts were created during this review.

Fresh proof refreshed on 2026-06-30:

- Canonical PR #38 is open, non-draft, and mergeable:
  https://github.com/Agentic-Assets/apple-mail-mcp/pull/38
- `git diff --check origin/main...HEAD` passed.
- `PYTEST_ADDOPTS='' .venv/bin/pytest tests/test_compose_tools.py
  tests/test_mail_search_tools.py tests/test_smart_inbox_json.py
  tests/test_read_only_registry.py -q` passed.
- `python3 tools/validate_manifests.py` passed with version `3.7.1` and
  tool count `31`.
- `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh`
  passed with version `3.7.1` and tool count `31`.
- `bash tools/dev-check.sh release` passed:
  - `ruff check plugin/apple_mail_mcp/`,
  - `ruff format --check plugin/apple_mail_mcp/`,
  - `mypy --strict plugin/apple_mail_mcp/`,
  - artifact rebuild and validation,
  - full pytest,
  - wrapper check skipped because no generated wrapper was on `PATH`.
- Sensitive-data scan over changed source, tests, skill docs, task report, and
  task index returned no matches.
- No live Mail drafts were created and no email was sent.

The release gate rebuilt package artifacts, but the working tree showed no
tracked artifact drift afterward.

### Current Merge Blocker

Historical note: the verifier-timeout blocker recorded in this section has been
resolved in the current branch. The remaining known correctness issues are tracked
by the current tests and review follow-up around attachment filename verification
and requested-signature verification.

## Before-Merge Todos

These are the review items most worth handling before merge if the goal is the
most robust reply draft system.

### 1. Preserve Known Draft ID On Verifier Timeout Or Error

Status: resolved in the current branch.

Current risk: `reply_to_email` may extract `Draft ID`, then
`_verify_saved_reply_draft` can time out or hit an AppleScript error and return a
generic verification failure without carrying the known draft id forward.

Why it matters: agents need the exact Drafts artifact id for cleanup or manual
inspection. Losing it recreates the old "something happened in Drafts" problem.

Recommended fix:

- Add `draft_id` to `_ReplyDraftVerification` for verifier timeout/error states
  when the caller supplied a draft id.
- Add a specific status/code such as `verification_timeout` or
  `applescript_error`.
- Ensure `_reply_draft_verification_error` returns the known exact draft id.
- Add a mocked test: main reply save returns `Draft ID: N`, verifier raises
  `AppleScriptTimeout`, returned error includes `N` and no email was sent.

Fresh reviewer consensus: required before merge.

### 2. Decide Fallback Verification Contract

Current behavior: exact Drafts id verification runs first, then a bounded newest
Drafts fallback may verify a different draft id. Tests currently allow
`verified_draft_id != draft_id`.

Why it matters: fallback is useful for Mail save/index races, but it is weaker
proof than exact artifact verification.

Recommended contract:

- Keep fallback, but expose it clearly.
- Add JSON field `exact_id_verified: true | false`.
- For text output, add a warning when `verified_draft_id != draft_id`.
- Add tests for both exact match and fallback match.

Fresh reviewer consensus: least disruptive path is to keep
`verification_status: "found"` and add `exact_id_verified`. Avoid replacing the
existing status with `found_fallback` unless callers are prepared for a new
status value.

### 3. Add Signature Default Tests For Cayman Workflow

Recommended tests:

- `DEFAULT_MAIL_SIGNATURE="TU"` and no per-call `signature_name`: assert the
  reply script applies `message signature of replyMessage to signature "TU"` and
  verifier uses `signatureWasRequested=true`.
- `include_signature=True` with no default signature configured and verifier
  returning `missing`: assert success is warning-bearing, not silent.
- JSON success with `signature_status="missing"` when a signature was requested.

Fresh reviewer consensus: this is important but not the primary merge blocker.
For Cayman preference, set `DEFAULT_MAIL_SIGNATURE` to the exact Apple Mail
signature name wherever this MCP runs.

### 4. Update Skill Docs For Full JSON Contract

`plugin/skills/email-drafting/SKILL.md` mentions
`reply_to_email(..., output_format="json")`, but it should list the full fields:

- `mode`
- `sent`
- `subject`
- `draft_id`
- `verified_draft_id`
- `verification_status`
- `body_present`
- `attachment_status`
- `attachment_count`
- `attachments_applied`
- `signature_status`
- `mailbox`
- `exact_id_verified`

### 5. Clarify `output_format="json"` With `mode="send"`

The current JSON success contract is for verified `draft` and `open` reply
creation. Since send mode is blocked in draft-safe mode and not draft-verifiable
the same way, either:

- reject `output_format="json"` with `mode="send"`, or
- document that send mode retains the legacy text response.

Add a focused test for the chosen behavior.

Fresh reviewer consensus: reject `output_format="json"` with effective
`mode="send"` before executing the main reply script, or keep text mode for send
and document it with a test. Rejection is cleaner because JSON currently means
verified draft or open artifact metadata.

### 6. Tighten `get_needs_response` ID Semantics

The branch changes JSON `message_id` in `get_needs_response` to the numeric Mail
id and adds `internet_message_id` for header correlation.

This aligns with ID-first action routing, but it is a compatibility shift for
callers that previously treated `message_id` as Internet Message-ID.

Recommended follow-up:

- Update docstrings and skill docs to say `message_id` is numeric Apple Mail id,
  `internet_message_id` is for header/replied correlation.
- Add a test where the new 8-field row shape preserves numeric `message_id`
  while `check_already_replied=True` matches by `internet_message_id`.

Fresh reviewer consensus: no slow keyword or substring targeting regression was
found. This item is a small contract cleanup, not a merge blocker for reply draft
correctness.

## Live Dummy Proof Needed

Mocks cannot prove Mail.app persistence behavior across accounts and providers.
They cannot prove real Drafts id visibility, real attachment state, file size
visibility, signature text detection, or race timing.

Before merge, run one controlled maintainer-only live proof unless Cayman accepts
the residual risk:

1. Use a non-sensitive dummy source message.
2. Call `reply_to_email` by exact `message_id`, `mode="draft"`,
   `output_format="json"`, `include_signature=True`, with a harmless dummy
   attachment.
3. Confirm:
   - `draft_id` is present,
   - `verified_draft_id` is present,
   - preferably `draft_id == verified_draft_id`,
   - `exact_id_verified == true` when `draft_id == verified_draft_id`,
   - if `exact_id_verified == false`, record both ids and treat the proof as
     fallback-only,
   - `verification_status == "found"`,
   - `body_present == true`,
   - `attachment_status == "verified"`,
   - `signature_status == "detected"` when `DEFAULT_MAIL_SIGNATURE` is set.
4. Run `verify_draft` by exact `draft_id` with expected body, attachment,
   signature, and quoted-original checks.
5. Delete the created dummy draft by exact id.
6. Verify the exact draft id is gone.

No live proof was run during this report.

## Resilience Recommendation

Do not add automatic retries around `reply_to_email` itself. Retrying a mutation
after an uncertain Mail timeout risks duplicate drafts. The current local
bounded verifier polling shape is appropriate for Mail save propagation.

Improve resilience by:

- preserving artifact ids in every failure,
- distinguishing exact-id verification from fallback verification,
- returning actionable structured status,
- cleaning up live proof artifacts by exact id.

## Merge Recommendation

PR #38 should not merge yet if the merge bar requires live Mail proof. Several
mocked-code blockers in this historical list have now been addressed. The
remaining high-value proof item is a controlled live dummy reply draft with
`DEFAULT_MAIL_SIGNATURE` configured, unless Cayman explicitly accepts mocked-only
residual risk.

After those are complete, PR #38 should be a strong merge candidate.

Recommended implementation order:

1. Add verifier failure metadata so `_verify_saved_reply_draft` preserves the
   known `draft_id` on timeout and AppleScript error.
2. Add `exact_id_verified` to reply JSON success payload and a text warning when
   fallback verifies a different Drafts id.
3. Lock `output_format="json"` plus `mode="send"` behavior with a focused test.
4. Add default-signature reply tests for configured signature and requested but
   missing signature.
5. Add the `get_needs_response` 8-field row test and docs clarification.
6. Rebuild artifacts and rerun `bash tools/dev-check.sh release`.
7. Run controlled dummy Mail proof only with exact draft cleanup approval.
