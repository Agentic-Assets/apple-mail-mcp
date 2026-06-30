# ID-First Search Retirement Recommendations

**Date:** 2026-06-29
**Repo:** Agentic-Assets/apple-mail-mcp
**Branch reviewed:** `fix/reply-draft-verification-consolidated`
**Purpose:** Reduce keyword, substring, and broad word-search behavior in the Apple Mail MCP tool surface while preserving practical discovery workflows.

## Executive Recommendation

Move the product to a two-tier contract:

1. **Discovery tools find candidate handles.** They may use bounded filters, headers, exact sender/domain filters, and metadata indexes. They return `message_id`, `internet_message_id`, account, mailbox, and, for drafts, `draft_id`.
2. **Action tools require handles.** Reply, forward, move, status update, trash/delete, draft lifecycle, attachment listing, attachment saving, and single-message export should operate by exact `account + mailbox + message_id`, exact `draft_id`, or an implemented exact selector. They should not select targets by subject keyword, sender substring, or body substring.

The repo already has the safety scaffolding: `message_id` paths, `draft_id` paths, `allow_filter_scan`, `allow_body_scan`, bounded scan helpers, `UNBOUNDED_SCAN_REQUIRED`, `FILTER_SCAN_DISABLED`, and `BODY_SCAN_DISABLED`. The next step is policy hardening: stop training agents to use keyword target selection, return structured deprecation errors for legacy selectors while schemas remain compatible, then remove the legacy schema fields in a breaking v4 pass.

## Mail Dictionary Facts That Support This Direction

The local Mail scripting dictionary supports stable handles for the workflows in question:

- `message.id` is a read-only integer unique identifier.
- `message.message id` is the Internet Message-ID header string.
- `message` contains `header` elements, so `In-Reply-To` and `References` can be parsed per bounded candidate or via a cache for thread linkage. Mail does not expose a cheap thread graph index.
- `reply <message>` and `forward <message>` return `outgoing message`.
- `outgoing message.id` is a read-only integer. Reply drafts are currently exact-id verified when Mail exposes the saved Drafts id. Forward drafts are dictionary-feasible, but current `forward_email` needs native-forward or post-save id capture and verification work before making the same claim.
- `outgoing message.message signature` accepts `signature` or `missing value`.
- `message` contains `mail attachment`; attachments expose `name`, `file size`, `downloaded`, and `id`.
- Saved/source `message.content` is read-only. `outgoing message.content` is writable.

This means the exact-id model is not a workaround. It aligns with the app dictionary.

Implementation caveats:

- `draft_id` is a repo term for `message.id` of a saved message in Drafts, not a separate Mail dictionary class.
- Current exact numeric id lookups are mailbox-scoped. Discovery should carry account and mailbox unless a cache or global lookup is added.
- Internet Message-ID and thread headers are dictionary-readable, but action tools do not yet accept them as selectors.
- Attachment ids exist in Mail's dictionary but are not yet surfaced by list/save tools.
- Saved-draft signature checks are content heuristics, not object-property checks.

## Policy Model

### Selection Classes

| Class | Allowed target selectors | Keyword or substring role |
|---|---|---|
| Exact message action | `account + mailbox + message_id` until a cache/global lookup exists | None |
| Exact draft action | `draft_id`, optional account | None |
| Exact attachment action | `message_id` plus surfaced `attachment_id` or exact attachment name | Optional display check only |
| Thread action | `message_id` anchor plus headers where available | Subject fallback only during compatibility window |
| Discovery | account, mailbox, date window, exact sender/domain, headers, bounded metadata filters | Bounded fallback only |
| Audited export/index | explicit account/mailbox, `max_emails`, fields, batch size | None, index can later be queried |

### Product Rules

1. **No action tool should target by keyword.**
2. **No destructive tool should target by sender substring.**
3. **No reply or forward tool should target by subject fallback once compatibility is retired.**
4. **No draft lifecycle mutation should target by `draft_subject`; only discovery can search Drafts.**
5. **No body-text search should run without explicit opt-in and a tight window.**
6. **`mailbox="All"` should be a last resort. Prefer explicit `mailboxes=[...]`.**
7. **Search results should be treated as candidate lists, not action authorization.**
8. **Every action response should echo the exact ids acted on or skipped.**
9. **Exact-id action calls should include mailbox scope until a tested locator/cache can resolve ids across mailboxes.**
10. **Compatibility parameters stay in v3.x schemas if needed, but should return structured deprecation errors before v4 removal.**

