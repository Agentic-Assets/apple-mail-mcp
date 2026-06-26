# tools/ — MCP tool registrations
All `@mcp.tool` handlers live here; `apple_mail_mcp/__init__.py` imports these six modules (side-effect registration). **29 tools** — verify: `rg '^@mcp\.tool' plugin/apple_mail_mcp/tools/*.py | wc -l`.

## Module map

| Module | # | Purpose / tools |
|--------|---|-----------------|
| `inbox.py` | 6 | Listing & overview: `list_inbox_emails`, `get_mailbox_unread_counts`, `list_accounts`, `list_account_addresses`, `list_mailboxes`, `get_inbox_overview` |
| `search.py` | 3 | Find & fetch: `search_emails`, `get_email_by_id`, `get_email_thread` |
| `compose.py` | 6 | Send & drafts: `create_rich_email_draft`, `compose_email`, `reply_to_email`, `forward_email`, `manage_drafts`, `verify_draft` |
| `manage.py` | 6 | Move/status/trash/sync: `move_email`, `save_email_attachment`, `update_email_status`, `manage_trash`, `create_mailbox`, `synchronize_account` |
| `analytics.py` | 5 | Stats & export: `list_email_attachments`, `get_statistics`, `export_emails`, `inbox_dashboard`, `full_inbox_export` |
| `smart_inbox.py` | 3 | Triage heuristics: `get_awaiting_reply`, `get_needs_response`, `get_top_senders` |

## Add a tool

1. Pick module by domain; add `@mcp.tool(annotations=…)` using presets from `../server.py` (matrix: `tasks/phase-3-annotation-matrix.md`).
2. `@inject_preferences` on user-facing tools; user strings → `core.escape_applescript()`; fan-out → `async` + `asyncio.to_thread`.
3. New file → import in `__init__.py`; bump five version manifests + `apple-mail-mcpb/manifest.json` `tools[]` and advertised tool count.

## Performance (summary)

- Default `recent_days=2.0` (48h). Tools refuse unbounded scans (`recent_days=0` / `max_emails=0`) with `code: UNBOUNDED_SCAN_REQUIRED`. The only tool that walks the entire inbox is `full_inbox_export` (slow; documented cost). Prefer bounded newest-message slices (`messages 1 thru N`) over broad `whose` clauses on large remote mailboxes.
- Pass `timeout` through to `run_applescript`; catch `AppleScriptTimeout` → structured error with account name.
- **ID-first mutations (v3.7.0+):** `move_email`, `update_email_status`, and `manage_trash` prefer `message_ids` from a prior list/search. Filter paths require `allow_filter_scan=True` or return `FILTER_SCAN_DISABLED`. `search_emails` requires `allow_body_scan=True` when `body_text` is set or returns `BODY_SCAN_DISABLED`.
- **Scan caps (v3.7.1):** bounded slices read `SCAN_BOUNDS` in `constants.py` (search ceiling 250, inbox max 500, `mailbox="All"` fan-out 10). See `docs/CLAUDE-conventions.md` § Centralized scan caps.
- Mutations: `normalize_message_ids` / `message_ids` for targeted ops. Detail: `docs/CLAUDE-conventions.md`.

## Structured error codes (agent-facing)

Returned as JSON (`serialize_tool_error`) with `code`, `message`, and `remediation` fields. Tests in `test_phase_2_scan_hardening.py` and `test_mail_search_tools.py` lock the contracts.

| Code | When | Remediation hint |
|------|------|------------------|
| `FILTER_SCAN_DISABLED` | `move_email` / `update_email_status` / `manage_trash` called with filters but no `message_ids` and `allow_filter_scan=False` | Collect ids first; or `allow_filter_scan=True` for approved bulk |
| `BODY_SCAN_DISABLED` | `search_emails(body_text=...)` without `allow_body_scan=True` | Narrow with subject/sender/date; or opt in with tight `date_from` |
| `UNBOUNDED_SCAN_REQUIRED` | Routine scan with `recent_days=0` / `max_emails=0` | Pass bounded window or use `full_inbox_export` |
| `INVALID_SCAN_WINDOW` | Forged or out-of-policy `ScanWindow` token | Call `bounded_inbox_scan()` only |
| `WHOSE_ID_LIST_TOO_LARGE` | `message_ids` longer than `MAX_WHOSE_IDS` (50) | `iter_id_chunks` + one call per batch |
| `UNSAFE_WHOSE_ON_LIST` | `build_bounded_message_scan(..., whose_condition=...)` | Use `build_bounded_filtered_scan` |

