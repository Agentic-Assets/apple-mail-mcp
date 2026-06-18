# Changelog

All notable changes to **apple-mail-mcp** (PyPI: `mcp-apple-mail`) are documented
here. The plugin/MCPB/marketplace versions track this file.

## 3.7.2 — 2026-06-17

Fixes the current GitHub issue queue around exact-id workflows, raw source
access, and reply draft reliability. Tool count is now 29.

### Added

- **`get_email_source`** fetches raw RFC 822/MIME source by exact numeric Apple
  Mail message id for original headers, MIME boundaries, href URLs, and faithful
  archival.

### Changed

- **`get_email_by_id(..., include_content=True, output_format="json")`** now
  returns bounded full `content` while preserving `content_preview`.
- **`get_needs_response(..., output_format="json")`** now returns numeric Apple
  Mail ids in `message_id` and preserves the Internet Message-ID separately as
  `internet_message_id`.
- **`reply_to_email`** now writes reply bodies through Mail's object model,
  emits `Draft ID`, verifies that exact draft id, and avoids subject-only
  verification matches.
- **`manage_drafts(action="list")`** supports `max_results` and stops after
  enough visible matches. Draft `send`, `open`, and `delete` can target exact
  `draft_id`.

### Fixed

- Reply drafts no longer rely on clipboard paste for body insertion, reducing
  body loss, attachment/body ordering races, and duplicate signature-only draft
  risk.
- `include_signature=False` for replies now explicitly clears Mail's native
  signature selection on the reply message.

## 3.6.1 — 2026-06-07

Codex plugin install-smoke regression recovery and test-count verification.

### Fixed

- **Codex plugin install surface** — recovered `plugin/.codex-plugin/plugin.json` versioning and marketplace routing after Codex setup work (2026-06-07).
- **Test count verification** — confirmed 798 tests + 30 subtests via `pytest --collect-only -q` in CI; updated root guidance.

### Changed

- **Documentation alignment** — manifest validator, release gate, and CI now all source from canonical `pyproject.toml` version (3.6.1).
## 3.7.1 — 2026-06-09

Tighter centralized ``SCAN_BOUNDS`` for large-mailbox performance. Tool count
unchanged (28).

### Changed (scan caps)

Search window ceiling **250** (was 500), search base **100** (was 200), inbox
unread scan max **500** (was 1000), ``mailbox="All"`` fan-out unchanged at
**10** folders, explicit multi-mailbox search cap **20** (was 50).
``compute_scan_upper_bound()`` reads defaults from ``SCAN_BOUNDS`` (scale **25**
days/message, was 50). ``get_statistics`` short windows: 10 mailboxes × **75**
messages; longer windows: 20 × **250**.

## 3.7.0 — 2026-06-09

ID-first mutation hardening for large mailboxes (24k+). Tool count unchanged (28).

### Changed (performance / agent safety)

- **`move_email`**, **`update_email_status`**, **`manage_trash`**: filter-based scans
  (subject/sender/date) now require **`allow_filter_scan=True`**. Default path is
  **`message_ids=[...]`** from a prior `list_inbox_emails` or `search_emails` call.
  Filter escape hatch responses include an explicit slow-scan warning.
- **`search_emails`**: **`body_text`** requires **`allow_body_scan=True`** or returns
  structured **`BODY_SCAN_DISABLED`**.
- **`mailbox="All"`** searches cap at **10** folders (was 50); JSON sets
  `mailboxes_truncated` when capped.
- Sender-only searches emit pairing hints (co-filter with subject or tight
  `recent_days`).

### Added

- Structured error **`FILTER_SCAN_DISABLED`** with remediation pointing to ID-first
  workflow and `allow_filter_scan` escape hatch.
- **`get_email_thread(message_id=...)`** for thread drill-down without subject
  re-search.
- **`list_email_attachments(message_ids=[...])`** and **`export_emails`**
  `single_email` **`message_id`** param.

### Fixed

- Mutation filter paths now pass **`recent_days`** into the search helper so scan
  caps use `compute_scan_upper_bound()` instead of bare `limit+1`.

## 3.6.0 — 2026-06-05

Compose-path race elimination + reliable draft lookup, from a second live draft-QA
session on the 24K Exchange account. The 3.5.0 `saving no` change was insufficient:
the reply/forward path was driving Mail's GUI, which is inherently racy. Tool count
unchanged (28); one additive optional param (`manage_drafts(subject_contains=...)`).