## Tool-by-Tool Migration Map

### Remove Or Deprecate Keyword Targeting

| Tool | Current keyword or substring path | Recommended target contract | Recommendation |
|---|---|---|---|
| `reply_to_email` | `subject_keyword` fallback scans bounded inbox by subject | Require `message_id` | Return structured deprecation in v3.x; remove schema field in v4 |
| `forward_email` | `subject_keyword` fallback | Require `message_id` | Return structured deprecation in v3.x; remove schema field in v4 |
| `move_email` | `subject_keyword`, `subject_keywords`, `sender`, `allow_filter_scan` | Require `message_ids` plus source mailbox | Keep schema-compatible deprecation first; quarantine approved bulk scans into a separate bulk surface or v4 removal |
| `update_email_status` | `subject_keyword`, `subject_keywords`, `sender`, `apply_to_all` | Require `message_ids` plus mailbox for targeted updates | Keep schema-compatible deprecation first; keep explicit bulk policy separate if needed |
| `manage_trash` | `subject_keyword`, `subject_keywords`, `sender`, `apply_to_all` | Require `message_ids` plus mailbox for move/delete | Highest priority because target mistakes are data-loss risks; keep `empty_trash` separate |
| `manage_drafts` lifecycle | `draft_subject` for send/open/delete | Require `draft_id` | Return structured deprecation in v3.x; remove schema field in v4 |
| `list_email_attachments` | `subject_keyword` fallback | Require `message_ids`; optional `internet_message_id` later | Deprecate subject fallback; add JSON output with attachment ids/names first |
| `save_email_attachment` | `subject_keyword` fallback and attachment filename substring | Require `message_ids` plus exact attachment selector | Deprecate subject fallback; add exact attachment selector before claiming full safety |
| `export_emails(scope="single_email")` | `subject_keyword` fallback | Require `message_id`; add `message_ids` for batch | Deprecate subject fallback |

### Keep But Bound And Reframe As Discovery

| Tool | Keep? | Changes |
|---|---|---|
| `search_emails` | Yes, discovery only | Add `sender_exact`, `sender_domain`, `internet_message_id`; warn on sender-only, `mailbox="All"`, `include_content`, and body search. |
| `get_email_thread` | Yes, but change strategy | Add JSON output with message ids and headers. Then make `message_id` path header-first using exact anchor headers, cached headers where available, and explicit mailbox sets. Subject matching becomes compatibility fallback only. Add `include_preview=False` option. |
| `manage_drafts(action="list")` | Yes, discovery only | Keep bounded `subject_contains`, but label it as locator-only. Later actions must use returned `draft_id`. |
| `manage_drafts(action="find")` | Yes | Keep bounded `in_reply_to`/`References` lookup. Prefer exact header matching and normalize bracket variants. |
| `get_needs_response` | Yes | Keep as heuristic triage. Replace sender/newsletter substring rules with header signals and sender-domain blocklists where possible. Body scan remains opt-in. |
| `get_awaiting_reply` | Yes | Already header-based. Add per-account header cache. |
| `get_top_senders` | Yes | Move sender/domain parsing to Python or an index. Surface sample coverage clearly. |
| `get_statistics` | Yes | Split live sample stats from indexed stats. Add exact sender/domain modes. |
| `full_inbox_export` | Yes, explicit expensive tool | Make it a source for a local metadata index. Add incremental/resume options later. |
| `list_inbox_emails` | Yes | Keep lean metadata default. Fetch body content by `get_email_by_id` only. Add warnings for high `include_content` use. |
| `inbox_dashboard` | Yes | Load previews lazily by exact id instead of including body previews in default payload. |

### Already Good

