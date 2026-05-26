# CLAUDE conventions — deep reference

This file holds the durable engineering rules extracted from the repo root `CLAUDE.md`. Folder-level `CLAUDE.md` files link here instead of duplicating these sections.

**Related:** root [`CLAUDE.md`](../CLAUDE.md) (layout, commands, architecture overview) · [`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) (install surface) · [`tests/CLAUDE.md`](../tests/CLAUDE.md) (mock patterns)

---

## Tool-implementation conventions (locked in 3.1.5)

The anti-patterns below caused real production timeouts on a 24K-message Exchange inbox. Every new tool that touches Mail.app must follow these rules. Templates: `search.py`, `inbox.py`, `smart_inbox.py`, `manage.py`, `analytics.py`, `compose.py`.

### ScanWindow capability token (v3.2.0)

[`bounded_inbox_scan()`](../plugin/apple_mail_mcp/bounded_scan.py) is the **sole legitimate issuer** of `ScanWindow` capability tokens. Tools must never construct `ScanWindow` directly — call `bounded_inbox_scan()` or one of the safe builders (`build_bounded_message_scan`, `build_whose_id_list`). `AppleScriptBackend._check_window` rejects forged or out-of-policy windows with structured error `code: INVALID_SCAN_WINDOW`. This is what enforces the unbounded-scan refusal (`code: UNBOUNDED_SCAN_REQUIRED`) and the `full_inbox_export` audit boundary at the backend layer, not just inside tool wrappers. Contract suite: `test_bounded_scan_contract`, `test_no_unbounded_whose`, `test_full_inbox_export`.

### Performance defaults

- **Recent-window default**: any tool that searches or lists takes `recent_days: float = 2.0` (48h). Tools must refuse unbounded scans (`recent_days=0` / `max_emails=0`) with `code: UNBOUNDED_SCAN_REQUIRED` plus a `remediation.fallback_tool` field. The only tool that walks the entire inbox is `full_inbox_export` (slow; documented cost). Routine tests and skills must pass bounded `recent_days` / `max_emails`.
- **AppleScript-side caps, not Python-side slicing.** Avoid broad `every message of mailbox whose …` scans on remote mailboxes; Mail may materialize/fetch before filtering. Prefer direct newest-first slices (`messages 1 thru N of mailbox`) and filter inside the bounded loop.
- **`ignoring case … end ignoring`** for case-insensitive comparisons. Never call out to `do shell script "echo … | tr '[:upper:]' '[:lower:]'"` per message — the deprecated `LOWERCASE_HANDLER` was removed in 3.1.5 for that exact reason.
- **Push date filters unconditionally** into the `whose` clause when the caller provides `date_from`/`date_to`. Don't gate them on the presence of other filters.

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
| `draft` (default) | Save to Drafts quietly; do not leave fresh compose windows open | Bulk drafting, background agent work, default under `--draft-safe` |
| `open` | Save first, then leave the compose window open for human review | User wants each draft to pop up in Mail (e.g. review 10 replies in sequence) |
| `send` | Send immediately | Explicit user authorization only; blocked when `DRAFT_SAFE` or `READ_ONLY` |

**Reply/forward targeting:** pass `message_id` from `search_emails`, `list_inbox_emails`, or `get_email_by_id` whenever available. `subject_keyword` is a fallback when no id is known — never prefer subject matching when an id is already in context. Do not use standalone draft creators (`compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")`) to answer existing mail: they create standalone messages with no quoted original thread. These paths refuse reply-like `Re:` / `Fwd:` subjects or quoted-thread bodies unless the caller explicitly passes `standalone_confirmed=True`.

**Rich `.eml` drafts:** `create_rich_email_draft` saves the front Mail compose window after opening the file (no subject-based outgoing-message lookup). Use `review_in_mail=True` for saved-open review; blank subjects stay `.eml`-only until a nonblank subject exists.

**Agent guidance:** skills under `plugin/skills/email-drafting/` and `plugin/skills/apple-mail-operator/` document the quiet-default vs saved-open review split. Sync `apple-mail-mcpb/manifest.json` tool descriptions when compose behavior changes.

---

## Versioning

Version is duplicated across **five** files — bump all together when releasing. Top-level marketplace `metadata.version` (1.0.0) describes the marketplace manifest itself; don't touch it. See [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md).

| File | Field |
|------|-------|
| `pyproject.toml` | `[project].version` |
| `plugin/.claude-plugin/plugin.json` | `version` |
| `.claude-plugin/marketplace.json` | `plugins[0].version` |
| `server.json` | `version` and `packages[0].version` |
| `apple-mail-mcpb/manifest.json` | `version` |

Tool-count claims drift. Description fields in `plugin.json`, `marketplace.json`, and `apple-mail-mcpb/manifest.json` must match `grep -c "^@mcp.tool" plugin/apple_mail_mcp/tools/*.py`. The mcpb manifest also embeds the full `tools[]` array — both count and names must match code. Run [`tools/validate_manifests.py`](../tools/validate_manifests.py) or `plugin-dev:plugin-validator` after add/remove; run `bash tools/dev-check.sh release` before shipping manifest, package, or artifact changes.

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

New entry points ship as skills only. `plugin/commands/email-management.md` stays (legacy `/email-management`); all companion workflows ship as skills only:

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
