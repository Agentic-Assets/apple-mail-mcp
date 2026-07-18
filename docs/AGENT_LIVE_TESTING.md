# Agent Live Testing (Apple Mail MCP)

Use the repo-owned CLI (`.venv/bin/apple-mail`) to verify changes against real Mail.app immediately after edits. This bypasses the slow generated mcporter wrapper and calls the same Python tool functions as the MCP server.

## Setup

```bash
cd /path/to/apple-mail-mcp
python3 -m venv .venv
.venv/bin/pip install -e . pytest
```

Optional but recommended for faster iteration:

```bash
export DEFAULT_MAIL_ACCOUNT="Your Mail Account Name"
```

When set, `perf-test`, `quick-check`, and `smoke-test` use this account instead of the first configured account.

## Permissions (macOS)

Mail.app must be configured and the terminal (or IDE) running the CLI needs:

- **Automation** — allow control of Mail
- **Mail Data Access** — allow reading mail data

If a command hangs or returns permission errors, open **System Settings → Privacy & Security** and grant access to Terminal, iTerm, or Cursor.

## Safe commands (read-only / dry-run)

### Test profiles

| Profile | Account | Use |
|---------|---------|-----|
| **light** | `ai.openclaw` (~9 mailboxes) | Fast regression after edits |
| **production** | `Cayman - Agentic Assets` (`cayman@agenticassets.ai`) | Realistic large-mailbox perf gate before merge |

Set the account once:

```bash
export DEFAULT_MAIL_ACCOUNT="Cayman - Agentic Assets"   # production gate
# export DEFAULT_MAIL_ACCOUNT="ai.openclaw"             # light smoke
```

### Batteries

| Command | What it exercises |
|---------|-------------------|
| `quick-check` | metadata + no-hit search + inbox (~30s target) |
| `perf-test --quick` | same as `quick-check` |
| `perf-test` | full battery: dry-run move/trash, overview, bad-account fast-fail, dashboard metadata |
| `perf-test --include-analysis --allow-heavy-mail-scan` | heavy opt-in battery + needs-response, awaiting-reply, top-senders, statistics |
| `perf-test --profile production` | production overview threshold (15s); metadata scales with mailbox count |
| `smoke-test` | accounts, inbox, no-hit search, invalid-account error, draft-safe send block |

Add `--verbose-sensitive` to `perf-test` / `quick-check` to include account names in perf samples (default output redacts them).

`--include-analysis` is intentionally blocked unless paired with `--allow-heavy-mail-scan`. Those probes are bounded in code, but they still touch enough Mail.app message headers that a large account may fetch remote state. Routine agent testing should use `quick-check`, `smoke-test`, or individual probes with small limits.

### Individual safe probes

```bash
.venv/bin/apple-mail accounts --json
.venv/bin/apple-mail addresses --json
.venv/bin/apple-mail mailboxes --account "$DEFAULT_MAIL_ACCOUNT" --json
.venv/bin/apple-mail unread --account "$DEFAULT_MAIL_ACCOUNT" --summary --json
.venv/bin/apple-mail inbox --account "$DEFAULT_MAIL_ACCOUNT" --limit 2 --json
.venv/bin/apple-mail search --account "$DEFAULT_MAIL_ACCOUNT" --query NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231 --json
.venv/bin/apple-mail show --account "$DEFAULT_MAIL_ACCOUNT" --id 12345 --no-content --json
.venv/bin/apple-mail overview --account "$DEFAULT_MAIL_ACCOUNT" --format compact --no-mailboxes --no-recent
.venv/bin/apple-mail needs-response --account "$DEFAULT_MAIL_ACCOUNT" --days 2
.venv/bin/apple-mail awaiting-reply --account "$DEFAULT_MAIL_ACCOUNT" --days 7
.venv/bin/apple-mail top-senders --account "$DEFAULT_MAIL_ACCOUNT" --days 30
.venv/bin/apple-mail statistics --account "$DEFAULT_MAIL_ACCOUNT" --scope account_overview --days 2
.venv/bin/apple-mail move-dry-run --account "$DEFAULT_MAIL_ACCOUNT" --to Archive --subject NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231
.venv/bin/apple-mail trash-dry-run --account "$DEFAULT_MAIL_ACCOUNT" --subject NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231
.venv/bin/apple-mail drafts list --account "$DEFAULT_MAIL_ACCOUNT"
.venv/bin/apple-mail drafts list --account "$DEFAULT_MAIL_ACCOUNT" --hide-empty
.venv/bin/apple-mail drafts cleanup-empty --account "$DEFAULT_MAIL_ACCOUNT"   # dry-run preview; add --execute to delete
.venv/bin/apple-mail search --account "$DEFAULT_MAIL_ACCOUNT" --mailboxes "INBOX,Sent" --query NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231 --json
```