| Tool | Status |
|---|---|
| `verify_draft` | Already exact `draft_id`; expectation substring checks do not select a draft. |
| `get_email_by_id` | Exact numeric id path. Can be improved with cache or batch lookup, but the public contract is right. |
| `create_rich_email_draft` | Standalone creation, no search target. |
| `compose_email` | Standalone creation, no search target. |
| `create_mailbox` | No message search. |
| `synchronize_account` | No search target. |
| `list_accounts`, `list_account_addresses`, `list_mailboxes` | Discovery/catalog tools, not target selection. |

## Better Alternatives To Keyword Search

### 1. Exact IDs From Discovery

Default workflow:

```python
candidates = list_inbox_emails(
    account="Work",
    max_emails=20,
    read_status="unread",
    output_format="json",
)

ids = [email["message_id"] for email in candidates["emails"]]
# Present count, subjects, senders, mailboxes, and ids for review first.
update_email_status(action="flag", mailbox="INBOX", message_ids=[ids[0]])
```

For action tools, this is the cleanest option because the user or agent reviews candidate metadata before mutation. Do not treat search results as authorization by themselves.

### 2. Header Graph For Threads

Use:

- numeric `message_id` to fetch the anchor.
- `message id` as the Internet Message-ID.
- `In-Reply-To` and `References` headers to find related messages or draft replies.

This should replace subject-thread matching wherever the anchor message is known, after the tool can return message ids and headers in its thread output. Subject matching can miss renamed threads and can overmatch common subjects.

### 3. Exact Sender And Domain Filters

Add:

- `sender_exact="person@example.com"`
- `sender_domain="example.com"`
- `from_address_exact`
- possibly `recipient_exact` for narrower discovery.

Do not overload `sender="emily"` as both a fuzzy search and a targeting primitive. Keep fuzzy sender only in `search_emails`, and report it as a fuzzy match.

### 4. Explicit Mailbox Sets

Prefer:

```python
search_emails(
    account="Work",
    mailboxes=["INBOX", "Sent", "Archive"],
    recent_days=7,
    limit=20,
)
```

over:

```python
search_emails(account="Work", mailbox="All")
```

`mailbox="All"` should remain available but should be described as a capped fallback, not the standard expansion path.

### 5. Local Metadata Index

Two possible designs:

1. **Repo-owned metadata cache:** populate a local SQLite or JSONL index from `full_inbox_export`, `list_inbox_emails`, and `search_emails` results. This avoids direct reads of Mail internals and is lower permission risk than direct Mail database access, but it is not automatically faster because the initial feeder is still an expensive Mail walk.
2. **Envelope Index backend:** read Mail's Envelope Index directly for fast metadata queries. This is the bigger v4 path already sketched historically. It needs Full Disk Access handling, schema drift checks, read-only opening, and fallback to AppleScript.

Recommended path: start with an index feasibility spike before using any cache in `search_emails`. The cache must live outside the repo and package artifacts, be opt-in, have a delete/refresh command, include provenance and freshness metadata, and invalidate after writes.

Use coverage tiers:

- `bulk_metadata`: subject, sender, dates, read/flagged status, message id, account, and mailbox from safe bulk exporters.
- `exact_hydrated`: recipients, thread headers, attachment metadata, body snippets, and other fields fetched by exact id or a bounded explicit candidate set.

Partial cache rows must not satisfy recipient, header, attachment, or thread queries unless they have been explicitly hydrated.

### 6. Batch Exact-ID Tools

Add or extend:

- `get_email_by_ids(message_ids=[...])` (implemented on `codex/id-first-search-retirement-implementation`)
- `verify_drafts(draft_ids=[...])` (implemented on `codex/id-first-search-retirement-implementation`)
- `list_email_attachments(message_ids=[...])` (implemented with internal chunking on `codex/id-first-search-retirement-implementation`)
- `export_emails(message_ids=[...])`

Batching lets agents avoid re-running discovery loops and makes exact-ID workflows efficient.

Batch APIs must respect the existing `MAX_WHOSE_IDS` cap. They should chunk internally, preserve input order, deduplicate safely, return per-id errors, and test 50, 51, and 120-id inputs.

## CLI And Skill Guidance Gaps

The MCP code is safer than some guidance surfaces. These should be cleaned up early because they train agents.

### CLI

