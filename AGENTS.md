# AGENTS.md

Navigation hub for **apple-mail-mcp**: one Python MCP server (**28 tools**, **355 tests + 30 subtests**, `fastmcp>=3.1.0,<4`) shipped as PyPI package (`mcp-apple-mail`), Codex plugin (`plugin/`), and Codex Desktop `.mcpb` (`apple-mail-mcpb/`). Marketplace entry: `.Codex-plugin/marketplace.json`.

## Agent orchestration (required)

**Always use subagents** for both **research and implementation** — not just exploration. Delegate real fixes, tests, docs, and live verification to subagents; the lead agent orchestrates and reviews.

| When | Subagent |
|------|----------|
| Code changes, tests, docs | `generalPurpose` |
| Codebase search / file discovery | `explore` |
| pytest, live CLI, shell tasks | `shell` |
| Independent workstreams | Run subagents **in parallel** |
| Dependent steps (e.g. perf gates before tool edits) | Run subagents **sequentially** |

**Always use plugin-dev experts** for plugin, MCP, marketplace, and skill work — invoke before and after substantive changes:

| Expert | Use for |
|--------|---------|
| **`plugin-dev:plugin-validator`** | Manifest drift, tool counts, marketplace readiness |
| **`plugin-dev:skill-reviewer`** | Bundled skill descriptions, trigger overlap, safety language |
| Skills: **`plugin-dev:mcp-integration`**, **`plugin-dev:plugin-structure`**, **`mcp-builder`** | MCP server design, `.mcp.json` / `plugin.json`, tool quality |

Do not solo large plugin or perf workstreams without at least one plugin-dev expert pass.

**Run `code-simplifier:code-simplifier` regularly** — after any non-trivial change to tools, backend, helpers, or tests. Especially after refactors that touched many sites (e.g. capability-token / structured-error / bounded-scan work). Behavior must be preserved; the simplifier collapses duplication, drops dead branches, and tightens names. Trigger it as part of every "ready to ship" pass alongside `plugin-validator` and `skill-reviewer`, and any time a file grows past ~600 LOC or a helper sprouts >3 near-copies.

## When working in…

| Area | Read |
|------|------|
| Plugin wrapper, `start_mcp.sh`, manifests | [`plugin/docs/AGENTS.md`](plugin/docs/AGENTS.md) |
| Package entry, `core.py`, `server.py`, CLI | [`plugin/apple_mail_mcp/AGENTS.md`](plugin/apple_mail_mcp/AGENTS.md) |
| Individual MCP tools | [`plugin/apple_mail_mcp/tools/AGENTS.md`](plugin/apple_mail_mcp/tools/AGENTS.md) |
| Skills (9 workflow skills) | [`plugin/skills/AGENTS.md`](plugin/skills/AGENTS.md) |
| Legacy slash commands | [`plugin/docs/commands.md`](plugin/docs/commands.md) |
| Tests & mocking AppleScript | [`tests/AGENTS.md`](tests/AGENTS.md) |
| Manifest validation, pre-commit | [`tools/AGENTS.md`](tools/AGENTS.md) |
| Live CLI testing, agent workflows | [`docs/AGENTS.md`](docs/AGENTS.md) |
| Deep tool/skill/plugin rules | [`docs/Codex-conventions.md`](docs/Codex-conventions.md) |
| Phase plans & backlog | [`tasks/AGENTS.md`](tasks/AGENTS.md) · [`tasks/todo.md`](tasks/todo.md) |
| MCPB bundle build | [`apple-mail-mcpb/AGENTS.md`](apple-mail-mcpb/AGENTS.md) |
| Marketplace manifest | [`.Codex-plugin/AGENTS.md`](.Codex-plugin/AGENTS.md) |

## Architecture (prose)

**Plugin wrapper** (`plugin/start_mcp.sh`, `plugin.json`) launches **Python package** (`plugin/apple_mail_mcp/`: `__main__` → import `tools/*` → register on `FastMCP` in `server.py`) which drives **Mail.app** through **`core.run_applescript()`** (stdin osascript, escaped user input, JSON-safe output). Dev venv: repo root `.venv/`; user plugin venv: `plugin/venv/` (install-time only).

## Dev setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest tests/                    # 355 tests + 30 subtests
.venv/bin/apple-mail quick-check --json    # live Mail smoke (~30s)
.venv/bin/python plugin/apple_mail_mcp.py --read-only
```

## Version bump (release together)

- `pyproject.toml` → `[project].version`
- `plugin/.Codex-plugin/plugin.json` → `version`
- `.Codex-plugin/marketplace.json` → `plugins[0].version` (not `metadata.version`)
- `server.json` → top-level + `packages[0].version`
- `apple-mail-mcpb/manifest.json` → `version`

Sync tool-count claims in manifests with `grep -c "^@mcp.tool" plugin/apple_mail_mcp/tools/*.py`. No repo lint config — don't add without asking.

## Related folders

`plugin/apple_mail_mcp/` (source of truth) · `plugin/` (Codex plugin) · `apple-mail-mcpb/` · `.Codex-plugin/` · `tests/` · `tools/` · `docs/` · `tasks/`

**Repo agent skills:** Add under `.agents/skills/<name>/`; symlink `.claude/skills/<name>` → `../../.agents/skills/<name>` (not `.cursor/skills/`). Commit and push after adding or moving skills.
**Post-change ship:** Invoke `finalize-apple-mail-mcp` to sync docs, AGENTS.md, manifests, then commit and push when the user asks.
