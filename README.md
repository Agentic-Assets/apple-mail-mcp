# Apple Mail MCP Server

<!-- mcp-name: io.github.agentic-assets/apple-mail -->

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI](https://img.shields.io/pypi/v/mcp-apple-mail)](https://pypi.org/project/mcp-apple-mail/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io)
[![GitHub stars](https://img.shields.io/github/stars/Agentic-Assets/apple-mail-mcp?style=social)](https://github.com/Agentic-Assets/apple-mail-mcp/stargazers)

## Star History

<a href="https://star-history.com/#Agentic-Assets/apple-mail-mcp&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Agentic-Assets/apple-mail-mcp&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Agentic-Assets/apple-mail-mcp&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Agentic-Assets/apple-mail-mcp&type=Date" />
 </picture>
</a>

An MCP server that gives AI assistants full access to Apple Mail -- read, search, compose, organize, and analyze emails via natural language. Built with [FastMCP](https://github.com/jlowin/fastmcp) (`fastmcp>=3.1.0,<4`). **31 tools**, Python **3.10+**.

## Documentation map

| Doc | Purpose |
|-----|---------|
| [`CLAUDE.md`](CLAUDE.md) | Root navigation hub for agents |
| [`docs/CLAUDE-conventions.md`](docs/CLAUDE-conventions.md) | Tool performance rules, read-only, skills, plugin-dev |
| [`docs/AGENT_LIVE_TESTING.md`](docs/AGENT_LIVE_TESTING.md) | Live Mail verification via `apple-mail` CLI |
| [`plugin/docs/CLAUDE.md`](plugin/docs/CLAUDE.md) | Plugin wrapper & `start_mcp.sh` |
| [`plugin/apple_mail_mcp/CLAUDE.md`](plugin/apple_mail_mcp/CLAUDE.md) | Package entry, `core.py`, CLI |
| [`plugin/apple_mail_mcp/tools/CLAUDE.md`](plugin/apple_mail_mcp/tools/CLAUDE.md) | MCP tool modules |
| [`plugin/skills/CLAUDE.md`](plugin/skills/CLAUDE.md) | Skill authoring |
| [`tests/CLAUDE.md`](tests/CLAUDE.md) | Test layout & AppleScript mocks |
| [`tools/CLAUDE.md`](tools/CLAUDE.md) | Manifest validation scripts |
| [`docs/CLAUDE.md`](docs/CLAUDE.md) | Docs folder index + plugin skill map |
| [`tasks/CLAUDE.md`](tasks/CLAUDE.md) | Phase plans & backlog |
| [`apple-mail-mcpb/CLAUDE.md`](apple-mail-mcpb/CLAUDE.md) | Desktop bundle build |
| [`.claude-plugin/CLAUDE.md`](.claude-plugin/CLAUDE.md) | Claude Code marketplace manifest |
| [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json) | Codex Desktop/CLI marketplace manifest |

## Quick Install

**Prerequisites:** macOS with Apple Mail configured, Python 3.10+

### Claude Code Plugin (Recommended)

One install — MCP server (31 tools) and **nine** bundled workflow skills under `plugin/skills/` (see table below). Workflow entry points are skills-only; the old `/email-management` slash command was retired to avoid duplicate skill/command exposure.

```bash
claude plugin marketplace add Agentic-Assets/apple-mail-mcp
claude plugin install apple-mail@apple-mail-mcp
```

Then restart Claude Code.

### Codex Desktop / CLI Plugin

Codex uses the repo marketplace at `.agents/plugins/marketplace.json`, which points at the shared `plugin/` runtime and `plugin/.codex-plugin/plugin.json`.

```bash
codex plugin marketplace add Agentic-Assets/apple-mail-mcp
codex plugin add apple-mail@apple-mail-mcp
```

For a local checkout:

```bash
cd /path/to/apple-mail-mcp
codex plugin marketplace add .
codex plugin add apple-mail@apple-mail-mcp
```

MCP-only fallback, still draft-safe:

```bash
codex mcp add apple-mail -- /bin/bash /path/to/apple-mail-mcp/plugin/start_mcp.sh --draft-safe
```

If `mcp__apple-mail__*` tools are absent after plugin install, treat that as an MCP registration failure. Do not create reply drafts with generic AppleScript, Mail UI scripting, shell `osascript`, or standalone compose fallbacks. Fix registration first, or use the MCP-only absolute-path fallback above, restart Codex, and confirm the Apple Mail tools are present before drafting.

How to know it worked: `codex plugin list` showing `installed, enabled` is not enough. The pass condition is that the active Codex session exposes `mcp__apple-mail__*` tools and an MCP `list_tools` handshake includes `reply_to_email`. Maintainers can run `bash tools/validate-codex-plugin.sh` to check that install plus runtime path in a temporary `CODEX_HOME`.

Restart Codex Desktop or start a fresh Codex CLI session after installing.

### Refresh another Mac / second computer

Use this when another computer has an older Apple Mail plugin install, stale
marketplace cache, or you want to prove both Codex and Claude Code are using the
same current checkout.

1. Get the current code:

```bash
cd ~/Documents/GitHub/agentic-assets/apple-mail-mcp
git switch main && git pull --ff-only
```

2. Refresh Codex from the local checkout:

```bash
codex plugin remove apple-mail@apple-mail-mcp || true
codex plugin marketplace remove apple-mail-mcp || true
codex plugin marketplace add ./
codex plugin add apple-mail@apple-mail-mcp
codex mcp get apple-mail --json
```

The Codex MCP registration should show:

```json
{
  "command": "/bin/bash",
  "args": ["./start_mcp.sh", "--draft-safe"]
}
```

`codex plugin list` showing `installed, enabled` is not enough. For a runtime
smoke, run:

```bash
python3 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/python tools/mcp_tool_smoke.py \
  --command /bin/bash \
  --arg ./start_mcp.sh \
  --arg=--draft-safe \
  --cwd "$PWD/plugin" \
  --expect-count 31 \
  --required-tool reply_to_email \
  --required-tool compose_email \
  --required-tool manage_drafts \
  --required-tool list_accounts \
  --required-tool get_inbox_overview
```

3. Refresh Claude Code from the local checkout:

```bash
claude plugin uninstall apple-mail@apple-mail-mcp --scope user --keep-data -y || true
claude plugin marketplace remove apple-mail-mcp || true
claude plugin marketplace add ./ --scope user
claude plugin install apple-mail@apple-mail-mcp --scope user
claude plugin details apple-mail@apple-mail-mcp
```

Prefer `--scope user` for personal machine setup. Project-scope marketplace
entries can write an absolute local path into `.claude/settings.json`, which is
usually not what you want to commit.

`claude plugin details apple-mail@apple-mail-mcp` should report version `3.7.1`
and `MCP servers (1) apple-mail`. To smoke the installed Claude cache directly,
replace the path below if the details output shows a different install path:

```bash
.venv/bin/python tools/mcp_tool_smoke.py \
  --command /bin/bash \
  --arg "$HOME/.claude/plugins/cache/apple-mail-mcp/apple-mail/3.7.1/start_mcp.sh" \
  --arg=--draft-safe \
  --cwd "$HOME/.claude/plugins/cache/apple-mail-mcp/apple-mail/3.7.1" \
  --expect-count 31 \
  --required-tool reply_to_email \
  --required-tool compose_email \
  --required-tool manage_drafts \
  --required-tool list_accounts \
  --required-tool get_inbox_overview
```

4. Restart clients:

After either refresh, restart Codex Desktop / start a fresh Codex CLI session and
restart Claude Code so they load the refreshed plugin process.

### Claude Desktop Cowork (plugin marketplace)

Cowork uses Anthropic's **remote marketplace backend** (`remoteMarketplaceClient`), which currently rejects most third-party GitHub marketplaces with a generic **"Failed to add marketplace"** even when the repo is valid. This is a [known Cowork/Desktop bug](https://github.com/anthropics/claude-code/issues/41653), not a problem with this fork's manifest. Claude Code CLI install (above) works; Cowork's GitHub sync often does not.

**Workaround — upload the `.plugin` file directly (recommended for Cowork):**

1. Build the artifacts: `bash tools/build-artifacts.sh` — produces `apple-mail.plugin`, `apple-mail-plugin.zip`, and `apple-mail-mcp-v{VERSION}.mcpb` at the repo root.
2. Cowork → **Customize** → **Add plugin** → **Upload plugin**.
3. Select `apple-mail.plugin` and enable **Apple Mail**.

`apple-mail.plugin` and `apple-mail-plugin.zip` are byte-identical — both work, the `.plugin` extension is the canonical Cowork upload format.

```bash
cd /path/to/apple-mail-mcp
bash tools/build-artifacts.sh   # produces apple-mail.plugin, apple-mail-plugin.zip, and .mcpb
```

If you must build the zip by hand, zip from **inside** `plugin/` so `.claude-plugin/plugin.json` sits at the zip root — Cowork rejects uploads where it is nested under `plugin/`:

```bash
cd /path/to/apple-mail-mcp/plugin
zip -rq -X -D ../apple-mail-plugin.zip . \
  -x 'venv/*' '*/__pycache__/*' '*.pyc' '*.DS_Store' 'CLAUDE.md' '*/CLAUDE.md'
```

**Important:** Apple Mail MCP requires **macOS Mail.app** on the host Mac (`start_mcp.sh` → AppleScript). Cowork's Linux VM cannot run Mail directly; the plugin MCP server must execute on your Mac host. If tools fail after upload, use the **Claude Code CLI** install or the **Desktop `.mcpb`** path below instead.

GitHub marketplace URL (when Cowork sync works): `Agentic-Assets/apple-mail-mcp`

### Other Install Methods

<details>
<summary><strong>Repo CLI + MCP runtime</strong></summary>

This fork includes a maintained `apple-mail` CLI that wraps the same Python
tool code as the MCP server. It is meant for humans, shell scripts, smoke
tests, and agents on another Mac.

```bash
git clone https://github.com/Agentic-Assets/apple-mail-mcp.git
cd apple-mail-mcp
python3 -m venv .venv
.venv/bin/pip install -e .

.venv/bin/apple-mail accounts --json
.venv/bin/apple-mail search --account "Gmail" --query "invoice" --limit 10 --json
.venv/bin/apple-mail show --account "Gmail" --id 12345 --json
.venv/bin/apple-mail draft --account "Gmail" --to person@example.com --subject "Draft" --body "Draft body" --signature-name "TU"
.venv/bin/apple-mail quick-check --account "Gmail" --json
.venv/bin/apple-mail perf-test --account "Gmail" --json
.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --account "Gmail" --json
.venv/bin/apple-mail smoke-test --account "Gmail" --json
```

See [`docs/AGENT_LIVE_TESTING.md`](docs/AGENT_LIVE_TESTING.md) for batteries, permissions, and when to use each command.

Generate draft-safe Claude/OpenClaw MCP config from the same checkout:

```bash
.venv/bin/apple-mail mcp-config --repo "$(pwd)"
```

</details>

<details>
<summary><strong>uvx (zero install, MCP server only)</strong></summary>

```bash
claude mcp add apple-mail -- uvx mcp-apple-mail
```

Or for Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "uvx",
      "args": ["mcp-apple-mail"]
    }
  }
}
```

</details>

<details>
<summary><strong>pip install (MCP server only)</strong></summary>

```bash
pip install mcp-apple-mail
claude mcp add apple-mail -- mcp-apple-mail
```

</details>

<details>
<summary><strong>Claude Desktop MCPB / DXT bundle</strong></summary>

For Claude Desktop chat (outside of Cowork mode):

1. Download `apple-mail-mcp-v{VERSION}.mcpb` from [Releases](https://github.com/Agentic-Assets/apple-mail-mcp/releases) or build locally with `bash tools/build-artifacts.sh`.
2. In Claude Desktop, open Settings → **Extensions** (or **Developer → MCP Servers → Install from file** depending on app version), pick **Add Custom Plugin / Install from file**, and select the `.mcpb`.
3. Grant the Automation + Mail Data Access prompts macOS surfaces on first run.
4. Restart Claude Desktop so the extension registers across chat and Cowork sessions. Cowork projects may need to enable the extension explicitly in the project's plugin settings.

The bundle bootstraps a per-install Python venv via `start_mcp.sh` on first run, so `python3` must be on PATH for the Claude Desktop process. For pure Cowork use, prefer the `.plugin` upload above (same MCP server, no Developer mode required).

</details>

<details>
<summary><strong>Manual setup</strong></summary>

```bash
git clone https://github.com/Agentic-Assets/apple-mail-mcp.git
cd apple-mail-mcp/plugin
python3 -m venv venv
venv/bin/pip install -r requirements.txt

claude mcp add apple-mail -- /bin/bash $(pwd)/start_mcp.sh
```

</details>

## Tools (31)

### Reading & Search
| Tool | Description |
|------|-------------|
| `get_inbox_overview` | Dashboard with unread counts, folders, and recent emails |
| `list_inbox_emails` | List emails (defaults to 50 most recent). Async parallel per-account dispatch |
| `get_mailbox_unread_counts` | Unread counts per mailbox or per-account summary |
| `list_accounts` | List all configured Mail accounts |
| `list_account_addresses` | List sender aliases configured for a Mail account |
| `search_emails` | Unified search — subject, sender, body, dates, attachments. Defaults to last 48h and the default account |
| `get_email_by_id` | Fetch one exact email by the Apple Mail message id returned from search results |
| `get_email_by_ids` | Fetch multiple exact emails by reviewed Apple Mail message ids, chunked internally |
| `get_email_thread` | Conversation thread view across Inbox + Sent; prefer `message_id` from search/list results |

### Organization
| Tool | Description |
|------|-------------|
| `list_mailboxes` | Folder hierarchy with optional message counts |
| `create_mailbox` | Create new mailboxes (supports nested paths) |
| `move_email` | Move by `message_ids` (preferred) or filters with `allow_filter_scan=True`. Default max 50 |
| `update_email_status` | Mark read/unread, flag/unflag by `message_ids` (preferred) or filters with `allow_filter_scan=True`. Default max 10 |
| `manage_trash` | Soft delete, permanent delete, empty trash; prefer `message_ids`, filters need `allow_filter_scan=True`. Default max 5 |
| `synchronize_account` | Explicitly confirmed Mail.app sync for an account (can fetch large backlogs) |

### Composition
| Tool | Description |
|------|-------------|
| `compose_email` | Create a new standalone draft by default; refuses reply-like subjects/bodies unless `standalone_confirmed=True`; does not include original thread context |
| `reply_to_email` | Native Mail reply or reply-all draft. Default `native_format=True` composes in Mail's reply window (keeps the rich quote bar + logo signature) and types `reply_body` above the quote — needs window focus + Accessibility permission, else returns `REPLY_WINDOW_FOCUS_FAILED`; `native_format=False` is the windowless object-model fallback (plain-text quote). Verifies exact Drafts id first with bounded fallback; returns verification status, verified draft id, attachment status, and signature status for draft/open modes |
| `forward_email` | Forward with optional message, CC/BCC; prefer `message_id` from search/list results |
| `manage_drafts` | Create, list, send, open, and delete drafts; list returns Drafts ids, and send/open/delete prefer exact `draft_id` over subject matching; standalone create refuses reply-like drafts unless `standalone_confirmed=True` (`send` blocked in `--read-only` and `--draft-safe`) |
| `verify_draft` | Verify one exact Drafts message id; returns JSON snapshot for recipients, body sentinel, attachments, signature state, quoted-original status, and thread headers |
| `verify_drafts` | Verify multiple exact Drafts message ids and merge the per-draft JSON snapshots |
| `create_rich_email_draft` | Build a standalone multipart HTML `.eml` draft and save it to Drafts by default; refuses reply-like drafts unless `standalone_confirmed=True` |

### Attachments
| Tool | Description |
|------|-------------|
| `list_email_attachments` | List attachments by `message_ids` (preferred) or subject keyword (capped at 50 by default) |
| `save_email_attachment` | Save attachments to disk (validates target path) |

### Smart Inbox
| Tool | Description |
|------|-------------|
| `get_awaiting_reply` | Sent emails that haven't received a reply (default last 7 days) |
| `get_needs_response` | Unread emails likely needing a response (filters out newsletters/automated); JSON rows include numeric `message_id` for actions and `internet_message_id` for replied-header correlation |
| `get_top_senders` | Most frequent senders by count or domain over a date window |

### Analytics & Export
| Tool | Description |
|------|-------------|
| `get_statistics` | Account overview, sender stats, or mailbox breakdown; short windows scan 10 mailboxes × 75 messages, longer windows 20 × 250 |
| `export_emails` | Export exact `message_ids`, single emails by `message_id`, or full mailboxes to TXT/HTML (default cap 1000) |
| `inbox_dashboard` | Interactive UI dashboard (requires `mcp-ui-server`) |
| `full_inbox_export` | Audited full-inbox walk; only tool that scans every message. Slow (minutes on 24K mailboxes). Named in `UNBOUNDED_SCAN_REQUIRED` remediation as the legitimate fallback. |

## Configuration

### Read-Only Mode

Pass `--read-only` to disable tools that send email (`compose_email`, `reply_to_email`, `forward_email`). Draft management remains available (list, create, delete) but sending a draft via `manage_drafts` is blocked.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/apple_mail_mcp.py", "--read-only"]
    }
  }
}
```

### Draft-Safe Mode

Pass `--draft-safe` to keep read, search, draft, and open-for-review workflows available while blocking actual sends. This is the recommended mode for shared agent workspaces.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/plugin/start_mcp.sh",
      "args": ["--draft-safe"]
    }
  }
}
```

In draft-safe mode:

- `compose_email`, `reply_to_email`, and `forward_email` default to `mode="draft"` (quiet save to Drafts, no leftover compose windows); native replies assign `reply_body` above the quoted original, and saved replies/forwards verify exact Drafts id first when Mail exposes it before returning verification metadata
- they apply `DEFAULT_MAIL_SIGNATURE` by default when set; pass `include_signature=False` or CLI `--no-signature` to suppress it. For replies, disabling signatures cannot skip `reply_body` insertion
- use `mode="open"` only when you want each draft saved and left open in Mail for review (bulk reply UIs)
- reply drafting requires `reply_to_email(message_id=...)`; standalone draft creators (`compose_email`, `create_rich_email_draft`, `manage_drafts(action="create")`) block reply-like `Re:` / `Fwd:` drafts unless `standalone_confirmed=True`
- treat `subject_keyword` reply targeting or any degraded reply fallback as Cayman-approved-only for the specific message
- pass `message_id` from search/list tools for reply/forward when available; `subject_keyword` is fallback only
- explicit `mode="send"` calls return an error
- `manage_drafts action="send"` returns an error; when send is enabled outside draft-safe mode, target drafts by exact `draft_id` from `manage_drafts(action="list")`

### Default Mail Account

Set `DEFAULT_MAIL_ACCOUNT` to make most tools default to one account instead of scanning every configured Mail account. This is the single biggest perf win on multi-account setups. Tools still accept an explicit `account` parameter to override, and you can pass `all_accounts=True` to a tool that supports it for explicit cross-account scope.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/apple_mail_mcp.py"],
      "env": {
        "DEFAULT_MAIL_ACCOUNT": "Work"
      }
    }
  }
}
```