| Surface | Issue | Recommendation |
|---|---|---|
| `apple-mail search --body` | It passes `body_text` but has no visible `--allow-body-scan` opt-in | Add `--allow-body-scan` and pass it through. |
| `move-dry-run` | Exposes subject/sender filters but not `message_ids` | Add `--message-ids`; make ID path the default documented shape. |
| `trash-dry-run` | Same filter issue | Add `--message-ids`; require explicit `--allow-filter-scan` if filter mode remains. |
| CLI help | `--subject`, `--sender`, `--body` look ordinary | Mark as discovery-only or slow/fuzzy where applicable. |
| `perf-test`, `quick-check`, `smoke-test` | Some probes still use no-hit subject searches or subject-filtered dry runs | Move probes to ID-based dry-run coverage or label them compatibility probes. |

### Skills And Examples

High-priority cleanup targets:

- `plugin/skills/email-management/templates/common-workflows.md`
- `plugin/skills/email-management/templates/search-patterns.md`
- `plugin/skills/email-management/examples/email-triage.md`
- `plugin/skills/email-management/examples/folder-organization.md`
- `plugin/skills/email-management/examples/inbox-zero-workflow.md`
- `plugin/skills/email-attachments/SKILL.md`
- `plugin/skills/email-archive-cleanup/SKILL.md`
- `plugin/skills/email-drafting/SKILL.md`
- `plugin/skills/email-style-profile/SKILL.md`
- `plugin/skills/email-management/references/thread-management.md`

Replacement house pattern:

```python
results = search_emails(
    account="Work",
    mailbox="INBOX",
    subject_keyword="Project Alpha",
    recent_days=7,
    limit=10,
    output_format="json",
)
ids = [email["message_id"] for email in results["emails"]]
# Review count, subjects, senders, mailboxes, dates, and ids before mutation.
move_email(dry_run=True, message_ids=ids, to_mailbox="Projects/Alpha", max_moves=len(ids))
move_email(dry_run=False, message_ids=ids, to_mailbox="Projects/Alpha", max_moves=len(ids))
```

In prose, emphasize: keyword search is candidate discovery, not target selection, and the human or orchestrator must review the candidate set before mutation.

### Dashboard And UI

The dashboard is a required dependency before runtime removal. Current quick actions should not keep using subject-only selectors. Add `message_id`, `internet_message_id`, and mailbox to the recent-email payload, migrate dashboard actions to `message_ids`, and add UI tests or template/static tests before deprecating runtime keyword mutation paths.

## Phased Todo List

### Phase 0: Product Contract Decision

- [ ] Decide whether action tools may keep keyword fallback for one compatibility release or should reject immediately.
- [ ] Decide release framing: minor hardening release versus breaking v4 cleanup.
- [ ] Decide whether `apply_to_all` remains in action tools or moves to separate bulk-campaign tools.
- [ ] Decide whether `mailbox="All"` should require an explicit opt-in flag in `search_emails`.

### Phase 1: Guidance, CLI, UI, And Static Gates

- [ ] Update `docs/CLAUDE-conventions.md` from "prefer message_ids" to "action tools must target by ids."
- [ ] Update packaged skills to remove routine filter mutation examples, starting with unsafe `move_email`, `update_email_status`, and `manage_trash` examples.
- [ ] Replace `search-patterns.md` with an ID-first discovery structure instead of patching individual broad examples.
- [ ] Update reply, forward, Drafts, thread, attachment, and style-profile skills so subject fallback requires explicit degraded-path approval.
- [ ] Add static docs tests that flag action calls like `move_email(subject_keyword=...)`, `update_email_status(sender=...)`, `manage_trash(sender=...)`, `reply_to_email(subject_keyword=...)`, `forward_email(subject_keyword=...)`, `list_email_attachments(subject_keyword=...)`, `save_email_attachment(subject_keyword=...)`, and unqualified `mailbox="All"` unless explicitly labeled as fallback.
- [ ] Add CLI `--allow-body-scan`, `--message-ids` for dry-run commands, and explicit `--allow-filter-scan` if filter dry-runs remain.
- [ ] Update CLI help to say `search` is discovery and actions are ID-first.
- [ ] Convert live/perf probes to ID-based dry-run coverage or label old subject probes as compatibility checks.
- [ ] Add dashboard payload ids and migrate dashboard quick actions to ID-first calls.

