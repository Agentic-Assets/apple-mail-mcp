# Changelog

All notable changes to **apple-mail-mcp** (PyPI: `mcp-apple-mail`) are documented
here. The plugin/MCPB/marketplace versions track this file.

## Unreleased

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