### Fixed (compose GUI races — data-loss/corruption class)

- **Reply/forward no longer leak a draft's body into the wrong thread, duplicate,
  or save empty.** `reply_to_email` and `forward_email` previously opened a Mail
  compose window (which auto-saves an empty draft *shell* → the duplicate), pasted
  the body from the **system clipboard** via `keystroke "v"` into whatever window
  had focus (→ body landing in an unrelated thread; or pasting nothing → empty
  draft), and closed with the **positional** `close window 1` (→ wrong window).
  Both tools now build the draft entirely through Mail's **object model**
  (`make new outgoing message` + `make new to recipient`), exactly like
  `compose_email`: **no window, no clipboard, no System Events, one `save`.** This
  removes the entire race class.
- **`reply_to_all=True` now includes every original party.** Instead of trusting
  Mail's reply-to-all (which silently dropped recipients), the reply now sets
  recipients **deterministically**: the original sender as To, and every other
  To/Cc party as Cc, excluding the sender and the account's own addresses.
- **Newly-created drafts are found reliably.** `manage_drafts(action="list")` and
  the draft lookup behind `send`/`open`/`delete` now read the **newest** drafts
  (a `messages startIdx thru totalDrafts` tail, newest-first) instead of the 100
  *oldest* (`messages 1 thru 100`), so a just-created draft is never missed when a
  mailbox holds >100 drafts. No date filter is used — fresh `outgoing message`
  drafts have a null `date received`, which previously made date-filtered draft
  searches silently drop them.
- **`compose_email` HTML path hardened** — the rich-HTML compose (which still needs
  the clipboard) now targets `window of newMsg` (and brings it to front before the
  paste) instead of the positional `window 1`.

### Added

- **`manage_drafts(subject_contains=...)`** — optional case-insensitive, in-loop
  subject filter for `action="list"`, giving a fast, bounded "find the draft I just
  created" lookup over the small Drafts mailbox. Prefer this (or `get_email_by_id`)
  over `search_emails` for draft verification — `search_emails` runs a date-filtered
  scan that is slow on large accounts and drops null-date drafts.

### Changed (behavior trade-off)

- **Replies/forwards are now reliable plain-text "Re:"/"Fwd:" drafts.** Because the
  clipboard was the only thing inserting rich HTML, eliminating it means reply and
  forward bodies are plain text with a `> `-quoted copy of the original (bounded to
  4000 chars) and a `Re:`/`Fwd:` subject. The draft is **correctly addressed and
  always contains your text** — the priority after the cross-thread corruption.
  `body_html` is still accepted on `reply_to_email` for backward compatibility but
  is ignored. Trade-off: replies no longer carry native `In-Reply-To`/`References`
  headers (they thread visually via subject + quote). `create_rich_email_draft` and
  `compose_email` still produce rich HTML for genuinely standalone messages.

## 3.5.0 — 2026-06-05

Live field-report hardening (draft QA workflow on a 24K Exchange account) plus
the previously-unreleased mcporter wrapper + large-mailbox work. Tool count is
unchanged (28); changes are additive params/actions/fields, all backward
compatible.

### Fixed (draft QA field report)

- **Reply / forward / rich drafts no longer create duplicate drafts.** The
  draft paths persisted twice — an explicit `save <message>` *then*
  `close window 1 saving yes` — which committed a second, byte-identical copy
  to Drafts (observed as same-second duplicate pairs). Every draft path now
  persists exactly once (`save` then `close window 1 saving no`; the rich-draft
  helper keeps its single `Cmd+S` and closes with `saving no`). Verified live:
  one reply call yields exactly one threaded draft.
- **`search_emails` no longer hangs on Exchange when a per-mailbox scan is
  slow.** A per-mailbox `with timeout` wrapper (added during the unreleased
  work) fired on the 24K-message Exchange INBOX, and the inner candidate-fetch
  `try` swallowed the timeout into a silent **0-row** result. The wrapper is
  removed; per-folder failures are still isolated by the existing
  `on error → ERROR_MAILBOX` handler, and the whole call is bounded by the
  single outer timeout budget.
- **`get_email_by_id` header parsing tolerates value-less headers.** A bare
  `In-Reply-To:` / `References:` line (no value) would make the `text N thru -1`
  slice raise and the surrounding `on error` wipe *both* fields — discarding a
  sibling header that had already parsed cleanly. Length guards now skip empty
  header values so threading metadata survives.

### Added (draft QA field report)

