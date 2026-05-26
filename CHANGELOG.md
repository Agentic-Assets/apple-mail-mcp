# Changelog

All notable changes to **apple-mail-mcp** (PyPI: `mcp-apple-mail`) are documented
here. The plugin/MCPB/marketplace versions track this file.

## Unreleased

## 3.3.0 — 2026-05-26

Phase 2 + Phase 3 hardening: faster analysis paths, structured JSON across
the smart-inbox surface, and one targeted breaking change to
`list_inbox_emails` JSON mode.

### Breaking

- **`list_inbox_emails` JSON mode now returns a Python `dict`, not a JSON
  string.** Stable shape: `{"emails": [...], "errors": [...]}` for every
  `output_format="json"` success and per-account-timeout path.
  - `errors` is always present (empty list when nothing timed out).
  - Account-not-found in JSON mode also returns a dict (`{"error":
    "account_not_found", "account": ..., "available_accounts": [...],
    "emails": []}`).
  - Account-listing timeouts surface as
    `{"emails": [], "errors": ["__account_listing__"]}`.
  - When deprecated aliases (`limit`, `unread_only`) are used, a `warnings`
    list is attached to the same dict.
  - **`UNBOUNDED_SCAN_REQUIRED` refusal errors remain a JSON-encoded string**
    so text-mode and JSON-mode callers see the same payload for that hard
    refusal path.
  - Migration: callers that did `json.loads(result)` on the
    `list_inbox_emails` JSON output should drop the `json.loads` call. The
    repo CLI (`apple-mail list-inbox --json`) handles dicts and strings
    transparently through `_print_result`.

  See `plugin/apple_mail_mcp/tools/inbox.py` and
  `tasks/robustness-backlog-2026-05-22.md` (Phase 3) for context.

### Performance

- **`get_statistics` (`account_overview` scope) uses Mail.app's cheap
  mailbox-count APIs** instead of per-message unread scans. AppleScript now
  emits a `MBOX|||name|||total|||unread` header row per sampled mailbox
  (via `count of messages of aMailbox` + `unread count of aMailbox`); the
  per-message `read status` fetch is gone. `total_emails` and `unread` now
  reflect true mailbox-wide totals across the sampled mailboxes;
  sample-bounded stats (`flagged`, `with_attachments`, `top_senders`,
  `mailbox_distribution` ROW-derived stats) still respect `days_back`.
- **`get_needs_response` reply matching moved to Python.** The inbox
  AppleScript emits a flat `MSG|||message_id|||...` row per candidate;
  replied detection runs as an O(1) set lookup in Python via
  `fetch_replied_ids` and `_normalize_message_id_token` (was O(N×M)
  AppleScript `repeat with repliedRef`). Header-based detection only
  (`In-Reply-To`, `References`) — no subject substring matching.

### Reliability

- **Silent per-message `on error` skips replaced with `errors[]`.** Inner
  per-message failures in `account_overview` are now counted per mailbox
  and surfaced as a single
  `__APPLE_MAIL_MCP_ERROR__|||mailbox|||N message(s) skipped due to read
  errors` line, parsed into the JSON `errors[]`.

### JSON / schema consistency

- **Smart-inbox tools accept `output_format="json"` and return dicts with
  stable keys + `errors[]`:**
  - `get_needs_response` → `{account, mailbox, days_back, max_results,
    high_priority, normal_priority, skipped_replied_count, errors}`
  - `get_awaiting_reply` → `{account, days_back, max_results, awaiting,
    errors}`
  - `get_top_senders` → `{account, mailbox, days_back, top_n,
    group_by_domain, senders, total_analysed, mailbox_count,
    unique_senders, scan_cap, errors}`
  - Error and timeout paths return dicts in JSON mode.
- `inbox_dashboard` JSON path returns a Python dict (already true in code;
  verified and documented).

### Docs

- `docs/AGENT_LIVE_TESTING.md` gains a "`--raw` examples for advanced
  wrapper options" subsection covering `get-inbox-overview`,
  `get-statistics` (three scopes), smart-inbox triage, `inbox-dashboard`
  JSON mode, and `full-inbox-export`.

See `tasks/robustness-backlog-2026-05-22.md` Phase 2 + Phase 3 for the
backlog this batch closes.