Use the exact account name as it appears in Apple Mail (e.g. `Gmail`, `Work`, `iCloud`). Leave unset to query all accounts by default.

### User Preferences (Optional)

Set `USER_EMAIL_PREFERENCES` to give the assistant context about your workflow. The string is injected into every preference-aware tool's docstring so the model sees it as part of the tool description.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/venv/bin/python3",
      "args": ["/path/to/apple_mail_mcp.py"],
      "env": {
        "DEFAULT_MAIL_ACCOUNT": "Work",
        "USER_EMAIL_PREFERENCES": "Prefer Archive folder over Trash, show max 25 emails, default to last week for triage"
      }
    }
  }
}
```

For `.mcpb` installs, configure both under Claude Desktop → **Developer > MCP Servers > Apple Mail MCP** (the bundle exposes them via `user_config`).

### Default Mail Signature

Set `DEFAULT_MAIL_SIGNATURE` to the exact Apple Mail signature name you want applied to new compose, reply, and forward drafts. Per-call `signature_name` overrides the default; `include_signature=False` disables it for one call. The CLI exposes this as `apple-mail draft --signature-name "TU"` and `--no-signature`.

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/path/to/plugin/start_mcp.sh",
      "args": ["--draft-safe"],
      "env": {
        "DEFAULT_MAIL_ACCOUNT": "Work",
        "DEFAULT_MAIL_SIGNATURE": "TU"
      }
    }
  }
}
```