- **`get_email_by_id` now returns threading + recipient metadata** so an agent
  can confirm a draft is a correctly-addressed reply without opening Mail:
  `to`, `cc`, `bcc`, `in_reply_to`, `references` (parsed from `all headers`),
  and a computed `has_quoted_original` flag. Single-message, bounded, fast.
- **`search_emails(mailboxes=[...])`** — new optional parameter to search an
  explicit list of folders (e.g. `["Archive", "Sent"]`) instead of one mailbox
  or paying for `mailbox="All"`. Missing folders degrade to a structured
  per-mailbox error rather than failing the call. Recommended over `"All"` on
  large Exchange/Gmail accounts.
- **`manage_drafts(action="list")` is now triageable** — each draft reports its
  `Id`, `To` recipients, and a short body snippet; new `hide_empty=True` skips
  orphaned blank drafts.
- **`manage_drafts(action="cleanup_empty")`** — removes orphaned blank drafts
  (blank subject **and** empty body). Preview-only by default (`dry_run=True`)
  with a `max_deletes` safety cap, matching the repo's destructive-op
  conventions.
- **CLI parity for the new draft/search surfaces.** `apple-mail search` gains
  `--mailboxes a,b,c` (comma-separated targeted-folder search); `apple-mail
  drafts list` gains `--hide-empty`; and a new `apple-mail drafts cleanup-empty`
  subcommand previews orphaned blanks by default and only deletes with
  `--execute` (`--limit` caps the batch). The repo CLI is the live-test harness,
  so these mirror the MCP params 1:1.

### Changed (draft QA field report)

- **Bulk `search_emails` no longer resolves per-message recipients.** Resolving
  `to recipients`/`address of` inside the bulk scan can *hang* (uncatchable by
  `on error`) on large remote mailboxes. Recipients are now fetched per message
  via `get_email_by_id` (and shown in `manage_drafts` list over the small local
  Drafts mailbox). The record layout reserves the fields, so they still surface
  wherever a tool populates them.

### Fixed (Gmail crash)

- **`list_inbox_emails(include_read=False)` no longer crashes on Gmail / Google
  Workspace accounts.** The historical AppleScript `(candidateMessages whose
  read status is false)` evaluated `whose` against a list of message
  references; on Gmail those refs point at `[Gmail]/All Mail`, which
  Mail.app rejects with `Can't get {message id N of mailbox
  "[Gmail]/All Mail" ...} whose read status = false`. Replaced with an
  in-loop `if read status of aMessage is false` filter (the same pattern
  `search_emails` already uses safely). Works on every account type
  including 24K+ Exchange inboxes.
- **`reply_to_email` / `forward_email` subject lookup hardened the same
  way.** The historical `whose subject contains "X" and date received >=
  cutoff` over a bound slice carried the same Gmail risk; the predicate is
  now evaluated in an AppleScript `repeat` loop with an early-exit on the
  date cutoff (slices are newest-first).
- **`bounded_scan.build_bounded_message_scan(..., whose_condition=...)`
  now raises `UNSAFE_WHOSE_ON_LIST`.** The footgun is gone — any future
  caller that needs to filter a bounded slice must use the new
  `build_bounded_filtered_scan(...)` helper, which emits the safe in-loop
  pattern by construction.

### Added

- **`list_inbox_emails(read_status=...)`** — new public parameter with the
  same vocabulary as `search_emails`: `"all"` (default), `"unread"`,
  `"read"`. The legacy `include_read: bool` / `unread_only: bool` kwargs
  continue to work but emit a `DeprecationWarning`.
- **`bounded_scan.build_bounded_filtered_scan(mailbox_var, scan_cap,
  target_max, condition_expr, ...)`** — new helper that emits the safe
  bounded-slice + in-loop filter pattern. The only sanctioned way to
  filter a bound slice by message property.

### Distribution

- **New `apple-mail.plugin` build artifact**: `tools/build-artifacts.sh` now
  emits `apple-mail.plugin` (byte-identical to `apple-mail-plugin.zip`)
  alongside the existing `.zip` and `.mcpb`. The `.plugin` extension is the
  canonical upload format for Claude Desktop's **Customize → Add plugin →
  Upload plugin** flow (Cowork), which was previously documented only as a
  generic `.zip` upload. Stale `.mcpb` files from 3.2.1 / 3.3.0 / 3.3.1
  cleaned from repo root; `.gitignore` covers the new `.plugin` artifact.

### Documentation

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
