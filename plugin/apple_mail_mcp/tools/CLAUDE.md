# tools/ — MCP tool registrations
All `@mcp.tool` handlers live here; `apple_mail_mcp/__init__.py` imports these seven tool surfaces (the `inbox/`, `search/`, `compose/`, `manage/`, `analytics/`, `smart_inbox/`, and `calendar/` packages) for side-effect registration. **41 tools**; verify: `rg '^@mcp\.tool' plugin/apple_mail_mcp/tools | wc -l` (recursive: every surface is a package).

## Module map

| Module | # | Purpose / tools |
|--------|---|-----------------|
| `inbox/list_emails.py` | 1 | Listing: `list_inbox_emails` (async per-account dispatch; `parsing.py`/`replied.py`/`list_scripts.py` leaves) |
| `inbox/unread_counts.py` | 1 | Unread totals: `get_mailbox_unread_counts` |
| `inbox/accounts.py` | 2 | Account enumeration: `list_accounts`, `list_account_addresses` |
| `inbox/mailboxes.py` | 1 | Folder listing: `list_mailboxes` |
| `inbox/overview.py` | 1 | Overview: `get_inbox_overview` |
| `search/emails.py` | 1 | Find: `search_emails` (windowing, replied-detection) |
| `search/by_id.py` | 2 | Exact-id fetch: `get_email_by_id`, `get_email_by_ids` |
| `search/thread.py` | 1 | Thread reconstruction: `get_email_thread` |
| `compose/send.py` | 1 | Standalone send/draft: `compose_email` |
| `compose/reply.py` | 1 | Reply (native window default): `reply_to_email` |
| `compose/forward.py` | 1 | Forward draft: `forward_email` |
| `compose/manage.py` | 1 | Draft listing/management: `manage_drafts` |
| `compose/rich_draft.py` | 1 | Rich standalone draft: `create_rich_email_draft` |
| `compose/verify_tools.py` | 2 | Exact-id draft verification: `verify_draft`, `verify_drafts` |
| `manage/move.py` | 1 | Move: `move_email` (id-direct + filter-scan; `_move_email_by_message_ids` helper) |
| `manage/attachments.py` | 1 | Attachment save: `save_email_attachment` (size/disk probes) |
| `manage/status.py` | 1 | Read/flag status: `update_email_status` |
| `manage/trash.py` | 1 | Trash ops: `manage_trash` (move_to_trash/delete_permanent/empty_trash) |
| `manage/mailbox.py` | 1 | Folder creation: `create_mailbox` (nested paths) |
| `manage/sync.py` | 1 | IMAP sync: `synchronize_account` (`helpers.py` is a shared leaf) |
| `analytics/attachments.py` | 1 | Attachment listing: `list_email_attachments` (`statistics_parsing.py` is a pure leaf) |
| `analytics/statistics.py` | 1 | Stats: `get_statistics` (account_overview/sender_stats/mailbox_breakdown) |
| `analytics/export.py` | 1 | Export: `export_emails` by exact ids, bounded filters, correspondent/thread scopes, or mailbox pages |
| `analytics/full_export.py` | 1 | Disabled refusal shim: `full_inbox_export` (returns `UNBOUNDED_EXPORT_DISABLED`, no AppleScript runs) |
| `analytics/dashboard.py` | 1 | Dashboard: `inbox_dashboard` + recent-email helpers |
| `smart_inbox/awaiting_reply.py` | 1 | Follow-up tracking: `get_awaiting_reply` (sent-vs-inbox Message-ID cross-reference; `helpers.py` shares `_normalize_message_id`) |
| `smart_inbox/needs_response.py` | 1 | Actionable detection: `get_needs_response` (newsletter/automated filtering, replied-detection join) |
| `smart_inbox/top_senders.py` | 1 | Sender analytics: `get_top_senders` (bounded newest-first Counter aggregation, domain grouping) |
| `calendar/calendars_list.py` | 1 | Calendar enumeration: `list_calendars` (writability, defaults, engine diagnostics) |
| `calendar/events_list.py` | 1 | Bounded event listing/search: `list_events` (windows, query, recurring expansion, paging; `helpers.py` shares the fan-out collector) |
| `calendar/events_get.py` | 1 | Exact-id detail fetch: `get_events_by_id` (notes, alarms, attendees; window-bounded) |
| `calendar/availability.py` | 1 | Free-busy folding: `check_availability` (busy blocks + free slots, 62-day cap) |
| `calendar/events_create.py` | 1 | Event creation: `create_event` (timezone-correct, alarms, allowlisted RRULE, conflict detection) |
| `calendar/events_batch.py` | 1 | Batch creation: `batch_create_events` (25-item cap, all-or-nothing validation, per-item writes) |
| `calendar/events_update.py` | 1 | ID-first PATCH: `update_event` (span rules, attendee-set diffing, dry-run) |
| `calendar/events_delete.py` | 1 | Exact-id bulk delete: `delete_events` (dry-run default, resolve-first, chunked) |
| `calendar/calendars_manage.py` | 1 | Calendar CRUD: `manage_calendars` (create/rename/delete, triple-gated cascade delete) |
| `calendar/rsvp.py` | 1 | Refusal shim: `respond_to_invitation` (returns `CALENDAR_RSVP_UNSUPPORTED`, no engine call) |