### Performance Defaults

To stay fast on large mailboxes (24K+ messages), the server applies conservative defaults you can opt out of per-call:

| Default | Tools | Override |
|---------|-------|----------|
| Last 48 hours | `search_emails`, `get_awaiting_reply`, `get_needs_response`, `get_top_senders` | Pass `recent_days=N` (e.g. `7` for a week); routine tools reject unbounded scans |
| 50 emails max | `list_inbox_emails`, `list_email_attachments` | Pass `max_emails` / `max_results` |
| Single account | All scoped tools when `DEFAULT_MAIL_ACCOUNT` is set | Pass `account=<name>` or `all_accounts=True` |
| Per-call timeout | All long-running tools | Pass `timeout=<seconds>` |
| Unbounded scans refused | All routine scan/search tools (`recent_days=0` / `max_emails=0`) | Returns structured error `code: UNBOUNDED_SCAN_REQUIRED`; `full_inbox_export` is a separate audited export tool, not a normal search fallback |
| **ID-first mutations** | `move_email`, `update_email_status`, `manage_trash` | Pass `message_ids=[...]` from `search_emails`, `list_inbox_emails`, or `get_needs_response(output_format="json")` (fast, preferred). Filter-based bulk moves/updates/trash require `allow_filter_scan=True` or return `code: FILTER_SCAN_DISABLED`. |
| **Gated filter scans** | `move_email`, `update_email_status`, `manage_trash` (filter path only) | `allow_filter_scan=True` (slow; timeout-prone on 24k+ inboxes). Filter paths still default to a 48h `recent_days` window. |
| **Body scan gate** | `search_emails` | `body_text` requires `allow_body_scan=True` or returns `code: BODY_SCAN_DISABLED`. Prefer subject/sender/date filters; pair body scans with a tight date window. |

