# Apple Mail Plugin Robustness Backlog Snapshot — 2026-05-22

This sidecar preserves the detailed backlog that previously lived in
`tasks/todo.md`. Keep `tasks/todo.md` as the tiny active pointer.

## Phase 1 — Wrapper parity + honest perf gates

- [x] Regenerate or repair generated MCP wrapper — regenerated mcporter bundle; `get-email-by-id` now on `apple-mail --help`.
- [x] Add wrapper command-surface smoke check — `tools/check_wrapper_surface.py` + `tests/test_wrapper_surface.py`.
- [x] Document repo CLI vs wrapper flags — `docs/AGENT_LIVE_TESTING.md` profiles, regen, naming table.
- [x] Scale `perf-test` metadata threshold — `2000 + max(0, mailbox_count - 20) * 35` ms in `cli.py`.
- [x] Add `perf-test --include-analysis --allow-heavy-mail-scan` — analysis cases behind explicit heavy-scan opt-in.
- [x] Overview threshold — `--profile light` (10s) vs `production` (15s).
- [x] Update `docs/AGENT_LIVE_TESTING.md` — light vs production profiles; heavy analysis opt-in.
- [ ] Push `.github/workflows/ci.yml` — blocked on GitHub OAuth `workflow` scope from Cursor; push from local terminal.

## Phase 2 — Analysis & metadata speed

### `list_mailboxes`

- [x] Default `include_counts=False` for perf metadata case.
- [x] Add `max_mailboxes` cap + `{truncated, total}` in JSON mode.

### `get_statistics` / `account_overview`

- [x] Lower scan defaults for short `days_back` (10 mailboxes x 100 messages when `days_back <= 7`; else 20 x 500).
- [x] Prefer `unread count of aMailbox` over per-message unread scan where scope allows.
      Account-overview now emits `MBOX|||name|||total|||unread` header rows from
      Mail.app's `count of messages` + `unread count` APIs; the per-message
      `read status` fetch is dropped. `total_emails` / `unread` now reflect true
      mailbox-wide totals across the sampled mailboxes; sample-bounded stats
      (flagged, with-attachments, top_senders, mailbox_distribution) still
      respect `days_back`.
- [x] Replace any remaining silent `on error` skips with `errors[]` in response.
      Per-message inner-loop errors are now counted and surfaced as a single
      `__APPLE_MAIL_MCP_ERROR__|||mailbox|||N message(s) skipped due to read
      errors` line per mailbox, parsed into the JSON `errors[]`.

### `get_needs_response`

- [x] Remove default `content of aMessage` fetch; `scan_body: bool = False`.
- [x] Tighter inbox/sent caps (`inbox_cap <= 100`, `sent_cap = 100`).
- [x] Reply matching in Python rather than nested AppleScript.
      Inbox script now emits a flat `MSG|||...` row per candidate; replied set
      built in Python via `fetch_replied_ids` and matched with O(1) set lookup
      (was O(N×M) AppleScript `repeat with repliedRef`).

### `get_awaiting_reply` / `get_top_senders`

- [x] Async dual-script pattern for awaiting-reply.
- [x] Python-side aggregation for top-senders (`Counter` + lower `scan_cap`).

Verification target: `.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --account cayman@agenticassets.ai --json` all green.

## Phase 3 — JSON finish

- [x] `inbox_dashboard` -> dict JSON (not string).
      Already returns a Python dict for `output_format="json"` (verified in
      `analytics.py:inbox_dashboard`, lines 1696-1705).
- [x] Smart inbox tools -> structured JSON + `errors[]`.
      `get_needs_response`, `get_awaiting_reply`, and `get_top_senders` all
      accept `output_format="json"` and return a dict with stable keys plus
      an `errors[]` array. Error and timeout paths return dicts in JSON mode.
- [x] `list_inbox_emails` JSON -> `{emails, errors}` (breaking; document if changed).
      JSON path now always returns a Python `dict` (not a JSON string) with stable
      shape `{"emails": [...], "errors": [...]}`. `errors` is always present
      (empty list when no per-account timeout). Account-not-found JSON returns a
      dict in JSON mode too. `UNBOUNDED_SCAN_REQUIRED` refusals stay as a JSON
      string for parity with text-mode callers. Callers that previously did
      `json.loads(result)` should drop the call.
- [x] Add generated-wrapper `--raw` examples for `get-inbox-overview` and wrapper commands with poor flag discovery.
      `docs/AGENT_LIVE_TESTING.md` now has a "--raw examples for advanced
      wrapper options" subsection covering statistics scopes, smart-inbox
      triage, dashboard JSON mode, and full inbox export.

## Phase 4 — Ship hygiene

- [x] Version bump (five files) -> 3.1.8.
- [x] MCPB rebuilt locally as `apple-mail-mcp-v3.1.8.mcpb`.
- [x] Marketplace `metadata.version` documented as marketplace metadata, not plugin release version.
- [x] Local manifest validator before merge.
- [x] `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh` release gate added and passing.

## Deferred

- [ ] Hybrid SQLite read-path — Envelope Index spike; feature-flagged.
- [ ] Id-first destructive actions — see `id-first-refactor-spec.md`.
- [x] Plugin workflow skill suite shipped.
- [ ] `include_timing` telemetry on tool responses.
- [ ] Normalize generated wrapper JSON — mcporter `content` wrapping vs direct dict.
- [ ] MCP registry submit (`server.json`).

