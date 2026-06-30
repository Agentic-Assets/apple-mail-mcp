# ID-First Search Retirement Todo

**Date:** 2026-06-29
**Source report:** `tasks/id-first-search-retirement-recommendations-2026-06-29.md`
**Goal:** Move Apple Mail MCP action tools away from keyword, substring, and broad search target selection. Keep keyword search as bounded discovery only.

## Core Recommendation

Use a two-step contract:

1. Discovery tools find candidate handles and return account, mailbox, `message_id`, `internet_message_id`, and `draft_id` where available.
2. Action tools operate on exact handles, usually `account + mailbox + message_id`, exact `draft_id`, or a surfaced exact attachment selector.

Do not treat search results as action authorization. Search or list first, review the candidate set, then mutate by exact ids.

## Progress Log

### 2026-06-29 Implementation Branch

Branch: `codex/id-first-search-retirement-implementation`

Completed in the first implementation pass:

- Added schema-compatible `TARGET_SELECTOR_DEPRECATED` errors for legacy action selectors while keeping v3.x parameter compatibility.
- Updated action-tool guidance in `docs/CLAUDE-conventions.md` and packaged skill examples for the highest-risk mutation paths.
- Added static guidance coverage for legacy action selectors in packaged docs and skills.
- Rebuilt and validated `apple-mail-plugin.zip`.

Completed in the CLI/dashboard follow-up pass:

- Added CLI `search --allow-body-scan` and wired it to `search_emails(allow_body_scan=True)`.
- Added CLI `move-dry-run --message-ids` and `trash-dry-run --message-ids`.
- Added CLI `--allow-filter-scan` flags for dry-run compatibility surfaces.
- Converted CLI perf dry-run probes from no-hit subject selectors to exact dummy `message_ids`.
- Added dashboard recent-email `message_id`, `internet_message_id`, and `mailbox` metadata.
- Migrated dashboard quick actions to `message_ids` and added static/template tests.
- Added `sender_exact` and `sender_domain` discovery filters to `search_emails` and the CLI.
- Rewrote `search-patterns.md` as ID-first discovery guidance and added a static guard against copyable whole-account mailbox examples.

Still open:

- Product decisions for v4 schema removal, `mailbox="All"` opt-in, and fate of fuzzy sender discovery.
- Exact attachment selector design.
- Forward draft id capture and verification.
- Thread header graph work.
- Metadata index feasibility and integration.
- Batch exact-ID APIs.

## Required Skills

Use these before or during implementation:

| Skill | When to use |
|---|---|
| `mail-scripting-dictionary` | Any AppleScript change, especially reply, forward, Drafts, headers, signatures, attachment ids, or Mail object properties. |
| `superpowers:dispatching-parallel-agents` | Any phase with two or more independent workstreams, such as CLI, skills, dashboard, and tests. |
| `testing-python` | Before adding or reshaping pytest coverage, fixtures, mocks, or focused test commands. |
| `reviewing-code` | Before finalizing each implementation branch or when doing adversarial review of a staged patch. |
| `mcp-builder` | Tool schema, MCP parameter contract, structured errors, and FastMCP registration changes. |
| `Plugin Structure` | Plugin wrapper, manifest, marketplace, package, and skill-bundle changes. |
| `python-performance-optimization` | Metadata index, batch APIs, perf budgets, cache behavior, and large-mailbox scan changes. |
| `finalize-apple-mail-mcp` | Ship-readiness pass after code, tests, docs, schemas, plugin manifests, and packaged skills change. |

If the plugin-dev expert tools are available, use `plugin-dev:plugin-validator` after schema or manifest changes and `plugin-dev:skill-reviewer` after packaged skill changes.

## Subagent Plan

Use subagents for independent lanes. Do not let them edit the same files at the same time unless one is read-only.

### Good Parallel Lanes