## Calendar surface notes

- All Calendar.app I/O flows through `calendar_core/` (engine seam): reads via `calendar.get_engine()` (AppleScript, or EventKit when installed and already granted), writes via `calendar.get_write_engine()` (AppleScript only in 3.10.0). Never emit `every event of` outside `calendar_core/scripts_read.py`/`scripts_write.py`; the lint in `tests/calendar_surface/test_calendar_scripts.py` enforces date-bounded predicates.
- Mode gating is stricter than mail (new plumbing, not the `_send_blocked` port): `--read-only` removes `CALENDAR_WRITE_TOOLS` + `CALENDAR_DESTRUCTIVE_TOOLS`; `--draft-safe` blocks deletes (`CALENDAR_DELETE_BLOCKED`, env unlock `CALENDAR_ALLOW_DESTRUCTIVE=1`) and attendee sends (`INVITE_SEND_BLOCKED`). Internal guards live in `calendar/helpers.py` because the CLI bypasses registry removal.
- Caps live in `constants.CALENDAR_BOUNDS`; every event read requires a `bounded_calendar_window` token. Unscoped reads fan out (capped at 20 calendars + a 240s call budget), which deliberately differs from mail's account-scoping default.
- Calendar ids are UUID-like strings (`calendar_core.validation.normalize_event_ids`), never the numeric Mail id helpers.

## Add a tool

1. Pick module by domain; add `@mcp.tool(annotations=…)` using presets from `../server.py` (matrix: [`tasks/reference/phase-3-annotation-matrix.md`](../../../tasks/reference/phase-3-annotation-matrix.md)).
2. `@inject_preferences` on user-facing tools; user strings → `core.escape_applescript()`; multi-account fan-out → `async` + `asyncio.to_thread`, dispatched sequentially (one account at a time), not via `asyncio.gather`, since Mail AppleScript is serialized behind a single-flight lock in `core/applescript.py`.
3. New file → import in `__init__.py`; update the root release version table files when releasing, plus `apple-mail-mcpb/manifest.json` `tools[]` and advertised tool count.

## Performance (summary)