### Phase 2a: Schema-Compatible Runtime Deprecation

- [ ] Keep legacy selector params in v3.x schemas, but return structured `TARGET_SELECTOR_DEPRECATED` errors before AppleScript runs.
- [ ] `reply_to_email`: return deprecation error for `subject_keyword` without `message_id`.
- [ ] `forward_email`: return deprecation error for `subject_keyword` without `message_id`; separately capture and verify forward draft ids.
- [ ] `manage_drafts`: return deprecation error for `draft_subject` on `send`, `open`, and `delete`.
- [ ] `move_email`: keep `message_ids` as the normal path; decide whether `allow_filter_scan=True` becomes a deprecation error or moves to a separate bulk tool.
- [ ] `update_email_status`: same migration shape as `move_email`.
- [ ] `manage_trash`: same migration shape as `move_email`; keep `empty_trash` as a separate confirmation-based path.
- [ ] `list_email_attachments`: return deprecation error for `subject_keyword`; add JSON output carrying attachment metadata.
- [ ] `save_email_attachment`: return deprecation error for `subject_keyword`; add exact `attachment_id` or exact-name semantics and ambiguous-duplicate errors.
- [ ] `export_emails(scope="single_email")`: return deprecation error for `subject_keyword`.
- [ ] Add migration tests proving deprecation errors include `code`, `message`, `remediation.preferred`, and do not call AppleScript.
- [ ] Add read-only and draft-safe precedence tests for `manage_drafts(action="send", draft_subject=...)`.

### Phase 2b: Breaking Schema Removal

- [ ] Remove legacy selector params only in v4, with manifest and tool-description updates.
- [ ] Add FastMCP schema snapshot tests proving legacy params are present during v3.x deprecation and absent after v4 removal.
- [ ] Update marketplace/plugin docs and bundled skills in the same branch.

### Phase 3: Better Discovery

- [ ] Add `sender_exact` and `sender_domain` to `search_emails`.
- [ ] Add `internet_message_id` lookup support where Mail headers are available.
- [ ] Decide whether fuzzy `sender` stays forever as discovery or becomes deprecated after exact/domain fields exist.
- [ ] Add `get_email_thread` JSON output with message ids and headers.
- [ ] Add `get_email_thread` header-first implementation for `message_id` anchors: exact anchor headers first, cached headers if available, explicit mailbox set only, subject fallback last.
- [ ] Add `include_preview=False` default or option for `get_email_thread`.
- [ ] Add `mailboxes=[...]` examples everywhere `mailbox="All"` is currently suggested.
- [ ] Add warnings in JSON/text responses for sender-only, body, content-preview, and All-mailbox searches.
- [ ] Add tests for renamed threads and common-subject overmatch.

### Phase 4a: Index Feasibility Spike

- [ ] Define cache privacy rules: outside repo/package artifacts, opt-in only, TTL-scoped, gitignored, refreshable, and deletable.
- [ ] Define index provenance, completeness, freshness TTL, resume/watermark behavior, and mailbox-count coverage.
- [ ] Extend safe metadata-only exporters only after measuring header and attachment-count costs.
- [ ] Split cached rows into `coverage_tier=bulk_metadata` and `coverage_tier=exact_hydrated`.
- [ ] Add tests proving partial cache rows cannot answer recipient/header/thread/attachment queries unless hydrated.
- [ ] Keep direct Envelope Index as a parallel spike for metadata-only queries after permission and schema-drift checks.

### Phase 4b: Metadata Index Integration

- [ ] Define a local cache schema for safe bulk metadata: account, mailbox, numeric id, Internet Message-ID when available, date, sender, subject, and flags.
- [ ] Let `full_inbox_export` populate or refresh the index.
- [ ] Let `search_emails` use the index for repeated broad discovery only when provenance and freshness rules pass.
- [ ] Add invalidation after write tools: move, trash, status update, draft lifecycle, send.
- [x] Add `get_email_by_ids` for batch exact fetches.
- [ ] Reassess direct Envelope Index backend after this safer cache path proves out.

### Phase 5: Tests And Gates