### Generated MCP wrapper probes

The generated wrapper (`apple-mail`, currently mcporter) is useful for parity
checks, but its flags are not identical to the repo CLI. Treat the repo CLI as
the canonical fast iteration surface, then spot-check wrapper commands agents
will actually invoke.

```bash
apple-mail --help
apple-mail -o json list-accounts
apple-mail -o json list-mailboxes --account "$DEFAULT_MAIL_ACCOUNT" --include-counts false
apple-mail -o json list-inbox-emails --account "$DEFAULT_MAIL_ACCOUNT" --max-emails 2 --output-format json
apple-mail -o json search-emails --account "$DEFAULT_MAIL_ACCOUNT" --subject-keyword NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231 --limit 1 --output-format json
apple-mail -o json get-inbox-overview --raw '{"account":"'"$DEFAULT_MAIL_ACCOUNT"'","output_format":"json","compact":true,"include_mailboxes":false,"include_recent":false,"include_suggestions":false}'
```

#### `--raw` examples for advanced wrapper options

mcporter embeds tool schemas at generation time; some tools expose only a
subset of parameters as named flags. Use `--raw <json>` to pass the full
parameter set verbatim. These are copy-paste ready — set
`DEFAULT_MAIL_ACCOUNT` first.

```bash
# Full inbox overview with all blocks suppressed → metadata-only JSON dict.
apple-mail -o json get-inbox-overview --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "output_format":"json",
  "compact":true,
  "include_mailboxes":false,
  "include_recent":false,
  "include_suggestions":false
}'

# Account statistics scoped to last 7 days; JSON dict with mailbox_totals.
apple-mail -o json get-statistics --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"account_overview",
  "days_back":7,
  "output_format":"json"
}'

# Sender-stats scope (requires sender filter).
apple-mail -o json get-statistics --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"sender_stats",
  "sender":"alerts@example.com",
  "days_back":30,
  "output_format":"json"
}'

# Mailbox breakdown using Mail.app count APIs (no per-message scan).
apple-mail -o json get-statistics --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"mailbox_breakdown",
  "mailbox":"INBOX",
  "days_back":30,
  "output_format":"json"
}'

# Triage: emails likely needing a response. Defaults already exclude rows
# with was_replied_to=true or has_draft=true, and report skipped_replied_count
# / skipped_drafted_count; check_already_replied adds the legacy Sent-header
# scan as an extra verification layer.
apple-mail -o json get-needs-response --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "days_back":7,
  "max_results":20,
  "output_format":"json"
}'

# Same window, widened to see already-replied and already-drafted rows again.
apple-mail -o json get-needs-response --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "days_back":7,
  "max_results":20,
  "include_already_replied":true,
  "include_drafted":true,
  "output_format":"json"
}'

# Sent messages still awaiting a reply (header-based match).
apple-mail -o json get-awaiting-reply --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "days_back":7,
  "exclude_noreply":true,
  "max_results":20,
  "output_format":"json"
}'

# Top senders grouped by domain.
apple-mail -o json get-top-senders --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "mailbox":"INBOX",
  "days_back":30,
  "top_n":10,
  "group_by_domain":true,
  "output_format":"json"
}'

# Inbox dashboard JSON (UI-free metadata; safe for headless agents).
apple-mail -o json inbox-dashboard --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "include_preview":false,
  "max_total":20,
  "max_per_account":10,
  "output_format":"json"
}'

# full_inbox_export is disabled: expect an immediate structured
# UNBOUNDED_EXPORT_DISABLED refusal (no AppleScript runs; max_emails/batch_size
# are accepted for schema compatibility but ignored). Useful for confirming the
# refusal contract, not for exporting anything. For a real metadata/export
# pass, page with export_emails(scope="entire_mailbox", max_emails<=50, offset=N)
# or list_inbox_emails(max_emails<=50) instead.
apple-mail -o json full-inbox-export --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "mailbox":"INBOX",
  "fields":["subject","sender","date_received","read_status","message_id"],
  "max_emails":500,
  "batch_size":250,
  "output_format":"json"
}'

# Correspondent history export, including received and Sent-side messages.
apple-mail -o json export-emails --raw '{
  "account":"'"$DEFAULT_MAIL_ACCOUNT"'",
  "scope":"correspondent",
  "email_address":"person@example.com",
  "include_sent":true,
  "date_from":"2026-07-01",
  "max_emails":10,
  "format":"txt"
}'
```

