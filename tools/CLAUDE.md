# tools/ — validation scripts

Dev-infra guardrails — not MCP tools (`plugin/apple_mail_mcp/tools/` is the server).

## validate_manifests

| Script | Role |
|--------|------|
| `validate_manifests.sh` | Bash entry; **CI calls this** |
| `validate_manifests.py` | Python equivalent; covered by `tests/test_validate_manifests.py` |

Enforces (source of truth: `pyproject.toml` `[project].version` and `[project].name`):

1. **Version sync** — Claude/Codex `plugin.json`, Claude marketplace `plugins[0].version`, `server.json` (×2), `apple-mail-mcpb/manifest.json`
2. **Tool count claims** — descriptions must match `rg "^@mcp\.tool" … | wc -l` (**29**)
3. **MCPB name parity** — `@mcp.tool` names ↔ `apple-mail-mcpb/manifest.json` `tools[]`
4. **Install contracts** — Claude plugin `mcpServers`, Codex `.mcp.json`, marketplace `source`/skills, MCPB `server` config, `server.json` package metadata, and PyPI package deps/packages must point at the shipped runtime
5. **Payload syntax** — `plugin/start_mcp.sh` and shipped Python files must parse before release
6. **Artifact freshness and exactness** — when `apple-mail-plugin.zip` or `apple-mail-mcp-v{version}.mcpb` exists locally, archive members must match current tracked payload bytes, with no unexpected stale files
7. **Archive structural integrity** — plugin zip and `.mcpb` must contain no duplicate members and no zero-byte directory entries (names ending in `/`); raw `zip -r .` produces entries that installers can reject. Build with `tools/build-artifacts.sh` / `mcpb pack` or `zip -D`.
8. **Release artifact presence** — opt in with `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1` to require **all three** local distributables before shipping: `apple-mail-plugin.zip`, `apple-mail.plugin`, and `apple-mail-mcp-v{version}.mcpb`
9. **Plugin/.plugin byte parity** — `apple-mail.plugin` must exist alongside `apple-mail-plugin.zip` and be byte-identical. The `.plugin` extension is the canonical Cowork "Add plugin → Upload plugin" artifact; drifting bytes break the Cowork upload silently. Always rebuild via `tools/build-artifacts.sh`, which copies the canonical zip to the `.plugin` name.
10. **Marketplace ↔ plugin.json component conflict** — fails if both `.claude-plugin/marketplace.json plugins[0]` and `plugin/.claude-plugin/plugin.json` declare any of `commands`, `agents`, `skills`, `hooks`, `mcpServers` while marketplace `strict` is not `true`. Mirrors the Claude Code "conflicting manifests" install error. See [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) § "Components live in plugin.json" for the rule and escape hatch.
11. **Codex plugin surface** — `.agents/plugins/marketplace.json` must point at `./plugin`; `plugin/.codex-plugin/plugin.json` must expose `skills: "./skills"` and `mcpServers: "./.mcp.json"`; `plugin/.mcp.json` must launch `/bin/bash ./start_mcp.sh --draft-safe` with `cwd: "."`.
12. **Stale distribution artifacts** — fails if repo root contains `apple-mail-mcp-v*.mcpb` files other than the current `pyproject.toml` version; run `tools/build-artifacts.sh` to prune and rebuild.

```bash
bash tools/validate_manifests.sh
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh
```

Codex runtime registration is checked separately:

```bash
bash tools/validate-codex-plugin.sh
```

That smoke installs the plugin into a temporary `CODEX_HOME`, reads `codex mcp get apple-mail --json`, launches the registered stdio server, and fails unless MCP `list_tools` includes `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview`.

Skips Claude marketplace `metadata.version` (1.0.0) and Codex marketplace release versioning because `.agents/plugins/marketplace.json` is install routing metadata — see [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md).

## check_wrapper_surface.py

| Script | Role |
|--------|------|
| `check_wrapper_surface.py` | Generated mcporter wrapper command-surface check; covered by `tests/test_wrapper_surface.py` |