## Forbidden AppleScript patterns

**Lint-enforced** by `tests/test_no_unbounded_whose.py` — these are the catalogued crash modes. Detail + safe alternatives: [`docs/CLAUDE-conventions.md § Forbidden AppleScript patterns`](../../../docs/CLAUDE-conventions.md#forbidden-applescript-patterns-lint-enforced).

| Don't write | Failure mode | Write instead |
|-------------|--------------|---------------|
| `<sliceVar> whose <pred>` (slice-bound list + `whose`) | Gmail crash: `Can't get {message id N of mailbox "[Gmail]/All Mail" ...} whose ...` | `build_bounded_filtered_scan(...)` from `bounded_scan` |
| `every message of MB whose <non-id-pred>` (unbounded `whose`) | Materializes whole mailbox; hangs on 24K+ inboxes | `build_bounded_message_scan(...)` + in-loop `repeat ... if` |
| `every message of MB` (no `whose`) | Raw enumeration | `messages 1 thru N of MB` |
| `build_bounded_message_scan(..., whose_condition=...)` | Raises `UNSAFE_WHOSE_ON_LIST` at runtime | `build_bounded_filtered_scan(...)` |
| `build_whose_id_list(ids)` with > 50 ids | Mail parser crash/hang; raises `WHOSE_ID_LIST_TOO_LARGE` | `iter_id_chunks(ids)` + loop |
| Pipe-row emit without `sanitize_pipe_delimited_field` on user fields | Subject containing `&#124;&#124;&#124;` corrupts `message_id` → wrong-email delete | `core.sanitize_pipe_delimited_field("messageSubject")` etc. |

When in doubt, copy the pattern from `search.py`'s per-message loop — it has been audited as Gmail-safe and Exchange-bounded.

## Account scoping

`account: Optional[str] = None` → `server.DEFAULT_MAIL_ACCOUNT`; error if unset. Exceptions: `synchronize_account` (None = all accounts, but requires `confirm_sync=True`). `inbox_dashboard` also respects `DEFAULT_MAIL_ACCOUNT` and only fans out across all accounts when no account/default is configured. `all_accounts=True` overrides default scoping.

## JSON `output_format`

Normalized: `get_statistics`, `get_inbox_overview`; also `list_inbox_emails`, `list_mailboxes` (`output_format="json"`).

## Agent-facing selection

Workflow skills under [`../../skills/`](../../skills/) document **when** to call each tool (triage vs archive vs compose). After adding/removing tools, update relevant `plugin/skills/*/SKILL.md` frontmatter tool lists and run **`plugin-dev:skill-reviewer`**.

## Compose defaults (`compose.py`)

| Tool | Default | Notes |
|------|---------|-------|
| `compose_email` | `mode="draft"` | New standalone message only; refuses reply-like drafts unless `standalone_confirmed=True` |
| `reply_to_email` | `mode="draft"` (via `send=False`) | Native Mail reply composer; quoted prior messages are automatic; verifies saved draft before success |
| `verify_draft` | read-only | Exact Drafts id snapshot for recipients, body, attachments, signatures, quoted original, and thread headers |
| `forward_email` | `mode="draft"` | Same id-first rule as reply |
| `create_rich_email_draft` | saves + closes | Standalone only; same reply-like guard; `review_in_mail=True` for saved-open review |

Do not match outgoing rich drafts by subject — `_save_front_compose_window_as_draft()` saves Mail's front compose window. Detail: [`docs/CLAUDE-conventions.md`](../../../docs/CLAUDE-conventions.md) § Compose and draft modes.

## Related

`../core.py` (bridge), `../server.py` (mcp + annotations), `../../tests/` (mock `run_applescript`), `tasks/phase-3-annotation-matrix.md`.