- Default `recent_days=2.0` (48h). Tools refuse unbounded scans (`recent_days=0` / `max_emails=0`) with `code: UNBOUNDED_SCAN_REQUIRED`. `full_inbox_export` is disabled (`code: UNBOUNDED_EXPORT_DISABLED`, no AppleScript runs) and is not a working fallback; narrow the window (`recent_days` / `date_from`) or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`) instead. Prefer bounded newest-message slices (`messages 1 thru N`) over broad `whose` clauses on large remote mailboxes.
- Pass `timeout` through to `run_applescript`; catch `AppleScriptTimeout` → structured error with account name.
- **ID-first mutations (v3.7.0+):** `move_email`, `update_email_status`, and `manage_trash` prefer `message_ids` from a prior list/search. `subject_keyword` / `sender` on action tools return `TARGET_SELECTOR_DEPRECATED` before any scan (even with `allow_filter_scan=True`). Date/bulk filter paths require `allow_filter_scan=True` or return `FILTER_SCAN_DISABLED`. `search_emails` requires `allow_body_scan=True` when `body_text` is set or returns `BODY_SCAN_DISABLED`.
- **Scan caps (2026-07, AGENTIC-988 hardening):** `SEARCH_HARD_CEILING` and `INBOX_HARD_CEILING` in `constants.py` `SCAN_BOUNDS` clamp `search_emails` and `list_inbox_emails` to at most **50 messages scanned per call**, regardless of `limit` / `max_emails` / `recent_days`; `get_statistics` per-mailbox reads share the same 50-message cap (fanning across 10 or 20 mailboxes instead); `mailbox="All"` fan-out stays capped at 10 accounts. See `docs/CLAUDE-conventions.md` § Centralized scan caps.
- **Mail calls are serialized:** every `osascript` call goes through one process-wide lock in `core/applescript.py`. Concurrent/parallel Mail tool calls queue behind each other and can time out. Call one Mail tool at a time.
- Mutations: `normalize_message_ids` / `message_ids` for targeted ops. Detail: `docs/CLAUDE-conventions.md`.

## Structured error codes (agent-facing)

Returned as JSON (`serialize_tool_error`) with `code`, `message`, and `remediation` fields. Tests in `tests/cross_cutting/test_phase_2_scan_hardening.py` and `tests/search/test_mail_search_tools.py` lock the contracts.

| Code | When | Remediation hint |
|------|------|------------------|
| `FILTER_SCAN_DISABLED` | `move_email` / `update_email_status` / `manage_trash` called with filters but no `message_ids` and `allow_filter_scan=False` | Collect ids first; or `allow_filter_scan=True` for approved bulk |
| `BODY_SCAN_DISABLED` | `search_emails(body_text=...)` without `allow_body_scan=True` | Narrow with subject/sender/date; or opt in with tight `date_from` |
| `UNBOUNDED_SCAN_REQUIRED` | Routine scan with `recent_days=0` / `max_emails=0` | Pass a bounded window (`recent_days` / `date_from`) or page through bounded calls (`export_emails`, `list_inbox_emails`, `search_emails`); `full_inbox_export` is disabled and is not a valid remediation |
| `INVALID_SCAN_WINDOW` | Forged or out-of-policy `ScanWindow` token | Call `bounded_inbox_scan()` only |
| `WHOSE_ID_LIST_TOO_LARGE` | `message_ids` longer than `MAX_WHOSE_IDS` (50) | `iter_id_chunks` + one call per batch |
| `UNSAFE_WHOSE_ON_LIST` | `build_bounded_message_scan(..., whose_condition=...)` | Use `build_bounded_filtered_scan` |

## Forbidden AppleScript patterns

**Lint-enforced** by `tests/core/test_no_unbounded_whose.py` — these are the catalogued crash modes. Detail + safe alternatives: [`docs/CLAUDE-conventions.md § Forbidden AppleScript patterns`](../../../docs/CLAUDE-conventions.md#forbidden-applescript-patterns-lint-enforced).

| Don't write | Failure mode | Write instead |
|-------------|--------------|---------------|
| `<sliceVar> whose <pred>` (slice-bound list + `whose`) | Gmail crash: `Can't get {message id N of mailbox "[Gmail]/All Mail" ...} whose ...` | `build_bounded_filtered_scan(...)` from `bounded_scan` |
| `every message of MB whose <non-id-pred>` (unbounded `whose`) | Materializes whole mailbox; hangs on 24K+ inboxes | `build_bounded_message_scan(...)` + in-loop `repeat ... if` |
| `every message of MB` (no `whose`) | Raw enumeration | `messages 1 thru N of MB` |
| `build_bounded_message_scan(..., whose_condition=...)` | Raises `UNSAFE_WHOSE_ON_LIST` at runtime | `build_bounded_filtered_scan(...)` |
| `build_whose_id_list(ids)` with > 50 ids | Mail parser crash/hang; raises `WHOSE_ID_LIST_TOO_LARGE` | `iter_id_chunks(ids)` + loop |
| Pipe-row emit without `sanitize_pipe_delimited_field` on user fields | Subject containing `&#124;&#124;&#124;` corrupts `message_id` → wrong-email delete | `core.sanitize_pipe_delimited_field("messageSubject")` etc. |

When in doubt, copy the pattern from `search/emails.py`'s per-message loop — it has been audited as Gmail-safe and Exchange-bounded.

## Account scoping

`account: Optional[str] = None` → `server.DEFAULT_MAIL_ACCOUNT`; error if unset. Exceptions: `synchronize_account` (None = all accounts, but requires `confirm_sync=True`). `inbox_dashboard` also respects `DEFAULT_MAIL_ACCOUNT` and only fans out across all accounts when no account/default is configured. `all_accounts=True` overrides default scoping.

## JSON `output_format`