- [ ] Add schema tests proving action tools no longer expose keyword target params when the removal phase lands.
- [ ] Add backward-compat tests for structured deprecation errors during the transition.
- [ ] Add CLI tests for `--allow-body-scan`, `--message-ids`, and filter-scan escape hatch behavior if retained.
- [ ] Add static scan tests for skill examples and CLI help.
- [ ] Keep `test_no_unbounded_whose.py`, `test_bounded_scan_contract.py`, and exact-id regression tests as merge gates.
- [ ] Treat no-unbounded-scan gates as design constraints for cache misses: fallback only through bounded AppleScript paths or explicit `full_inbox_export`.
- [ ] Add metadata-index tests for cache hit, cache miss, stale fallback, and post-write invalidation.
- [ ] Add batch-id tests for 50, 51, and 120 ids.
- [ ] Add performance budget tests and before/after p50/p95 fixtures for `search_emails`, `get_email_thread`, `get_email_by_id`, `full_inbox_export`, and new batch APIs.

## Suggested Issue Breakdown

1. **Docs and skill cleanup:** remove action-tool keyword targeting examples.
2. **CLI ID-first cleanup:** `--message-ids` for dry-run commands, `--allow-body-scan` for search.
3. **Dashboard ID-first cleanup:** add ids to dashboard feed and migrate quick actions.
4. **Schema-compatible deprecation:** structured errors for legacy selectors while v3.x schemas stay stable.
5. **Reply and forward exact-id contract:** deprecate `subject_keyword`; add forward saved-draft id verification.
6. **Draft lifecycle exact-id contract:** deprecate `draft_subject` from send/open/delete.
7. **Mutation exact-id contract:** deprecate or quarantine `allow_filter_scan` paths from move/status/trash into explicit bulk tools.
8. **Attachment exact-id contract:** surface attachment ids and require exact attachment selectors.
9. **Thread header graph:** add thread JSON ids/headers, then make `get_email_thread(message_id=...)` header-first.
10. **Search precision:** add exact sender/domain and Internet Message-ID filters.
11. **Index feasibility spike:** opt-in cache privacy, provenance, coverage tiers, hydration, and perf measurements.
12. **v4 schema removal:** remove legacy selector params after compatibility deprecation is proven.

## Recommended Immediate Next Action

Start with docs, CLI, dashboard, and skills before removing runtime parameters:

1. Make examples ID-first so agents stop selecting by keywords.
2. Add CLI guardrails and dashboard IDs so live surfaces can use the replacement path.
3. Add schema-compatible deprecation errors and tests for legacy selectors.
4. Then remove keyword targeting from the highest-risk actions in v4: `manage_trash`, `move_email`, and `update_email_status`.
5. Then remove subject fallback from reply/forward and draft lifecycle actions.

This sequence avoids breaking workflows before the documented replacement path is visible, while still moving firmly toward exact-id-only actions.

## Evidence Gathered

- Local Mail dictionary inspected at `/System/Applications/Mail.app/Contents/Resources/Mail.sdef`.
- Existing repo references reviewed:
  - `tasks/id-first-refactor-spec.md`
  - `tasks/whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`
  - `docs/CLAUDE-conventions.md`
  - `plugin/apple_mail_mcp/tools/search.py`
  - `plugin/apple_mail_mcp/tools/compose.py`
  - `plugin/apple_mail_mcp/tools/manage.py`
  - `plugin/apple_mail_mcp/tools/analytics.py`
  - `plugin/apple_mail_mcp/tools/smart_inbox.py`
  - `plugin/apple_mail_mcp/tools/inbox.py`
  - `plugin/apple_mail_mcp/cli.py`
  - `plugin/ui/templates/dashboard.html`
  - packaged skills and examples under `plugin/skills/`
- Initial read-only parallel review agents checked action tools, discovery/analytics tools, and docs/CLI/skills. A second adversarial review set checked Mail dictionary feasibility, MCP schema compatibility, performance/indexing, docs/CLI/UI guidance, and user-experience regressions. Their outputs agree on the main split: action tools should become exact-id-only, but the safe path is schema-compatible deprecation first, v4 schema removal later, with dashboard/CLI/docs migrated before runtime removals.