**Recommended mutation flow:** search, list, or `get_needs_response(output_format="json")` → collect numeric `message_id` values → call `move_email`, `update_email_status`, or `manage_trash` with `message_ids`. Use `dry_run=True` with ids for a fast preview without acting.

When a per-account call fails in a multi-account fan-out, you get partial results plus an `errors` field naming the account. JSON responses also include `error_details` when the tool can distinguish a timeout from another Mail/App permission error.

### Safety Limits (destructive ops)

Batch operations cap by default to prevent accidental bulk actions. Override via the per-tool parameter when needed.

| Operation | Default cap | Param |
|-----------|-------------|-------|
| `move_email` | 50 | `max_moves` |
| `update_email_status` | 10 | `max_updates` |
| `manage_trash` | 5 | `max_deletes` |
| `export_emails` | 1000 | `max_emails` |

**Dry-run defaults:** `manage_trash` defaults to `dry_run=True` (safe preview — explicit override needed to act, especially for `action="delete_permanent"`). `move_email` and `update_email_status` default to `dry_run=False` (live) because their effects are reversible; pass `dry_run=True` to preview matches first.

## Usage Examples

```
Show me an overview of my inbox
Search for emails about "project update" in my Gmail
Find the recent "Domain name" message, show me its message_id, then draft a reply by id
Search for recent invoice messages, show me the candidate ids, then move the reviewed message_ids to Archive
Show me email statistics for the last 30 days
Draft replies to unread messages with mode=open for review, or create a rich HTML weekly-update draft
```