Normalized dict JSON: `get_statistics`, `get_inbox_overview`, `list_inbox_emails`, `list_mailboxes`, `get_needs_response`, `get_awaiting_reply`, and `get_top_senders`.

`reply_to_email(output_format="json")` is a compose contract for verified `mode="draft"` / `mode="open"` only. It returns reply artifact metadata including `draft_id`, `verified_draft_id`, `exact_id_verified`, `attachment_status`, `attachment_count`, `attachments_applied`, and verification status fields. Effective `mode="send"` with JSON is rejected before mutation because sent replies do not produce a verifiable Drafts artifact.

## Agent-facing selection

Workflow skills under [`../../skills/`](../../skills/) document **when** to call each tool (triage vs archive vs compose). After adding/removing tools, update relevant `plugin/skills/*/SKILL.md` frontmatter tool lists and run **`plugin-dev:skill-reviewer`**.

## Compose defaults (`compose/` package)

| Tool | Default | Notes |
|------|---------|-------|
| `compose_email` | `mode="draft"` | New standalone message only; refuses reply-like drafts unless `standalone_confirmed=True` |
| `reply_to_email` | `mode="draft"` (via `send=False`), `native_format=True` | `native_format=True` is the only supported path: it opens Mail's reply window (rich quote bar + logo signature) and types `reply_body` above the quote, which needs window focus + **Accessibility permission** or returns `REPLY_WINDOW_FOCUS_FAILED` (no draft saved). `native_format=False` returns `WINDOWLESS_FALLBACK_DISABLED` unless `allow_windowless_fallback=True` is explicitly passed (deliberate headless/CI only, never set by agents). Both verify exact Drafts id first with bounded fallback, expose `exact_id_verified` in JSON, and preserve known `draft_id` on verifier timeout/error |
| `verify_draft` | read-only | Exact Drafts id snapshot for recipients, body, attachments, signatures, quoted original, and thread headers. Optional `resolve_source=True` (`resolve_recent_days=30.0` default) maps the reply's `In-Reply-To` header back to its source Inbox message via one bounded `search_emails(internet_message_id=...)` call, adding a `source` block (`resolved`/`not_found_in_window`/`no_in_reply_to_header`) |
| `verify_drafts` | read-only | Batch exact Drafts id snapshots with per-draft JSON payloads; accepts the same `resolve_source` / `resolve_recent_days` options as `verify_draft` |
| `forward_email` | `mode="draft"` | Same id-first rule as reply |
| `create_rich_email_draft` | saves + closes | Standalone only; same reply-like guard; `review_in_mail=True` for saved-open review |

Do not match outgoing rich drafts by subject — `_save_new_compose_window_as_draft()` saves the compose window opened by this call, identified by an id diff against the `outgoing messages` snapshot taken before the open (never `item 1`, never a pre-existing window). Detail: [`docs/CLAUDE-conventions.md`](../../../docs/CLAUDE-conventions.md) § Compose and draft modes.

## Module size

Every tool surface is now a split-by-domain package under the **600 LOC** budget; the `compose/`, `search/`, `inbox/`, `manage/`, `analytics/`, and `smart_inbox/` packages are the worked examples (search: `emails.py`, `by_id.py`, `thread.py`, plus `records.py`/`script.py`/`dispatch.py` leaves; inbox: `list_emails.py`, `unread_counts.py`, `accounts.py`, `mailboxes.py`, `overview.py`, plus `parsing.py`/`replied.py`/`list_scripts.py` leaves; manage: `move.py`, `attachments.py`, `status.py`, `trash.py`, `mailbox.py`, `sync.py`, plus the shared `helpers.py` leaf; analytics: `attachments.py`, `statistics.py`, `export.py`, `export_helpers.py`, `full_export.py`, `dashboard.py`, plus the pure `statistics_parsing.py` leaf; smart_inbox: `awaiting_reply.py`, `needs_response.py`, `top_senders.py`, plus the pure `helpers.py` leaf), each file under budget, linked through the package `__init__.py` facade. CI warns on every run and **blocks growth** past the baseline in `tests/fixtures/module_line_budget/baseline.json`. Prefer the same domain split over reviving single-file monoliths. See [`docs/CLAUDE-conventions.md`](../../../docs/CLAUDE-conventions.md) § Module line budget.

## Related

`../core/` (bridge package), `../server.py` (mcp + annotations), `../../tests/` (mock `run_applescript`), [`tasks/reference/phase-3-annotation-matrix.md`](../../../tasks/reference/phase-3-annotation-matrix.md).