Known wrapper checks to keep separate from manifest validation:

- `apple-mail --help` must expose critical read commands, especially `get-email-by-id`.
- Some wrapper commands only expose `--raw <json>` for advanced options.
- Repo CLI flags like `--output-format` may not exist on every wrapper command; use the wrapper help as the source of truth.
- `list_inbox_emails`, `get_statistics`, `get_inbox_overview`, `inbox_dashboard`, `get_needs_response`, `get_awaiting_reply`, and `get_top_senders` all return a Python `dict` for `output_format="json"` (not a JSON string). Through the generated wrapper the dict is rendered as JSON; through the MCP transport it crosses as a structured object.
- `reply_to_email(output_format="json")` is different from the read-only JSON tools: it is returned as a JSON string and is defined only for verified draft/open reply artifacts, not send mode.

**Wrapper command-surface check** (repo script; skips if no wrapper on PATH):

```bash
python3 tools/validators/check_wrapper_surface.py
```

**Regenerate wrapper** after adding MCP tools (mcporter embeds schemas at generation time):

```bash
APPLE_MAIL_CLI_HOME="${APPLE_MAIL_CLI_HOME:-$HOME/.local/share/apple-mail-cli}"
rsync -a --delete --exclude venv /path/to/apple-mail-mcp/plugin/ "$APPLE_MAIL_CLI_HOME/plugin/"
cd "$APPLE_MAIL_CLI_HOME"
npx mcporter@0.11.3 generate-cli --from ./apple-mail-cli.cjs --bundle apple-mail-cli.cjs
python3 /path/to/apple-mail-mcp/tools/probes/patch_mcporter_wrapper.py ./apple-mail-cli.cjs
./install.sh
python3 /path/to/apple-mail-mcp/tools/validators/check_wrapper_surface.py
```

`patch_mcporter_wrapper.py` is required with mcporter 0.11.3 because the
generated CLI otherwise reserves global `--timeout` for transport timeouts in
milliseconds. The patch renames the request flag to `--request-timeout-ms`, so
tool-level `--timeout` still reaches Apple Mail tools as seconds.

**Repo CLI vs wrapper naming:**

| Repo CLI | Generated wrapper |
|----------|-------------------|
| `show --id` | `get-email-by-id` |
| `inbox` | `list-inbox-emails` |
| `overview` | `get-inbox-overview` |
| `search` | `search-emails` |

## After each change

**Fast loop (~30–60s):**

```bash
.venv/bin/apple-mail quick-check --json
```

**Full performance gate:**

```bash
.venv/bin/apple-mail perf-test --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json
```

**Capture and compare perf baselines:**

Use this before and after hot-path edits. The comparator is pure JSON and does
not touch Mail.app; the only live work is the two explicit `perf-test` captures.

```bash
.venv/bin/apple-mail perf-test --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json > /tmp/apple-mail-baseline.json
.venv/bin/apple-mail perf-test --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json > /tmp/apple-mail-current.json
.venv/bin/python tools/probes/compare_perf_results.py /tmp/apple-mail-baseline.json /tmp/apple-mail-current.json --max-regression-pct 0 --json
```

For routine local iteration, use a small positive budget such as
`--max-regression-pct 5`. For v4 hot-tool work, treat any p95 or live-case
regression as a redesign signal unless the phase plan explicitly records why
the tradeoff is acceptable.

**Honest analysis gate (expect failures until Phase 2 speed work):**

```bash
.venv/bin/apple-mail perf-test --include-analysis --allow-heavy-mail-scan --account "$DEFAULT_MAIL_ACCOUNT" --profile production --json
```

Exit code is non-zero if any threshold is breached.

### Thresholds (full `perf-test`)

| Case | Threshold |
|------|-----------|
| metadata (accounts + addresses + mailboxes) | `2000 + max(0, mailbox_count - 20) × 35` ms |
| no-hit search | < 3s light / < 4.5s production |
| inbox (limit 2) | < 5s |
| dry-run move | < 5s |
| dry-run trash | < 5s |
| overview (compact, metadata-only) | < 10s light / < 15s production |
| bad_account (invalid name fast-fail) | < 2s |
| dashboard_metadata (unread + recent, no preview) | < 5s |