## CLI

Install from a repo checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Common commands:

```bash
apple-mail accounts --json
apple-mail addresses --json
apple-mail inbox --account "Gmail" --limit 10 --json
apple-mail search --account "Gmail" --query "invoice" --limit 10 --json
apple-mail show --account "Gmail" --id 12345 --json
apple-mail mailboxes --account "Gmail" --json
apple-mail mailboxes --account "Gmail" --counts --json   # slower; explicit counts opt-in
apple-mail draft --account "Gmail" --to person@example.com --subject "Draft" --body "Draft body" --signature-name "TU"
apple-mail mcp-config --repo "$(pwd)"
apple-mail quick-check --account "Gmail" --json
apple-mail perf-test --account "Gmail" --json
apple-mail perf-test --include-analysis --allow-heavy-mail-scan --account "Gmail" --json
apple-mail smoke-test --account "Gmail" --json
```

Live verification guide: [`docs/AGENT_LIVE_TESTING.md`](docs/AGENT_LIVE_TESTING.md).

Use `perf-test --include-analysis --allow-heavy-mail-scan` only when you explicitly want the heavy analysis gate (`needs-response`, `awaiting-reply`, `top-senders`, `statistics`). Routine validation should use `quick-check`, `smoke-test`, or `perf-test` without analysis.

