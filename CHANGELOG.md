# Changelog

All notable changes to **apple-mail-mcp** (PyPI: `mcp-apple-mail`) are documented
here. The plugin/MCPB/marketplace versions track this file.

## Unreleased

Mcporter wrapper + large-mailbox hardening on top of 3.4.0. No tool signatures
or return shapes changed.

- **`search_emails` subject-only fast path**: narrow subject lookups (no sender,
  body, attachment, or read-status filters) now scan only the requested page
  size and skip per-message date/sender/read-status reads. No-hit lookups on
  large Exchange mailboxes that previously took 48–115s now complete inside
  the wrapper request ceiling. `recent_days` still controls the bounded slice
  for searches that include other filters.
- **`search_emails` recent-window early break**: bounded scans with a
  `date_from` lower bound now read `date received` first and `exit repeat`
  once messages cross the cutoff, avoiding subject/sender/read-status reads
  on messages outside the window.
- **`full_inbox_export` AppleScript syntax fix**: per-field `(try … end try)`
  expressions were invalid AppleScript inside a concatenation and aborted the
  tool with `-2741`. Replaced with per-field variable assignments inside a
  `try` block, then concatenated. Repro: `max_emails=1` through `--raw`.
- **`full_inbox_export` named-flag input**: `fields` now accepts a
  comma-separated string in addition to a list, so generated mcporter wrappers
  that flatten the list parameter still work without `--raw`.
- **`tools/patch_mcporter_wrapper.py`**: post-generation patch renames the
  mcporter global `--timeout <ms>` (which collides with per-tool `timeout`
  seconds) to `--request-timeout-ms`, and optionally repoints embedded
  `start_mcp.sh` paths for relocated plugin roots.
- **`check_wrapper_surface.py`** now flags the global `--timeout <ms>` flag
  in generated wrappers and reminds operators to run `patch_mcporter_wrapper.py`.
- **`validate_manifests._tracked_plugin_files`** is more defensive when
  `git ls-files` returns nothing while `plugin/` exists on disk.

## 3.4.0 — 2026-05-26

Hardening release: 15 real bugs fixed (1 HIGH security, 8 type-safety / None-handling,
3 silent-error / resource, 3 AppleScript-injection / shell-quoting) plus a new lint +
static-analysis + property-test baseline. No breaking changes to MCP tool signatures
or return shapes.

### Security

- **HIGH — `create_rich_email_draft` path traversal**: `output_path` accepted from
  the caller was written directly to disk without `validate_save_path` / sensitive-dir
  guard. An attacker could pass `output_path="~/.ssh/authorized_keys"` (or `~/.aws/credentials`,
  `~/.claude/settings.json`, `~/Library/Keychains/*`) and silently corrupt the file with
  a draft `.eml` body. Now resolved with `os.path.realpath(os.path.expanduser(...))`
  and rejected against the shared `SENSITIVE_DIRS` list before any write.
- **`search_emails` forgotten-wiring**: `escaped_sender = escape_applescript(sender)`
  was computed but never used; the raw `sender` string flowed into the AppleScript
  filter fragment. Now wired correctly so quote / backslash / newline injection
  characters are escaped before they reach `osascript`.
- **`compose.py` shell-quote consistency**: 6 `do shell script "cat '{path}'"` /
  `"rm -f '{path}'"` call sites in `_send_html_email` / `reply_to_email` /
  `forward_email` rewritten to `"cat " & quoted form of "{path}"`, matching the
  safe pattern already used for `body_temp_path`. Single-quoted bare paths are
  brittle if `tempfile.gettempdir()` ever returns a path containing a quote.

### Reliability

- **`validate_save_path` NUL-byte contract change** (minor API): paths containing
  `\x00`–`\x1F` or `\x7F` previously raised `ValueError` from `os.path.realpath`,
  bubbling an uncaught exception out of the MCP tool boundary. Now returns the
  standard structured-error string, matching every other validator in `core.py`.
  Surfaced by a new Hypothesis property test.
- **`analytics.py` entire-mailbox export file-handle leak**: the batch-export
  `on error -- Continue` handler skipped `close access fileRef`, leaking a kernel
  fd per failed message. Now closes inside a guarded `try / close access / end try`
  block, mirroring the single-email export path.
- **`core.fetch_replied_ids_impl` silent except**: caught `Exception` and returned
  empty `set()` for ALL non-timeout errors (`OSError`, `PermissionError`, broken
  Mail connection). Triage tools (`get_awaiting_reply`, `get_needs_response`)
  then falsely reported every sent message as awaiting reply. Now logs at
  `WARNING` with exception class + message before returning, while still
  returning empty so callers keep working.