**With `--include-analysis --allow-heavy-mail-scan`:**

| Case | Threshold |
|------|-----------|
| needs-response (days=2) | < 8s |
| awaiting-reply (days=7) | < 5s |
| top-senders (days=30) | < 5s |
| statistics account_overview (days=2) | < 12s |

Output is redacted by default: counts and char lengths only; account names, subjects, senders, and bodies are omitted unless `--verbose-sensitive` is set.

## Unit tests vs live Mail

Local CI-equivalent gates run mocked pytest + manifest validation + **module line budget** (600 LOC warn, baseline regression fail):

```bash
bash tools/gates/validate_manifests.sh
python3 tools/validators/check_module_line_budget.py
.venv/bin/pytest tests/ -q -rw
```

Detail: [`CLAUDE-conventions.md`](CLAUDE-conventions.md) § Module line budget.

Required checked-in hooks (manifest drift + pytest; wrapper check when staged MCP tool files change):

```bash
bash tools/gates/install-git-hooks.sh   # every local or cloud checkout
test "$(git config --get core.hooksPath)" = ".githooks"
bash tools/gates/dev-check.sh             # manual equivalent
bash tools/gates/dev-check.sh surface     # always include wrapper check
```

Release packaging gate before commit/PR when `plugin/`, manifests, `pyproject.toml`, `requirements.txt`, zip, or MCPB surfaces changed:

```bash
bash tools/gates/dev-check.sh release
```

Live Mail verification is manual on macOS with Mail.app running.

## MCP config for agents

### MCP env vars

The Claude plugin starts the server via `mcpServers.apple-mail` → `${CLAUDE_PLUGIN_ROOT}/start_mcp.sh` (see `plugin/.claude-plugin/plugin.json`). Optional environment variables:

| Variable | Purpose |
|----------|---------|
| `DEFAULT_MAIL_ACCOUNT` | Exact Mail account name (e.g. `Work`, `Gmail`). When set, most tools default to this account instead of fanning out across every account — largest perf win on multi-account mailboxes. |
| `DEFAULT_MAIL_SIGNATURE` | Exact Apple Mail signature name to apply by default to compose, reply, and forward drafts (e.g. `TU`). |
| `USER_EMAIL_PREFERENCES` | Free-text workflow hints injected into preference-aware tool docstrings (e.g. "Prefer Archive over Trash, cap lists at 25"). |

Example `env` block for a manual MCP config (also emitted by `apple-mail mcp-config` if you add `env` yourself):

```json
"env": {
  "DEFAULT_MAIL_ACCOUNT": "Work",
  "DEFAULT_MAIL_SIGNATURE": "TU",
  "USER_EMAIL_PREFERENCES": "Prefer Archive over Trash; default triage window 7 days"
}
```

Full setup examples: [README — Default Mail Account & User Preferences](../README.md#default-mail-account).

Generate draft-safe MCP wiring from the repo checkout:

```bash
.venv/bin/apple-mail mcp-config --repo "$(pwd)"
```

This adds `--draft-safe` so send tools stay blocked during agent testing.

## Plugin workflow skills (agent UX)

The Claude Code plugin bundles **eleven** workflow skills under `plugin/skills/`. They complement live CLI testing: skills guide **tool selection and safety**; this doc guides **verification**.

| Agent task | Start with skill | Live CLI probes (examples) |
|------------|------------------|----------------------------|
| Daily “what needs reply?” | `inbox-triage` | `needs-response`, `awaiting-reply`, `overview --format compact` |
| Folder mess / taxonomy | `mailbox-taxonomy` | `mailboxes --json`, `top-senders`, `statistics --scope account_overview` |
| Bulk archive / cleanup | `email-archive-cleanup` | `move-dry-run`, `trash-dry-run`, `search` previews before writes |
| Draft / reply | `email-drafting` | `draft` (quiet default), `draft --open` (saved-open review); reply/forward should use `message_id` when known; send blocked in draft-safe |
| MCP misbehaving / slow | `apple-mail-operator` | `quick-check`, `accounts`, narrow `search` with `recent_days` |

Full skill map: [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md). User install copy: [`README`](../README.md) § Claude Code Skills.

When editing skills, run **`plugin-dev:skill-reviewer`**. When editing manifests, package/dependency files, release artifacts, or bundled skill marketing copy, run **`bash tools/gates/dev-check.sh release`** and **`plugin-dev:plugin-validator`**. Use **`bash tools/gates/validate_manifests.sh`** for quick inner-loop checks.
