# CLAUDE conventions — deep reference

This file holds the durable engineering rules extracted from the repo root `CLAUDE.md`. Folder-level `CLAUDE.md` files link here instead of duplicating these sections.

**Related:** root [`CLAUDE.md`](../CLAUDE.md) (layout, commands, architecture overview) · [`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) (install surface) · [`tests/CLAUDE.md`](../tests/CLAUDE.md) (mock patterns)

---

## Tool-implementation conventions (locked in 3.1.5)

The anti-patterns below caused real production timeouts on a 24K-message Exchange inbox. Every new tool that touches Mail.app must follow these rules. Templates: `search.py`, `inbox.py`, `smart_inbox.py`, `manage.py`, `analytics.py`, `compose.py`.

### ScanWindow capability token (v3.2.0)

[`bounded_inbox_scan()`](../plugin/apple_mail_mcp/bounded_scan.py) is the **sole legitimate issuer** of `ScanWindow` capability tokens. Tools must never construct `ScanWindow` directly — call `bounded_inbox_scan()` or one of the safe builders (`build_bounded_message_scan`, `build_whose_id_list`). `AppleScriptBackend._check_window` rejects forged or out-of-policy windows with structured error `code: INVALID_SCAN_WINDOW`. This is what enforces the unbounded-scan refusal (`code: UNBOUNDED_SCAN_REQUIRED`) and the `full_inbox_export` audit boundary at the backend layer, not just inside tool wrappers. Contract suite: `test_bounded_scan_contract`, `test_no_unbounded_whose`, `test_full_inbox_export`.

### ID-first mutations and scan opt-in gates (v3.7.0)

Destructive and bulk mutation tools default to **exact `message_ids`** from a prior `list_inbox_emails` or `search_emails` call. Subject and sender substring target selectors are deprecated on action tools and return `code: TARGET_SELECTOR_DEPRECATED`. Date-only or explicit bulk paths remain off by default and require an explicit escape hatch.

| Gate | Tools | Default | Opt-in kwarg | Structured error when blocked |
|------|-------|---------|--------------|-------------------------------|
| Filter scan | `move_email`, `update_email_status`, `manage_trash` | `message_ids` preferred; date/bulk filter path disabled | `allow_filter_scan=True` | `code: FILTER_SCAN_DISABLED` |
| Body scan | `search_emails` | `body_text` ignored unless opted in | `allow_body_scan=True` | `code: BODY_SCAN_DISABLED` |
| Deprecated target selector | `reply_to_email`, `forward_email`, `move_email`, `update_email_status`, `manage_trash`, `list_email_attachments`, `save_email_attachment`, `export_emails(scope="single_email")`, `manage_drafts(send/open/delete)` | Exact ids required | None | `code: TARGET_SELECTOR_DEPRECATED` |

**`FILTER_SCAN_DISABLED` contract** (`manage.py` → `_filter_scan_disabled_error`):

- Raised when a mutation tool is called with date-only or explicit bulk filter kwargs but **without** `message_ids` and **without** `allow_filter_scan=True`.
- `remediation.preferred`: collect ids via `search_emails` / `list_inbox_emails`, then call the mutation with `message_ids=[...]`.
- `remediation.escape_hatch`: `allow_filter_scan=True` (slow; timeout-prone on 24k+ inboxes; approved bulk/date campaigns only).
- When the escape hatch is used, responses are prefixed with `FILTER_SCAN_WARNING` so agents see the slow-path notice in plain text.

**`TARGET_SELECTOR_DEPRECATED` contract** (`backend/base.py` -> `target_selector_deprecated_error`):

- Raised before AppleScript runs when an action tool is called with `subject_keyword`, `subject_keywords`, `sender`, or `draft_subject` instead of exact ids.
- `remediation.discovery`: the read/search/list tool to call first.
- `remediation.exact_selector`: the id parameter required by the action tool.
- Keep these legacy kwargs in v3.x schemas for compatibility, but do not route them into target lookup.

**`BODY_SCAN_DISABLED` contract** (`search.py` → `_body_scan_disabled_error`):

- Raised when `search_emails` is called with `body_text` set but `allow_body_scan=False` (the default).
- Body scans are O(N × message-size) on large mailboxes; pair `allow_body_scan=True` with a tight `date_from` / `recent_days` window.
- `remediation.preferred`: narrow with `subject_keyword`, `sender`, `date_from`, or `has_attachments` instead.

**ID path rules** (shared across `move_email`, `update_email_status`, `manage_trash`):

- When `message_ids` is provided, keyword/sender/date filters are **ignored** (fast `build_whose_id_list` path).
- Empty or all-non-numeric `message_ids` → plain-text validation error before AppleScript runs.
- Lists longer than `MAX_WHOSE_IDS` (50) → `code: WHOSE_ID_LIST_TOO_LARGE`; chunk with `bounded_scan.iter_id_chunks`.
- Filter paths still honor `recent_days` defaults and refuse unbounded scans with `UNBOUNDED_SCAN_REQUIRED` when no date window is set.

Agent workflow: **search/list -> collect `message_id` -> mutate by ids**. Prefer `sender_exact="person@example.com"`, `sender_domain="example.com"`, or `internet_message_id="<id@example.com>"` over fuzzy `sender="..."` when the exact address, domain, or Message-ID is known. Reserve `allow_filter_scan=True` and `allow_body_scan=True` for rare, operator-approved bulk/date or full-text campaigns.

### Centralized scan caps (`SCAN_BOUNDS`, v3.7.1)

All bounded AppleScript slices read caps from [`constants.py`](../plugin/apple_mail_mcp/constants.py) `SCAN_BOUNDS`. Edit one dict to retune every tool; `bounded_scan.compute_scan_upper_bound()` uses `SEARCH_BASE_CAP`, `SEARCH_WINDOW_CAP`, and `SEARCH_DAYS_SCALE`.

| Key | Value | Used by |
|-----|-------|---------|
| `SEARCH_BASE_CAP` | 100 | `search_emails` floor via `compute_scan_upper_bound` |
| `SEARCH_WINDOW_CAP` | 250 | `search_emails` ceiling; `get_statistics` long windows (20 mailboxes) |
| `SEARCH_DAYS_SCALE` | 25 | Per-day scaling in `compute_scan_upper_bound` |
| `BODY_SEARCH_AUTO_CAP` | 75 | `search_emails` body scans without explicit `date_from` |
| `INBOX_DEFAULT_CAP` / `INBOX_MAX_CAP` | 100 / 500 | `list_inbox_emails` unread/read filter slice |
| `INBOX_SHORT` / `INBOX_LONG` | 25 / 75 | `smart_inbox` per-mailbox ceilings |
| `TRASH_SCAN` | 100 | Trash listing branches |
| `DRAFT_LOOKUP` / `MESSAGE_LOOKUP` | 75 | Compose draft/reply lookup tails |
| `MAX_MAILBOXES_PER_SEARCH` | 20 | Multi-mailbox `search_emails` fan-out |
| `MAX_MAILBOXES_PER_SEARCH_ALL` | 10 | `search_emails(mailbox="All")` cap |

`get_statistics`: `days_back <= 7` → 10 mailboxes × `INBOX_LONG` (75); else 20 × `SEARCH_WINDOW_CAP` (250).

### Performance defaults

- **Recent-window default**: any tool that searches or lists takes `recent_days: float = 2.0` (48h). Tools must refuse unbounded scans (`recent_days=0` / `max_emails=0`) with `code: UNBOUNDED_SCAN_REQUIRED` plus a `remediation.fallback_tool` field. The only tool that walks the entire inbox is `full_inbox_export` (slow; documented cost). Routine tests and skills must pass bounded `recent_days` / `max_emails`.
- **AppleScript-side caps, not Python-side slicing.** Avoid broad `every message of mailbox whose …` scans on remote mailboxes; Mail may materialize/fetch before filtering. Prefer direct newest-first slices (`messages 1 thru N of mailbox`) and filter inside the bounded loop.
- **`ignoring case … end ignoring`** for case-insensitive comparisons. Never call out to `do shell script "echo … | tr '[:upper:]' '[:lower:]'"` per message — the deprecated `LOWERCASE_HANDLER` was removed in 3.1.5 for that exact reason.
- **Push date filters unconditionally** into the `whose` clause when the caller provides `date_from`/`date_to`. Don't gate them on the presence of other filters.

### Forbidden AppleScript patterns (lint-enforced)

The patterns below are catalogued failure modes from real production crashes. **Each is enforced by `tests/test_no_unbounded_whose.py` — adding one of them to tool source breaks CI.** Use the named safe alternative.

| Forbidden | Why it fails | Use instead |
|-----------|--------------|-------------|
| `<sliceVar> whose <predicate>` where `<sliceVar>` is `candidateMessages` / `mailboxMessages` / `inboxMessages` / `draftMessages` / etc. — i.e. a variable bound via `messages 1 thru N of MB` followed by a `whose` clause. | AppleScript's `whose` over a list re-resolves the predicate against each ref's underlying physical folder. On Gmail that folder is `[Gmail]/All Mail`; Mail rejects the call with `Can't get {message id N of mailbox "[Gmail]/All Mail" ...} whose ...`. This is the 2026-05-27 Gmail crash. | `bounded_scan.build_bounded_filtered_scan(mailbox_var, scan_cap, target_max, condition_expr)` — emits a bounded slice plus an in-AppleScript `repeat ... if` loop by construction. Predicates of the form `<prop> of aMessage` work safely here. |
| `every message of MB whose <non-id-predicate>` (subject contains, sender contains, date received, read status, …) without a downstream slice. | Mail materializes the entire remote mailbox to evaluate the predicate. Hangs/times out on 24K-message Exchange inboxes and large Gmail folders. | Bind a bounded newest-first slice via `build_bounded_message_scan(mailbox_var, limit)`, then filter per-message in a `repeat with aMessage in candidateMessages` loop. For ID-only lookups use `build_whose_id_list(ids)`. |
| `every message of MB` with no `whose` (raw enumeration). | Same materialization cost as above, with no filter to limit work. | `messages 1 thru N of MB`. |
| `build_bounded_message_scan(..., whose_condition=...)`. | The helper raises `ToolError(code="UNSAFE_WHOSE_ON_LIST")` to prevent the slice-then-whose bug at construction time. | `build_bounded_filtered_scan(...)`. |
| `build_whose_id_list(ids)` with `len(ids) > MAX_WHOSE_IDS` (50). | Mail's AppleScript parser rejects or hangs on `id is X or id is Y or ...` predicates beyond ~200–500 OR-terms (varies by macOS); the helper raises `ToolError(code="WHOSE_ID_LIST_TOO_LARGE")` to prevent the crash. | `iter_id_chunks(ids)` plus a Python loop, one `osascript` call per chunk. |
| Building a pipe-delimited row (`messageSubject & "&#124;&#124;&#124;" & messageSender & ...`) without first running `sanitize_pipe_delimited_field` on each user-controlled field. | A subject legitimately containing the pipe trio shifts every parser field right; the corrupted `message_id` slot can then be passed to `manage_trash(action="delete_permanent")` and **delete the wrong message** — silent data loss. | `core.sanitize_pipe_delimited_field("messageSubject")` (and `"messageSender"`) before the row emit. The Python-side parser additionally validates `message_id.isdigit()` as a belt-and-suspenders backstop. |
| `do shell script "echo X \| tr '[:upper:]' '[:lower:]'"` per message. | Hundreds of subprocess spawns per scan; killed the 3.1.4 search path. | `ignoring case … end ignoring` AppleScript blocks. |
| Tool kwarg `allow_full_scan`. | Retired in v3.2.0 in favor of structured `UNBOUNDED_SCAN_REQUIRED` errors with `remediation.fallback_tool = "full_inbox_export"`. | Refuse with a structured error and point at `full_inbox_export`. |

The lint test `tests/test_no_unbounded_whose.py` enforces the first four rules via source regex (with an empty `KNOWN_DANGEROUS_WHOSE` allowlist — add to it only with a tracking note and a follow-up PR planned). The builder-output contract `tests/test_bounded_scan_contract.py` asserts that the safe helpers emit the in-loop pattern, not the unsafe one. The Gmail-crash regression suite `tests/test_gmail_unread_crash_regression.py` simulates Mail's rejection to confirm the fix end-to-end.

### Account scoping

- **`DEFAULT_MAIL_ACCOUNT`**: every tool that takes an `account` parameter must (a) default it to `Optional[str] = None`, (b) at the top fall back to `_server.DEFAULT_MAIL_ACCOUNT` if `account is None`, (c) return a structured error if neither is set. Exception: `synchronize_account` requires `confirm_sync=True` and additionally requires `all_accounts=True` for all-account sync.
- **`all_accounts: bool = False`** is the explicit override for tools that need every configured account even when `DEFAULT_MAIL_ACCOUNT` is set.

### Async + per-account isolation

- Tools that fan out across accounts should be `async def` and dispatch each account via `asyncio.to_thread(run_applescript, …)` + `asyncio.gather(..., return_exceptions=True)`. Wall time ≈ slowest single account, not sum.
- Pair with per-account `AppleScriptTimeout` catch; append failing accounts to an `errors: list[str]` field and include structured error details when a tool can distinguish timeout from another Mail/App failure. Partial results > total failure.
- Single-account tools (`compose_email`, `move_email`, `manage_drafts`, `get_top_senders`, etc.) stay sync.

### Timeout exposure

- Every modernized tool takes `timeout: Optional[int] = None` and threads it into `run_applescript(..., timeout=timeout)`. Wrap in `try/except core.AppleScriptTimeout` and return a structured error naming the account and elapsed budget.

### Escaping

- User-supplied strings reaching AppleScript **always** go through `core.escape_applescript()`. Missing it is script-injection and syntax-corruption regardless of string source.

### What NOT to do

- Don't add `subprocess.run(["osascript", …])` calls that bypass `run_applescript()`. Compose paths were migrated in 3.1.6; don't add new bypasses.
- Don't write `except: pass` or `except Exception: pass` — collect errors into a list the caller can see.
- Don't materialize a full mailbox into a Python list before filtering. `every message of …` without a `whose` cap is the bug.

### Orphan watcher

`__main__._start_orphan_watcher` works around [python-sdk#526](https://github.com/modelcontextprotocol/python-sdk/issues/526): when the MCP client exits without closing stdin, the server keeps polling Mail.app and silently relaunches Mail after the user quits it. The watcher captures the initial PPID and self-terminates with `os._exit(0)` when reparented. `get_ppid` and `exit_fn` are injectable for `tests/test_orphan_watcher.py` — keep those seams.

### Read-only enforcement

`--read-only` removes send tools from the registry; it does **not** branch inside tool implementations. `manage_drafts` stays registered but blocks the "send" action internally. New email-sending capabilities: extend `SEND_TOOLS` in `plugin/apple_mail_mcp/server.py`.

### Rich HTML drafts

`create_rich_email_draft` generates a multipart `.eml` on disk and saves it through Mail.app by default, rather than injecting HTML into AppleScript's `content` property (Mail stores literal markup). Prefer this for anything HTML. Use explicit review mode only when the operator wants Mail left open; saved defaults should not leave fresh compose windows behind.

### Compose and draft modes

`compose_email`, `reply_to_email`, and `forward_email` share a `mode` parameter:

| Mode | Behavior | When agents should use it |
|------|----------|---------------------------|
| `draft` (default) | Save to Drafts quietly; do not leave fresh compose windows open. Native replies use Mail's dictionary-backed `reply` command, assign `reply_body` above the quoted original without clipboard/UI scripting, and verify exact Drafts id first with bounded fallback. | Bulk drafting, background agent work, default under `--draft-safe` |
| `open` | Save first, then leave the compose window open for human review | User wants each draft to pop up in Mail (e.g. review 10 replies in sequence) |
| `send` | Send immediately | Explicit user authorization only; blocked when `DRAFT_SAFE` or `READ_ONLY` |

**Reply/forward targeting:** pass `message_id` from `search_emails`, `list_inbox_emails`, or `get_email_by_id`. `subject_keyword` is schema-compatible only and returns `TARGET_SELECTOR_DEPRECATED`; run discovery first. `reply_to_email` uses Mail's native reply command, constructs and assigns `reply_body` above the quoted-original block, and saved replies/forwards verify exact Drafts id first when Mail exposes one. `body_html` is ignored on replies for compatibility. Do not use standalone draft creators (`compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")`) to answer existing mail: they create standalone messages with no quoted original thread. These paths refuse reply-like `Re:` / `Fwd:` subjects or quoted-thread bodies unless the caller explicitly passes `standalone_confirmed=True`.

**Thread discovery:** pass `message_id` and explicit `mailboxes=[...]` to `get_email_thread` whenever a prior list/search result exposed the id. The message-id path reads Mail's dictionary-backed Message-ID, In-Reply-To, and References headers first, then uses subject fallback only when headers are unavailable or no header-linked messages are found. Use `output_format="json"` to collect exact message ids and header metadata, and `include_preview=False` when the workflow only needs handles. Check `selection_strategy` and `subject_fallback_used` before treating a reconstructed thread as header-confirmed.

**Rich `.eml` drafts:** `create_rich_email_draft` saves the opened Mail compose object after opening the file (no subject-based outgoing-message lookup and no `System Events` save keystroke). Use `review_in_mail=True` for saved-open review; blank subjects stay `.eml`-only until a nonblank subject exists.

**Draft lifecycle targeting:** `manage_drafts(action="list")` returns each draft's id. For `send`, `open`, or `delete`, pass `draft_id`; `draft_subject` is schema-compatible only and returns `TARGET_SELECTOR_DEPRECATED`.

**Attachment targeting:** pass `message_ids` to `list_email_attachments`; `subject_keyword` is schema-compatible only and returns `TARGET_SELECTOR_DEPRECATED`. Use `output_format="json"` to get per-row `message_id`, `attachment_index`, filename, and size. Prefer `save_email_attachment(message_ids=[one_id], attachment_index=N, ...)` for exact saves. `attachment_name` remains compatible, but duplicate filename matches return `AMBIGUOUS_ATTACHMENT_SELECTOR` and instruct callers to retry with `attachment_index`.

**Agent guidance:** skills under `plugin/skills/email-drafting/` and `plugin/skills/apple-mail-operator/` document the quiet-default vs saved-open review split. Sync `apple-mail-mcpb/manifest.json` tool descriptions when compose behavior changes.

---

## Versioning

Version is duplicated across **six** files — bump all together when releasing. Top-level Claude marketplace `metadata.version` (1.0.0) describes the marketplace manifest itself; don't touch it. The Codex marketplace at `.agents/plugins/marketplace.json` does not carry a release version; it points at `./plugin`. See [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md).

| File | Field |
|------|-------|
| `pyproject.toml` | `[project].version` |
| `plugin/.claude-plugin/plugin.json` | `version` |
| `plugin/.codex-plugin/plugin.json` | `version` |
| `.claude-plugin/marketplace.json` | `plugins[0].version` |
| `server.json` | `version` and `packages[0].version` |
| `apple-mail-mcpb/manifest.json` | `version` |

Tool-count claims drift. Description fields in Claude/Codex `plugin.json`, marketplace manifests, and `apple-mail-mcpb/manifest.json` must match `grep -c "^@mcp.tool" plugin/apple_mail_mcp/tools/*.py`. The mcpb manifest also embeds the full `tools[]` array — both count and names must match code. Run [`tools/validate_manifests.py`](../tools/validate_manifests.py) or `plugin-dev:plugin-validator` after add/remove; run `bash tools/dev-check.sh release` before shipping manifest, package, or artifact changes.

---

## Distribution channels — four install surfaces, one source

The repo ships from **one source tree** to **four install surfaces**. Claude Desktop artifacts rebuild in one shot via [`tools/build-artifacts.sh`](../tools/build-artifacts.sh); Claude Code and Codex plugin installs share the checked-in `plugin/` runtime. The validator and CI tests enforce parity between them.

| Artifact | Target | How users install |
|----------|--------|-------------------|
| `apple-mail-plugin.zip` | Claude Code plugin marketplace | `claude plugin install apple-mail@apple-mail-mcp` (uses `.claude-plugin/marketplace.json`) |
| `apple-mail.plugin` | Claude Desktop **Cowork** | Customize → Add plugin → **Upload plugin**. The Cowork UI accepts the `.plugin` extension; without it the upload silently fails. |
| `apple-mail-mcp-v{VERSION}.mcpb` | Claude Desktop **chat extension** | "Add Custom Plugin" / "Install from file" (DXT bundle built with `mcpb pack`) |
| `.agents/plugins/marketplace.json` + `plugin/.codex-plugin/plugin.json` | Codex Desktop/CLI plugin marketplace | `codex plugin marketplace add Agentic-Assets/apple-mail-mcp` then `codex plugin add apple-mail@apple-mail-mcp`; local checkouts can use `codex plugin marketplace add .` |

**`.zip` and `.plugin` must be byte-identical** — `tools/build-artifacts.sh` copies the canonical zip to the `.plugin` name so they cannot drift. `tools/validate_manifests.py::_check_plugin_file_parity` rejects any divergence and `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1` promotes a missing `.plugin` to a hard error. Regression coverage: `tests/test_validate_manifests.py::test_plugin_file_parity_*`.

**Never** ship a release where any required artifact or manifest is missing or stale. Real installer failures we have hit and now guard against:

- MCPB built with raw `zip -r .` emitting zero-byte directory entries → Claude Desktop installer aborts with `ENOENT`. Build with `mcpb pack` or `zip -X -D`. Guard: `_check_no_directory_entries`.
- Plugin zip wrapping files under `plugin/` prefix → Cowork rejects with "No manifest found". Build from inside `plugin/`. Guard: `test_plugin_zip_has_manifest_at_root_not_nested`.
- `.plugin` extension missing → Cowork "Upload plugin" rejects the `.zip` silently. Guard: `_check_plugin_file_parity`.

---

## Marketplace vs plugin.json — component ownership

Claude Code rejects the install with *"conflicting manifests: both plugin.json and marketplace entry specify components"* when both `.claude-plugin/marketplace.json plugins[0]` and `plugin/.claude-plugin/plugin.json` declare any of `commands`, `agents`, `skills`, `hooks`, `mcpServers` while `strict` is not `true` on the marketplace entry.

Rule for this repo: **all component declarations live in `plugin/.claude-plugin/plugin.json`** (today: only `mcpServers`). The marketplace entry is metadata-only (`name`, `displayName`, `description`, `version`, `author`, `source`, `keywords`, `strict: false`). Skills auto-discover from `plugin/skills/<name>/SKILL.md` — do not re-list them in marketplace.json. If a future change truly needs marketplace-side components, set `"strict": true` in the same edit.

The guard lives in `tools/validate_manifests.py::_check_marketplace_contract`; regression tests `test_marketplace_contract_rejects_dual_component_declarations` / `..._allows_dual_components_when_strict_true` lock it in. Also see [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) § "Components live in plugin.json".

---

## Plugin-dev agents

This repo **is** a Claude Code plugin. For plugin shell, MCP wiring, skills, agents, commands, hooks, or manifests, defer to `plugin-dev:*` agents — they override memory about plugin authoring:

| Agent / skill | When |
|---------------|------|
| **`plugin-dev:plugin-validator`** | After any change to `plugin.json`, `marketplace.json`, `.mcp.json`, command/skill/agent frontmatter, or directory layout. Blocking before merge. |
| **`plugin-dev:skill-reviewer`** | After creating or editing any skill under `plugin/skills/`. Focus on `description` / frontmatter — that drives triggering. |
| **`plugin-dev:agent-creator`** | Adding a new agent. Don't hand-author frontmatter from memory. |
| **`plugin-dev:*` skills** | Invoke the matching skill *before* designing (`mcp-integration`, `skill-development`, `command-development`, etc.). |

Server-side AppleScript/FastMCP work is plain Python — use general agents, not plugin-dev.

---

## Skill authoring convention

Every skill under `plugin/skills/` follows the same shape so siblings trigger crisply without competing:

- **Directory name == frontmatter `name`.** `email-management/` ↔ `name: email-management`. No `-expert` suffix.
- **`description`**: third-person, scenario-rich, ends with "Do NOT use for X (see \<sibling\>)". Include 4–6 quoted trigger phrases and name 3–5 central MCP tools.
- **Body**: imperative/infinitive ("Start with `get_inbox_overview()`"). Addresses the executing model, not a human reader.
- **`SKILL.md`**: 1,500–2,000 words. Detail → `references/`, code → `examples/`, scripts → `scripts/`. Link in "Additional Resources".
- **Top of body**: (1) purpose, (2) when-to-use / when-NOT-to-use, (3) performance defaults, (4) sibling decision tree, (5) red-flag table for destructive ops.
- **No persona openers** ("You are an expert…").
- **Verify** with `plugin-dev:skill-reviewer` before merge. Template: `plugin/skills/email-management/SKILL.md`.

### Skills only — no new slash commands

Entry points ship as skills only. Do not restore `plugin/commands/`; the old `/email-management` slash command was retired because hosts can surface commands beside skills and confuse routing. Release validation fails if the legacy commands directory reappears.

| Skill directory | Primary intent |
|-----------------|----------------|
| `apple-mail-operator` | MCP bootstrap, navigation, troubleshooting |
| `inbox-triage` | Fast read-first daily scan |
| `email-management` | Umbrella Inbox Zero / sustained habits |
| `mailbox-taxonomy` | Folder design + noise diagnosis |
| `email-archive-cleanup` | Staged moves, exports, capped trash |
| `mail-rules-advisor` | Filter/rule prose only (no MCP rule API) |
| `email-drafting` | Compose / reply / forward / rich drafts |
| `email-style-profile` | Voice contract before drafting |
| `email-attachments` | List + save attachments |

**Routing cheat sheet:** [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md). **Narrow skills** may stay shorter than the umbrella template if they include triggers, sibling matrix, performance notes, and destructive red lines. **Umbrella template:** `plugin/skills/email-management/SKILL.md` (also has `references/`, `examples/`, `templates/`).

After adding or editing any skill: run **`plugin-dev:skill-reviewer`**. After manifest, package, artifact, or skill-count marketing copy changes: **`plugin-dev:plugin-validator`** + `bash tools/dev-check.sh release`.

---

## Platform constraints

- **macOS only.** Tests mock `subprocess.run` — see `tests/test_modernization_3_1_5.py` and `tests/test_mail_search_tools.py` (patch with `side_effect` capturing script via `kwargs["input"]`).
- **Python 3.10+** per `pyproject.toml`. `start_mcp.sh` gates 3.10+ (prefers 3.12+); mcpb embedded README must stay in sync.
- **Permissions**: Mail.app must be configured; Automation + Mail Data Access granted to the terminal/IDE. Surface clear errors; don't retry blindly.
- **Async**: `asyncio.to_thread` for `run_applescript` in worker threads. Don't make `run_applescript` itself async.
