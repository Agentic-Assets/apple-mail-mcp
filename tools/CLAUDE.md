# tools/ — validation scripts

Dev-infra guardrails — not MCP tools (`plugin/apple_mail_mcp/tools/` is the server).

## sync_skill_references

| Script | Role |
|--------|------|
| `sync_skill_references.py` | Copy canonical `plugin/skills/references/*.md` into each packaged skill's `references/` folder |

Packaged Claude/Codex skills only expose files inside each `plugin/skills/<name>/` directory. Shared refs are edited once under `plugin/skills/references/`, then synced:

```bash
python3 tools/sync_skill_references.py          # write copies
python3 tools/sync_skill_references.py --check  # CI / dev-check parity gate
```

Enforced by `tests/infra/test_packaged_skill_paths.py` (link escape + byte parity).

## validate_tasks_layout

| Script | Role |
|--------|------|
| `validate_tasks_layout.py` | Enforces `tasks/` bucket layout (`active/`, `reference/`, `archive/`); covered by `tests/infra/test_tasks_layout.py` |

Agents must read `tasks/CLAUDE.md` § Agent requirements before creating or moving planning artifacts. The gate fails when loose markdown or workstream folders appear at `tasks/` root, required buckets are missing, navigation files drop layout markers, or `todo.md` links to stale flat paths.

```bash
python3 tools/validate_tasks_layout.py
```

Runs in `bash tools/dev-check.sh` (default and release tiers).

## validate_manifests

| Script | Role |
|--------|------|
| `validate_manifests.sh` | Bash entry; **CI calls this** |
| `validate_manifests.py` | Python orchestrator (`main`); covered by `tests/infra/test_validate_manifests.py` |
| `manifest_checks/` | Check implementations grouped by concern (see below) |

The individual checks live in the sibling `manifest_checks/` package (`common.py` for the shared `ROOT`/constants/helpers, then `version.py`, `tool_count.py`, `install_contracts.py`, `codex.py`, `artifacts.py`, `module_budget.py`); `validate_manifests.py` imports and orchestrates them in `main` and re-exports them so the test suite keeps calling `validate_manifests.<check>`. `validate_manifests.ROOT` forwards to `manifest_checks.common.ROOT`, so monkeypatching it still redirects every check.

Enforces (source of truth: `pyproject.toml` `[project].version` and `[project].name`):

1. **Version sync** — Claude/Codex `plugin.json`, Claude marketplace `plugins[0].version`, `server.json` (×2), `apple-mail-mcpb/manifest.json`
2. **Tool count claims**: descriptions must match `rg "^@mcp\.tool" ... | wc -l` (**31**)
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
13. **Module line budget** — warns on modules over **600 LOC** in `plugin/apple_mail_mcp/` and `tools/`; **fails** on baseline regression (`tests/fixtures/module_line_budget/baseline.json`). Covered by `tests/infra/test_module_line_budget.py` and `check_module_line_budget.py`.

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

## validate_tasks_layout.py

| Script | Role |
|--------|------|
| `validate_tasks_layout.py` | Enforces `tasks/` bucket layout (`active/`, `reference/`, `archive/`); covered by `tests/infra/test_tasks_layout.py` |

Agents must read `tasks/CLAUDE.md` § Agent requirements before creating or moving planning artifacts. The gate fails when:

1. Loose `*.md` or workstream folders appear at `tasks/` root (only `CLAUDE.md`, `INDEX.md`, `todo.md` allowed).
2. `active/`, `reference/`, or `archive/` is missing.
3. `tasks/CLAUDE.md` or `tasks/INDEX.md` drops required layout markers.
4. `tasks/todo.md` links to stale flat paths like `tasks/foo-2026-06-30.md`.

```bash
python3 tools/validate_tasks_layout.py
```

Runs in `bash tools/dev-check.sh` (default and release tiers).

## check_wrapper_surface.py

| Script | Role |
|--------|------|
| `check_wrapper_surface.py` | Generated mcporter wrapper command-surface check; covered by `tests/infra/test_wrapper_surface.py` |

Separate from **`validate_manifests`** — manifest validation checks Python `@mcp.tool` ↔ MCPB `tools[]` parity, but the generated `apple-mail` wrapper on PATH embeds schemas at generation time and can drift when new tools are added.

Verifies critical read commands (`get-email-by-id`, `search-emails`, `get-email-thread`, `list-inbox-emails`, `get-inbox-overview`) appear in `apple-mail --help`. Exit 0 when all present; exit 1 when missing. Skips gracefully (exit 0) if no wrapper on PATH.

```bash
python3 tools/check_wrapper_surface.py
python3 tools/check_wrapper_surface.py --wrapper /path/to/apple-mail
```

Run after regenerating the mcporter bundle or adding read tools agents rely on.

## check_module_line_budget.py

| Script | Role |
|--------|------|
| `check_module_line_budget.py` | 600 LOC budget scanner for `plugin/apple_mail_mcp/` and `tools/`; covered by `tests/infra/test_module_line_budget.py` |

Warn-only CLI (exit 0) listing oversized modules; **regression** enforced in pytest and `validate_manifests.py` via `tests/fixtures/module_line_budget/baseline.json`.

```bash
python3 tools/check_module_line_budget.py
python3 tools/check_module_line_budget.py --write-baseline tests/fixtures/module_line_budget/baseline.json
```

Runs automatically in `dev-check.sh` (default/release), `validate_manifests.sh` (regression), CI (dedicated step + pytest `-rw`), and pre-commit.

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

## inspect_envelope_index_schema.py

| Script | Role |
|--------|------|
| `inspect_envelope_index_schema.py` | Schema-only Envelope Index research helper for Phase 4a risk assessment |

Inspects only SQLite schema metadata from Mail's local Envelope Index: table names, column names/types, index names/columns, and a schema fingerprint. It is not an MCP tool, does not read message rows, redacts the file path, and requires `--confirm-read-only-live-mail-index`.

```bash
python3 tools/inspect_envelope_index_schema.py \
  --confirm-read-only-live-mail-index
```

Use this only to assess permission and schema-drift risk before any future direct-index backend work. Do not use it as a runtime query path or include its output in package artifacts.

## dev-check.sh

Tiered local gate (no live Mail except `live` tier). Requires root `.venv/`.

| Tier | Runs |
|------|------|
| `default` | `validate_manifests.sh` + `validate_tasks_layout.py` + module line budget report + `pytest` + `run_test_count_check`; adds `check_wrapper_surface.py` when **staged** files touch `plugin/apple_mail_mcp/tools/`, tool registration, or MCPB `manifest.json` |
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

### Collected-test count (single source of truth)

`tools/expected_test_count.txt` holds the one canonical collected-test count. Docs no
longer hardcode the number; `run_test_count_check` (in `default` and `release`)
recomputes the real count with `PYTEST_ADDOPTS='' pytest --collect-only tests` and fails
on drift, printing the new number to drop into that one file. The tool count (31) is
already derived/enforced separately by `validate_manifests`.

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

`.github/workflows/ci.yml` (Ubuntu, Python 3.10): `validate_manifests.sh`, module line budget report, then `pytest tests/ -q -rw`. Same gate as pre-commit; live Mail is manual ([`docs/AGENT_LIVE_TESTING.md`](../docs/AGENT_LIVE_TESTING.md)).

Run after tool add/remove, version bump, mcpb `tools[]` edit, or plugin skill marketing copy in manifests. Supplement with **`plugin-dev:plugin-validator`** when available; add **`plugin-dev:skill-reviewer`** when editing `plugin/skills/*/SKILL.md`.

## Related

[`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md) · [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) · [`.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json) · [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)
