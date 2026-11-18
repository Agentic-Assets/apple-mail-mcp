---
name: test-audit
description: "Writing/changing/reviewing/sweeping tests: reject duplicate or implementation-coupled proof; remove test-only production seams."
---

# Test Audit

- Authoring gates every new/changed test.
- Audits: remove low-value proof in coherent owner batches; never chase deletion counts.
- Whole-subsystem campaign: read [CAMPAIGN.md](CAMPAIGN.md) first.

## Authoring gate

Before adding test, answer:

1. Protected observable behavior, invariant, or independent contract?
2. Credible regression that makes it fail?
3. Why existing proof misses it? Each contract: one primary owner at strongest
   boundary. Another layer needs distinct transport/lifecycle risk. Extend
   tables/shared fixtures; consolidate repeated setup.
4. Export, flag, wrapper, injection hook with no production caller needed?
   Move proof to real boundary instead.

- Use the [repository test guide](../../../tests/CLAUDE.md). Mock Mail.app or EventKit at
  the I/O boundary. Keep parsing, paging, and file writes real where they are the contract.
  Use native Mail-free AppleScript with synthetic objects when the regression lives in
  emitted script behavior, and retain the compiler gate for generated syntax.
- Regressions must fail pre-fix for intended reason, pass after repair; report
  unavailable baseline proof.
- One owner regression covers bug; never replay at every crossed layer.
- Behavior-preserving refactors should not break test.

## Junk patterns

- Reject self-comparisons, copied inventories, source greps,
  implementation/call-shape assertions, duplicate contracts, test-only seams,
  subject-generated expected values, mocks supplying asserted behavior.
- Fixtures must not fabricate owner receipts/order or claim persistence path
  never writes.
- Negative controls must fail at intended guard; names must match exercised behavior.
- Auditing candidates: use [full checklist](references/audit.md#junk-patterns).
- For a dropped-row, partial-result, or file-write regression, assert the actual
  IDs, completion state, or bytes. Exercise tied ordering across pages, failures
  after an earlier success, repeated names, and retries where those risks apply.

## Retention bar

- Keep independent API, SDK, protocol, config, migration, storage, security,
  platform, default, prompt-byte, generated-language, package, release,
  architecture, observable-ordering contracts. Static/slow ≠ disposable.
- Source inspection must survive identifier-only refactors and guard independent
  user-facing contract.
- Baseline failures may be bugs to repair, not tests to delete.

## Audit and validation

- Before audit edit: read [candidate evidence and workflow](references/audit.md#candidate-evidence);
  inspect full owners/callers/history; record remaining proof before deletion.
- Delete obsolete production seams with tests; never optimize for deletion counts.
- Run owner and sibling tests with `.venv/bin/pytest`. Recount with
  `PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests` if collection changes
  and update `tools/expected_test_count.txt`. Use the local gate for the changed
  surface and [bounded live verification](../../../docs/AGENT_LIVE_TESTING.md)
  for runtime changes. Never run concurrent Mail calls or publish real mail output.
- Report causes, retained contracts, proof/cost, production versus test LOC,
  follow-ups. Commit/push/land only with task authority.

Adapted from the [OpenClaw test-audit skill](https://github.com/openclaw/openclaw/tree/2b58c8e5224ee0d2d9826467b5e1370e4091b1e9/.agents/skills/test-audit) under the [MIT license](LICENSE).
