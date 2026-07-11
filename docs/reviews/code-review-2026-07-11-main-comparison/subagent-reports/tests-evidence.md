# Test and regression evidence review

Reviewer: test coverage and verification pass

Scope: `origin/main...HEAD`, concentrating on the new compose tests and
whether they execute the changed AppleScript contracts.

## Findings

### HIGH | Retry tests fabricate the missing native Draft ID

Location: `tests/compose/test_compose_tools.py:66-84` and
`tests/compose/test_compose_tools.py:2566-2639`.

The retry tests use `_saved_reply_draft_output(..., draft_id=...)`, which
injects a `Draft ID:` line that the generated native script never produces.
They therefore prove only the Python branch after an impossible production
input, not the complete native retry contract.

Fix: first fix the native template, then assert its emitted output contains a
captured Draft ID and test the retry flow against that generated contract.

### MEDIUM | The public quote-scoping tests omit authored `wrote:` text

Location: `tests/compose/test_draft_verification_helpers.py:142-195`.

The tests cover an ordinary attribution but do not cover `wrote:` in the new
reply body. That lets the false-negative public verification behavior survive.

Fix: add an assertion that `prices will adjust` is found above the real
attribution in `As Keynes wrote: prices will adjust. On Tue, Ann wrote:
original`.

### SUGGESTION | The compilation test does not discover the native reply builder

Location: `tests/cross_cutting/test_applescript_builders_compile.py:148-165`
and `plugin/apple_mail_mcp/tools/compose/reply_scripts.py:297`.

The discovery convention accepts names ending in `_script` and scripts that
start with `tell application "Mail"`. `_build_reply_native_window_applescript`
fails both filters because it starts with helper handlers, so the changed
native script is not covered by that suite. A manual `osacompile` run succeeds
today, but a future generated-script parse regression would not be caught by
the suite.

Fix: register this builder explicitly or broaden the discovery rule without
weakening the compilation test's existing coverage.

## Verification performed

- Focused compose and AppleScript-builder tests: 191 passed.
- `mypy --strict` over the changed compose and draft-verification modules: passed.
- Generated native reply AppleScript: `osacompile` passed.
- Manifest validator and release-contract test subset: passed.
- Full test collection: 1536, matching `tools/expected_test_count.txt`.