The CLI keeps write operations draft-first. It intentionally does not expose
send/delete shortcuts; use the MCP tools with `--draft-safe` for shared agents.

### Rich HTML Drafts

Use `create_rich_email_draft` when you need a visually formatted email, newsletter, or leadership update.

- It generates an unsent `.eml` file with multipart plain-text + HTML bodies
- It saves the opened Mail compose object to Drafts by default, then closes the fresh window
- It can leave the saved draft open for explicit human review (`review_in_mail=True`)
- It can write only the `.eml` artifact with `open_in_mail=False`
- Blank subjects stay `.eml`-only until there is a subject to save safely through Mail
- It accepts partial details, so you can start with just an account and subject and fill in the rest later

This is more reliable than injecting raw HTML into AppleScript `content`, which Mail often stores as literal markup.

## Claude Code Skills

Workflow skills ship with the Claude Code and Codex plugin installs and load automatically on install (see [`plugin/skills/CLAUDE.md`](plugin/skills/CLAUDE.md) for routing):

| Skill | Purpose |
|-------|---------|
| [`apple-mail-operator`](plugin/skills/apple-mail-operator/) | MCP + Mail setup, accounts/mailboxes, safe navigation, performance |
| [`inbox-triage`](plugin/skills/inbox-triage/) | 5–10 min read-first scan (needs-response, awaiting-reply) |
| [`email-management`](plugin/skills/email-management/) | Sustained Inbox Zero habits and cross-cutting programs |
| [`mailbox-taxonomy`](plugin/skills/mailbox-taxonomy/) | Folder strategy, noise diagnosis, structural `create_mailbox` |
| [`email-archive-cleanup`](plugin/skills/email-archive-cleanup/) | Staged archive / bulk move / trash with dry runs + exports |
| [`mail-rules-advisor`](plugin/skills/mail-rules-advisor/) | Mail filter / rule proposals (manual apply in Mail.app) |
| [`email-drafting`](plugin/skills/email-drafting/) | Compose, reply, forward, rich drafts (`--draft-safe` aware) |
| [`email-style-profile`](plugin/skills/email-style-profile/) | Learn voice from Sent mail + preferences for drafting |
| [`email-attachments`](plugin/skills/email-attachments/) | List and save attachments with path safety |

