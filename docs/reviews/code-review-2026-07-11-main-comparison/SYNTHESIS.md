# Code review: main comparison, 2026-07-11

## Consolidated overview

Scope: `origin/main...HEAD` on
`fix/agentic-1214-reply-body-truncation` (four branch commits, 51 changed
files). The branch improves chunked native reply typing, full-body
verification, and the `manage_drafts` threading contract.

Executive summary at review time: do not merge unchanged. The new native
delete-and-retype safety net could not run in production because its
AppleScript template never returned the Drafts ID required by the guard. The
updated public verifier also misclassified ordinary authored text containing
`wrote:` as quote content.

The runtime, API, and test passes independently found the missing native
Drafts ID. The test pass adds that the mocked test output hides the failure.
The API and test passes independently reproduced the quote-boundary false
negative. No manifest, package-version, tool-registration, formatting, type,
or generated-AppleScript parse defect was found.

## Resolution addendum, 2026-07-11

All three findings are addressed in v3.11.2 on this branch.

- **CR-001 resolved:** The implementation rejected the proposed transient
  `outgoing message.id` shortcut after a live Exchange probe proved that it
  differs from the persisted Drafts ID. It instead emits a retryable Draft ID
  only after a complete bounded Drafts snapshot gains exactly one new RFC
  `Message-ID` whose `In-Reply-To` contains the source RFC token. Verification
  and deletion revalidate both headers at the numeric Drafts ID. Cap limits,
  indexing delay, ambiguity, malformed headers, and ID drift fail closed.
- **CR-002 resolved:** The public verifier recognizes Apple Mail attribution,
  an Outlook header block, and the Outlook original-message separator. A bare
  authored `wrote:` phrase has no quote-boundary effect; if no reliable
  boundary exists, verification checks the complete preview.
- **CR-003 resolved:** The cross-cutting suite now directly compiles the
  generated native reply builder with `osacompile`.

### Findings

#### HIGH | Native delete-and-retype is unreachable

Location: `plugin/apple_mail_mcp/tools/compose/reply_scripts.py:528-541`,
`plugin/apple_mail_mcp/tools/compose/reply.py:404-458`, and
`tests/compose/test_compose_tools.py:66-84`.

Impact: native success output has no `Draft ID:`. The caller receives
`draft_id=None`, so its exact-ID retry guard fails even when the verifier
identifies a `body_missing` artifact. It falls back to same-subject scanning,
which can verify an older draft instead of the new one. The live report
documents a concrete `body_missing` result with `retyped: false`.

Fix: capture `id of replyMessage` after save and before the draft window is
closed, emit it as `Draft ID:`, and replace response-only mocks with a test
that asserts the native generator provides the field.

#### MEDIUM | Quote-scoped verification falsely rejects normal authored text

Location: `plugin/apple_mail_mcp/tools/draft_verification.py:49-67`.

Impact: the first bare `wrote:` is treated as the quote boundary. A valid reply
such as `As Keynes wrote: prices will adjust. On Tue, Ann wrote: original`
reports that `prices will adjust` appears only in the quote. An agent can then
reject or delete a correct draft.

Fix: identify a real attribution boundary before flattening line structure, or
use whole-body matching when no unambiguous attribution is available. Add the
reproduced case to the public verifier tests.

#### SUGGESTION | Add direct compilation coverage for the native reply builder

Location: `tests/cross_cutting/test_applescript_builders_compile.py:148-165`.

Impact: the test's discovery convention excludes
`_build_reply_native_window_applescript`, so the newly changed native script is
only covered by a local hook and manual review.

Fix: explicitly include the builder or extend the discovery rule to accept its
safe helper-prefixed layout.

## Supporting reports

| Report | Reviewer type | Contribution |
| --- | --- | --- |
| `subagent-reports/reply-correctness.md` | Runtime correctness | Traced native typing, output, and retry flow. |
| `subagent-reports/api-contract.md` | MCP API and integration | Checked public verification and packaging contracts. |
| `subagent-reports/tests-evidence.md` | Test and regression | Validated test realism, suite scope, and static proof. |

## Consolidated TODO list

- [x] **CR-001:** replace the unsafe native ID assumption with persisted,
  header-linked Drafts identity and fail-closed cleanup.
- [x] **CR-002:** recognize only reliable quote boundaries.
- [x] **CR-003:** compile the generated native reply builder in the suite.

## Audit block

Reviewers and subagents used: runtime correctness, MCP API and integration,
and test and regression evidence. Skills invoked: `code-review-command`,
`reviewing-code`, `python-anti-patterns`, `testing-python`, and `mcp-builder`.

Verification: `git diff --check origin/main...HEAD`; focused compose,
draft-verification, threading, and AppleScript-builder tests (191 passed);
strict mypy for the changed compose surface; generated native AppleScript
compiled with `osacompile`; manifest validation and release-contract tests;
1536 collected tests matching the expected count.

Artifact manifest:

- `SYNTHESIS.md`
- `subagent-reports/reply-correctness.md`
- `subagent-reports/api-contract.md`
- `subagent-reports/tests-evidence.md`