- **`update_email_status` bulk-action silent fallback**: bulk
  `set read status of every message …` failures fell through to the per-message
  loop without surfacing the bulk error. Now captures `errMsg`/`errNum` in the
  `on error` block and emits a `BULKERR|errNum=… errMsg=…` row so callers see
  the real failure.
- **`subprocess.run(["open", "-a", "Mail", ...])` in `create_rich_email_draft`**:
  raised `CalledProcessError` / `FileNotFoundError` uncaught when Mail.app
  wasn't available or the `.eml` was malformed. Now wrapped in try/except
  returning a structured error.

### Type-safety (mypy: 27 errors → 0 across 16 source files)

- **`compose.py` `Optional[str]` flowing into non-None operations** (5 sites):
  `account.strip()` on `str | None` → `AttributeError`; `"Account: " + account`
  string concatenation with `None` → `TypeError`; `escape_applescript(account)`
  silently stringifying `None` to the literal `"None"` reaching synthesised
  AppleScript. Each fixed with an `assert account is not None` immediately
  after the `_resolve_account` error guard, documenting the invariant that
  a non-`None` account and a `None` error are mutually exclusive.
- **`_build_found_message_lookup` return type tightened** from
  `Tuple[str, Optional[object]]` to `tuple[str, ToolError | None]` —
  reflects the actual runtime invariant and stops mypy noise at every
  call site.
- **`inbox.py` `**dict[str, int | str | None]` typed-kwargs unpacking** (4 sites):
  a heterogeneous-value dict was spread into functions with per-param types,
  hiding potential `TypeError`s at runtime. Replaced with explicit kwargs at
  every call site. Same file: `body` variable shadowing (`Dict[str, Any]`
  then re-assigned `str`) fixed by renaming to `text_body`; `item` dict in
  `list_mailboxes` annotated as `Dict[str, Any]`.
- **`core.parse_email_list` missing annotations** on `emails` and `current_email`
  (residual pre-existing mypy warning) — annotated explicitly.

### Testing & static analysis

- **+279 tests** (suite 367 → 646+), all green:
  - +90 AppleScript script-idiom regression tests (`test_applescript_script_idioms.py`)
  - +12 `osacompile` parse-checks per builder (skips on Linux, runs on macOS CI)
  - +25 Hypothesis property tests on `escape_applescript`, `validate_account_name`,
    `validate_save_path` — found the NUL-byte bug
  - +33 `jsonschema` contract tests for `get_inbox_overview`, `list_inbox_emails`,
    `get_awaiting_reply`, `search_emails`, `get_email_thread`
  - +70 bug-fix regression tests (`test_compose_none_handling.py`,
    `test_compose_security.py`, `test_core_validators.py`, `test_search_escaping.py`,
    `test_inbox_typed_kwargs.py`, `test_analytics_resource_safety.py`,
    `test_core_fetch_replied_ids.py`, `test_manage_bulk_action_errors.py`)
- **New dev dependencies** under `[project.optional-dependencies] dev`:
  `ruff`, `mypy`, `pytest-cov`, `hypothesis`, `jsonschema`. Install with
  `pip install -e ".[dev]"`.
- **`tools/dev-check.sh lint` tier**: runs `ruff check`, `ruff format --check`,
  and `mypy` on the plugin source. Wired into the `release` tier.
- **`tools/pre-commit-validate.sh`**: now runs `ruff check` on staged Python files.
- **CI**: `.github/workflows/ci.yml` installs dev deps and runs `ruff check`
  on `plugin/ tools/ tests/`.
- **`pyproject.toml`**: `[tool.ruff]`, `[tool.ruff.lint]` (rules E, F, I, B,
  UP, SIM, RET, PTH), `[tool.mypy]` (permissive baseline, no `disallow_untyped_defs`),
  `[tool.pytest.ini_options]`.
- **Coverage baseline**: 78% measured (lowest: `__main__.py` 48%, `manage.py` 62%).

## 3.3.1 — 2026-05-26

Hotfix for a 3.3.0 regression in `get_awaiting_reply`: the Phase 2 inbox
header-extraction AppleScript used `header value of header named "X" of
msg`, which is not valid Mail.app dictionary syntax and failed to parse
with osascript `-2740` ("A application constant or consideration can't
go after this identifier"). Replaced with the standard `headers of
aMessage` iteration that filters by `name of aHeader` and reads
`content of aHeader`. The INBOXHDR row protocol consumed by the Python
parser is unchanged; tests cover the parser behavior, not the broken
AppleScript form, so no test churn was required.

Reproduced on live TU Exchange inbox (24K messages): pre-fix returned
`AppleScript error: ... syntax error ... (-2740)`; post-fix returns 4
sent emails awaiting reply over a 7-day window.

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