For standalone MCP installs, copy the needed skill directories manually (example loop):

```bash
for d in apple-mail-operator inbox-triage email-management mailbox-taxonomy \
         email-archive-cleanup mail-rules-advisor email-drafting \
         email-style-profile email-attachments; do
  cp -r "plugin/skills/$d" "$HOME/.claude/skills/$d"
done
```

The plugin MCP server starts with **`--draft-safe`** by default for both Claude Code (`plugin/.claude-plugin/plugin.json`) and Codex (`plugin/.mcp.json`).

## Requirements

- macOS with Apple Mail configured
- Python 3.10+
- `fastmcp>=3.1.0,<4` and `mcp-ui-server==1.0.0` for the MCP Apps dashboard
- Claude Desktop, Codex Desktop/CLI, or any MCP-compatible client
- Mail.app permissions: Automation + Mail Data Access (grant in **System Settings > Privacy & Security > Automation**)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Mail.app not responding | Ensure Mail.app is running; check Automation permissions in System Settings |
| Slow searches on a large account | Set `DEFAULT_MAIL_ACCOUNT` to the account you actually work in. Pair `account=` with `recent_days=` (default 48h) for tight scopes. Pass `include_content=False` if you don't need bodies |
| One account fails across a fan-out | Returned JSON includes an `errors` array naming the account plus `error_details` when available. The other accounts' results are still returned. Bump `timeout=` for timeout entries; fix Mail permissions or account config for non-timeout entries |
| Mailbox not found | Use exact folder names; nested folders use `/` separator (e.g., `Projects/Alpha`) |
| Permission errors | Grant access in **System Settings > Privacy & Security > Automation** |
| Rich draft shows raw HTML | Use `create_rich_email_draft` instead of pasting HTML into `manage_drafts` or AppleScript `content` |
| Save / Don't Save when closing drafts | Use default `mode="draft"` or `mode="open"` (saves first). Avoid leaving unsaved compose windows from bulk agent runs |