| Lane | Suggested agent role | Write scope | Output required |
|---|---|---|---|
| Docs and packaged skills | Worker | `docs/`, `plugin/skills/`, relevant static tests | Rewritten ID-first examples, static guidance test results, changed files. |
| CLI guardrails | Worker | `plugin/apple_mail_mcp/cli.py`, `tests/test_cli.py` | `--allow-body-scan`, `--message-ids`, filter escape behavior, focused test results. |
| Dashboard ID-first cleanup | Worker | `plugin/ui/templates/`, dashboard data producer tests | Recent-email IDs in payload, quick actions by `message_ids`, UI/static proof. |
| Schema-compatible deprecation | Worker | tool modules and migration tests | Structured `TARGET_SELECTOR_DEPRECATED` behavior with no AppleScript call. |
| Attachment selector design | Explorer first, then worker | `analytics.py`, `manage.py`, attachment tests | Exact attachment selector contract and ambiguity behavior. |
| Thread header graph | Explorer first, then worker | `search.py`, thread tests, header fixtures | JSON output with message ids and headers, header-first behavior. |
| Metadata index spike | Explorer first | `tasks/`, optional spike branch only | Privacy, cache schema, freshness, hydration, perf evidence, no runtime integration until approved. |
| Verification review | Explorer or reviewer | Read-only | Adversarial review against schema, docs, tests, no-unbounded-scan gates, and privacy. |

### When To Avoid Parallel Edits

- Do not split two agents across the same tool function.
- Do not run a schema removal worker in parallel with a docs worker that depends on the exact final schema.
- Do not let cache/index work modify `search_emails` until the feasibility spike is reviewed.
- Do not mix dashboard action changes and mutation-tool deprecation changes without a shared interface note.

## Phase 0: Decisions To Record

- [ ] Decide whether v3.x should reject legacy selectors immediately at runtime or warn for one compatibility release.
- [ ] Decide whether `allow_filter_scan=True` remains as an approved bulk-campaign escape hatch or moves to separate `bulk_*` tools.
- [ ] Decide whether `mailbox="All"` requires an explicit opt-in flag.
- [ ] Decide whether fuzzy `sender` remains permanently as discovery or becomes deprecated after `sender_exact` and `sender_domain`.
- [ ] Decide release boundary: v3.x compatibility deprecation first, v4 schema removal later.

## Phase 1: Guidance, CLI, UI, And Static Gates

Recommended subagents:

- Agent A: docs and skills.
- Agent B: CLI guardrails.
- Agent C: dashboard ID-first cleanup.
- Agent D: static tests and read-only review.

Todos:

- [x] Update `docs/CLAUDE-conventions.md` to say action tools must target exact ids.
- [x] Rewrite `plugin/skills/email-management/templates/search-patterns.md` as ID-first discovery guidance.
- [ ] Remove fuzzy mutation examples from packaged skills, starting with `move_email`, `update_email_status`, and `manage_trash`.
- [ ] Update reply, forward, Drafts, thread, attachment, and style-profile skills so subject fallback requires explicit degraded-path approval.
- [x] Add static docs tests for action calls using `subject_keyword`, `sender`, `draft_subject`, or unqualified `mailbox="All"`.
- [x] Add CLI `--allow-body-scan` for `search --body`.
- [x] Add CLI `--message-ids` to `move-dry-run` and `trash-dry-run`.
- [x] Require explicit `--allow-filter-scan` for any remaining filter dry-run path.
- [x] Convert live/perf probes to ID-based dry-run coverage or label old subject probes as compatibility checks.
- [x] Add `message_id`, `internet_message_id`, and mailbox to dashboard recent-email data.
- [x] Migrate dashboard quick actions to `message_ids`.

Verification:

- [x] Focused docs/static tests.
- [x] `tests/test_cli.py`.
- [x] Dashboard template/static tests or focused UI tests.
- [x] `git diff --check`.
- [x] Sensitive-data scan over changed docs, skills, UI, and tests.

## Phase 2a: Schema-Compatible Runtime Deprecation

Recommended subagents:

- Agent A: reply, forward, Drafts.
- Agent B: move, status, trash.
- Agent C: attachments and export.
- Agent D: schema and structured-error tests.

Todos:

- [x] Keep legacy selector params in v3.x schemas.
- [x] Add structured `TARGET_SELECTOR_DEPRECATED` errors before AppleScript runs.
- [x] `reply_to_email`: deprecate `subject_keyword` without `message_id`.
- [x] `forward_email`: deprecate `subject_keyword` without `message_id`.
- [ ] Add forward saved-draft id capture and verification work item.
- [x] `manage_drafts`: deprecate `draft_subject` for `send`, `open`, and `delete`.
- [ ] `move_email`: keep `message_ids` normal path, decide fate of `allow_filter_scan=True`. Runtime migration done; product decision remains open.
- [ ] `update_email_status`: same migration shape as `move_email`. Runtime migration done; product decision remains open.
- [ ] `manage_trash`: same migration shape as `move_email`, keep `empty_trash` separate. Runtime migration done; product decision remains open.
- [ ] `list_email_attachments`: deprecate `subject_keyword`, add JSON attachment metadata.
- [ ] `save_email_attachment`: deprecate `subject_keyword`, add exact attachment selector design.
- [x] `export_emails(scope="single_email")`: deprecate `subject_keyword`.
- [x] Tests must prove no AppleScript call occurs for deprecated target selectors.
- [x] Read-only and draft-safe precedence tests for `manage_drafts(action="send", draft_subject=...)`.

Verification:

- [x] Focused compose, manage, analytics, and schema tests.
- [x] `tests/test_read_only_registry.py` or equivalent schema inspection.
- [x] `tests/test_phase_2_scan_hardening.py` updates.
- [x] `ruff check`, `ruff format --check`, and type checks where touched.
- [x] `git diff --check`.

## Phase 2b: v4 Schema Removal

Do this only after Phase 2a ships and compatibility evidence is reviewed.

Todos:

- [ ] Remove legacy selector params from tool signatures.
- [ ] Update FastMCP schema snapshots.
- [ ] Update plugin manifests, marketplace text, docs, and packaged skills in the same branch.
- [ ] Remove or rewrite tests that assert old fallback behavior.
- [ ] Keep structured remediation for missing exact ids.

Verification:

- [ ] Full schema/registry tests.
- [ ] Manifest validation.
- [ ] Focused compose, manage, analytics, CLI, and skill tests.
- [ ] Release gate if packaged files or manifests change.

## Phase 3: Better Discovery

Recommended subagents:

- Agent A: `search_emails` exact filters.
- Agent B: `get_email_thread` JSON output and header-first path.
- Agent C: discovery warnings and skill examples.
- Agent D: adversarial review for false positives and overmatches.

Todos:

- [x] Add `sender_exact` and `sender_domain` to `search_emails`.
- [ ] Add `internet_message_id` lookup support where headers are available.
- [ ] Decide future of fuzzy `sender`.
- [ ] Add `get_email_thread` JSON output with message ids and headers.
- [ ] Make `get_email_thread(message_id=...)` header-first: exact anchor headers, cached headers where available, explicit mailbox set, subject fallback last.
- [ ] Add `include_preview=False` default or option for `get_email_thread`.
- [ ] Add examples for explicit `mailboxes=[...]`.
- [ ] Add warnings for sender-only, body, content-preview, and All-mailbox searches.
- [ ] Test renamed threads and common-subject overmatch.

Verification:

- [x] Search and thread focused tests.
- [ ] Schema tests for new params.
- [ ] No-unbounded-scan tests.
- [ ] Large-mailbox fixture or mocked perf checks where available.

## Phase 4a: Index Feasibility Spike

Recommended subagents:

- Agent A: privacy and cache storage design.
- Agent B: source-data coverage and hydration tiers.
- Agent C: performance measurement.
- Agent D: direct Envelope Index feasibility as a read-only research lane.

Todos:

- [ ] Define cache storage outside repo and package artifacts.
- [ ] Make cache opt-in, TTL-scoped, gitignored, refreshable, and deletable.
- [ ] Define provenance, completeness, freshness, resume/watermark, and mailbox-count coverage.
- [ ] Split rows into `bulk_metadata` and `exact_hydrated`.
- [ ] Prove partial rows cannot answer recipient, header, thread, attachment, or body queries unless hydrated.
- [ ] Measure header and attachment-count costs before extending exporters.
- [ ] Keep direct Envelope Index as a parallel research spike only until permission and schema-drift risks are known.

Verification:

- [ ] Design note or spike report.
- [ ] Perf baseline and p50/p95 comparison where runnable.
- [ ] Privacy and packaging review.
- [ ] No live private content in reports.

## Phase 4b: Metadata Index Integration

Do not start until Phase 4a is reviewed.

Todos:

- [ ] Define safe bulk metadata schema: account, mailbox, numeric id, Internet Message-ID where available, date, sender, subject, and flags.
- [ ] Let `full_inbox_export` populate or refresh the index.
- [ ] Let `search_emails` use the index only when provenance and freshness rules pass.
- [ ] Add write invalidation after move, trash, status update, draft lifecycle, and send.
- [ ] Add `get_email_by_ids`.
- [ ] Reassess direct Envelope Index after safer cache path proves useful.

Verification:

- [ ] Cache hit, cache miss, stale fallback, and invalidation tests.
- [ ] Cache miss must fall back only through bounded AppleScript paths or explicit `full_inbox_export`.
- [ ] Perf budget tests.
- [ ] Packaging and sensitive-data scans.

## Phase 5: Batch Exact-ID APIs

Todos:

- [ ] Design batch APIs around `MAX_WHOSE_IDS`.
- [ ] Chunk internally.
- [ ] Preserve input order.
- [ ] Deduplicate safely.
- [ ] Return per-id errors.
- [ ] Add tests for 50, 51, and 120 ids.

Candidate APIs:

- [ ] `get_email_by_ids(message_ids=[...])`
- [ ] `verify_drafts(draft_ids=[...])`
- [ ] `list_email_attachments(message_ids=[...])`
- [ ] `export_emails(message_ids=[...])`

## Phase 6: Final Verification And Release Readiness

Use `reviewing-code`, `testing-python`, `Plugin Structure`, and `finalize-apple-mail-mcp`.

Verification:

- [ ] Focused tests for every touched tool.
- [ ] Adjacent compose, manage, analytics, search, smart-inbox, CLI, and dashboard tests.
- [ ] `test_no_unbounded_whose.py`.
- [ ] `test_bounded_scan_contract.py`.
- [ ] Read-only registry/schema tests.
- [ ] Static docs and skill tests.
- [ ] Manifest validation if plugin/package metadata changes.
- [ ] `ruff check`.
- [ ] `ruff format --check`.
- [ ] `mypy --strict` for touched Python surfaces if consistent with repo gate.
- [ ] `git diff --check`.
- [ ] Sensitive-data scan over changed source, tests, docs, skills, UI, and package artifacts.

## Worker Prompt Template

Use this shape when dispatching subagents:

```text
Repo: /Users/caymanseagraves/.codex/worktrees/79e8/apple-mail-mcp
Task: <one focused lane>
Read first: AGENTS.md, relevant CLAUDE.md, tasks/id-first-search-retirement-todo-2026-06-29.md, and relevant skill docs.
Scope: <exact files or modules>
Do not: send email, create live Mail drafts, print private content, push, merge, or edit outside scope.
Goal: implement the lane with focused mocked/local tests.
Return: changed files, behavior summary, tests run, residual risks, and next action.
```

## Recommended First Branch

Start with Phase 1 because it reduces agent misuse before runtime behavior changes:

1. Docs and skill cleanup.
2. CLI guardrails.
3. Dashboard ID-first payload and actions.
4. Static tests that keep the guidance from regressing.

After that, start Phase 2a runtime deprecation with schema-compatible structured errors.