Separate from **`validate_manifests`** — manifest validation checks Python `@mcp.tool` ↔ MCPB `tools[]` parity, but the generated `apple-mail` wrapper on PATH embeds schemas at generation time and can drift when new tools are added.

Verifies critical read commands (`get-email-by-id`, `search-emails`, `get-email-thread`, `list-inbox-emails`, `get-inbox-overview`) appear in `apple-mail --help`. Exit 0 when all present; exit 1 when missing. Skips gracefully (exit 0) if no wrapper on PATH.

```bash
python3 tools/check_wrapper_surface.py
python3 tools/check_wrapper_surface.py --wrapper /path/to/apple-mail
```

Run after regenerating the mcporter bundle or adding read tools agents rely on.

## measure_metadata_hydration.py

| Script | Role |
|--------|------|
| `measure_metadata_hydration.py` | Read-only exact-id timing helper for Phase 4a metadata-index feasibility |

Measures header-read and attachment-count hydration costs for exact Mail message ids. It is not an MCP tool and must not be run casually: it requires `--confirm-read-only-live-mail`, sends nothing, creates no drafts, and prints only aggregate timings/counts. It does not print message contents, headers, senders, subjects, recipient addresses, attachment names, or raw message ids.

```bash
python3 tools/measure_metadata_hydration.py \
  --account "$DEFAULT_MAIL_ACCOUNT" \
  --mailbox INBOX \
  --message-ids "12345,67890" \
  --repeats 3 \
  --confirm-read-only-live-mail
```

Use only with known dummy or approved exact ids. The output is suitable for deciding whether Phase 4b metadata-index hydration is worth implementing, but it is not a runtime cache and does not mutate Mail.

## dev-check.sh

Tiered local gate (no live Mail except `live` tier). Requires root `.venv/`.

| Tier | Runs |
|------|------|
| `default` | `validate_manifests.sh` + `pytest`; adds `check_wrapper_surface.py` when **staged** files touch `plugin/apple_mail_mcp/tools/`, tool registration, or MCPB `manifest.json` |
| `lint` | Fatal package quality gate: `ruff check plugin/apple_mail_mcp/`, `ruff format --check plugin/apple_mail_mcp/`, and `mypy --strict plugin/apple_mail_mcp/` |
| `surface` | default + wrapper check always |
| `manifest` | manifests only |
| `live` | default + `.venv/bin/apple-mail quick-check --json` |
| `release` | `lint` first, then `tools/build-artifacts.sh` (rebuilds `apple-mail-plugin.zip` + `apple-mail.plugin` + `.mcpb`, then runs `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS` validate and `mcpb unpack`/`validate` smoke), pytest, and wrapper. **Run before every commit that touches `plugin/`, manifests, `pyproject.toml`, `requirements.txt`, or release artifacts** — finalize-apple-mail-mcp skill enforces this. |
| `all` | default + wrapper check always |

```bash
bash tools/dev-check.sh
bash tools/dev-check.sh surface
bash tools/dev-check.sh release   # always before commit/PR
```

## pre-commit hook

Install once per clone:

```bash
bash tools/install-git-hooks.sh
```

Runs `bash tools/dev-check.sh default` on every commit (manifests + pytest; wrapper check when staged tool surface changes). Manual equivalent:

```bash
bash tools/pre-commit-validate.sh
```

## CI

`.github/workflows/ci.yml` (Ubuntu, Python 3.10): `validate_manifests.sh` then `pytest tests/ -q`. Same gate as pre-commit; live Mail is manual ([`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md)).

Run after tool add/remove, version bump, mcpb `tools[]` edit, or plugin skill marketing copy in manifests. Supplement with **`plugin-dev:plugin-validator`** when available; add **`plugin-dev:skill-reviewer`** when editing `plugin/skills/*/SKILL.md`.

## Related

[`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md) · [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) · [`.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json) · [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)