## Project Structure

```
apple-mail-mcp/
├── .agents/
│   └── plugins/
│       └── marketplace.json   # Codex Desktop/CLI marketplace entry
├── .claude-plugin/
│   └── marketplace.json       # Claude Code marketplace manifest
├── plugin/                    # Shared Claude Code + Codex plugin runtime
│   ├── .codex-plugin/
│   │   └── plugin.json        # Codex plugin manifest
│   ├── .claude-plugin/
│   │   └── plugin.json        # Claude Code plugin manifest
│   ├── .mcp.json              # Codex MCP config
│   ├── skills/                # bundled workflow skills (see plugin/skills/CLAUDE.md)
│   ├── apple_mail_mcp/        # Python MCP server package (31 tools)
│   ├── apple_mail_mcp.py      # Entry point
│   ├── start_mcp.sh           # Startup wrapper (auto-creates venv)
│   └── requirements.txt
├── apple-mail-mcpb/           # MCPB build files (Claude Desktop)
├── LICENSE
└── README.md
```

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit and push
4. Open a Pull Request

## License

MIT -- see [LICENSE](LICENSE).

## Links

- [Releases](https://github.com/Agentic-Assets/apple-mail-mcp/releases)
- [Issues](https://github.com/Agentic-Assets/apple-mail-mcp/issues)
- [Discussions](https://github.com/Agentic-Assets/apple-mail-mcp/discussions)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [Model Context Protocol](https://modelcontextprotocol.io)
