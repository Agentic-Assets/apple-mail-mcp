# CLAUDE.md

Navigation hub for **apple-mail-mcp**: one Python MCP server (**28 tools**, `fastmcp>=3.1.0,<4`) shipped as PyPI package (`mcp-apple-mail`), shared Claude Code + Codex plugin runtime (`plugin/`), Claude Desktop/Cowork `.plugin`, and Claude Desktop `.mcpb` (`apple-mail-mcpb/`). Marketplace entries: `.claude-plugin/marketplace.json` for Claude Code and `.agents/plugins/marketplace.json` for Codex Desktop/CLI. **Test count:** run `.venv/bin/pytest tests/ --collect-only -q` (do not hardcode totals in docs).

## Distribution channels (four install surfaces from one source tree)

A single `plugin/` runtime serves Claude Code and Codex plugin installs; `bash tools/build-artifacts.sh` emits the Claude Desktop upload artifacts. Drift between manifests and artifacts has caused real installer failures; `tools/validate_manifests.py` enforces parity and the release gate refuses to ship with any artifact missing or stale.

| Surface | Install target | Format |
|---------|----------------|--------|
| `apple-mail-plugin.zip` | Claude Code plugin marketplace (`claude plugin install`) | Plain zip, `.claude-plugin/plugin.json` at zip root |
| `apple-mail.plugin` | Claude Desktop **Cowork → Customize → Add plugin → Upload plugin** | Byte-identical copy of the `.zip`, `.plugin` extension is what the Cowork UI accepts |
| `apple-mail-mcp-v{VERSION}.mcpb` | Claude Desktop chat extension via "Add Custom Plugin" / "Install from file" | DXT bundle (`mcpb pack`), `manifest.json` at zip root |
| `.agents/plugins/marketplace.json` + `plugin/.codex-plugin/plugin.json` | Codex Desktop/CLI plugin marketplace (`codex plugin add`) | Repo marketplace points at shared `./plugin` runtime with `plugin/.mcp.json` |

If you change distribution, version, or filenames: re-run `bash tools/dev-check.sh release` and verify `tests/test_validate_manifests.py` covers the change. **Never** ship a `.plugin` whose bytes differ from the `.zip` — the validator and CI tests treat that as a hard error.

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
| Plugin wrapper, `start_mcp.sh`, manifests | [`plugin/docs/CLAUDE.md`](plugin/docs/CLAUDE.md) |
| Package entry, `core.py`, `server.py`, CLI | [`plugin/apple_mail_mcp/CLAUDE.md`](plugin/apple_mail_mcp/CLAUDE.md) |
| Individual MCP tools | [`plugin/apple_mail_mcp/tools/CLAUDE.md`](plugin/apple_mail_mcp/tools/CLAUDE.md) |
| Skills (9 workflow skills) | [`plugin/skills/CLAUDE.md`](plugin/skills/CLAUDE.md) |
| Tests & mocking AppleScript | [`tests/CLAUDE.md`](tests/CLAUDE.md) |
| Manifest validation, pre-commit | [`tools/CLAUDE.md`](tools/CLAUDE.md) |
| Live CLI testing, agent workflows | [`docs/CLAUDE.md`](docs/CLAUDE.md) |
| Deep tool/skill/plugin rules | [`docs/CLAUDE-conventions.md`](docs/CLAUDE-conventions.md) |
| Phase plans & backlog | [`tasks/CLAUDE.md`](tasks/CLAUDE.md) · [`tasks/todo.md`](tasks/todo.md) |
| MCPB bundle build | [`apple-mail-mcpb/CLAUDE.md`](apple-mail-mcpb/CLAUDE.md) |
| Claude Code marketplace manifest | [`.claude-plugin/CLAUDE.md`](.claude-plugin/CLAUDE.md) |
| Codex Desktop/CLI plugin surface | [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json) · [`plugin/.codex-plugin/plugin.json`](plugin/.codex-plugin/plugin.json) · [`plugin/.mcp.json`](plugin/.mcp.json) |

## Architecture (prose)

**Plugin wrapper** (`plugin/start_mcp.sh`, `plugin.json`) launches **Python package** (`plugin/apple_mail_mcp/`: `__main__` → import `tools/*` → register on `FastMCP` in `server.py`) which drives **Mail.app** through **`core.run_applescript()`** (stdin osascript, escaped user input, JSON-safe output). Dev venv: repo root `.venv/`; user plugin venv: `plugin/venv/` (install-time only).

## Dev setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest tests/ --collect-only -q   # discover current test count
.venv/bin/pytest tests/ -q                  # run full suite
.venv/bin/apple-mail quick-check --json    # live Mail smoke (~30s)
.venv/bin/python plugin/apple_mail_mcp.py --read-only
```

## Version bump (release together)

- `pyproject.toml` → `[project].version`
- `plugin/.claude-plugin/plugin.json` → `version`
- `plugin/.codex-plugin/plugin.json` → `version`
- `.claude-plugin/marketplace.json` → `plugins[0].version` (not `metadata.version`)
- `server.json` → top-level + `packages[0].version`
- `apple-mail-mcpb/manifest.json` → `version`

Sync tool-count claims in manifests with `grep -c "^@mcp.tool" plugin/apple_mail_mcp/tools/*.py`. Codex marketplace metadata lives in `.agents/plugins/marketplace.json` and points at `./plugin`; Codex MCP wiring lives in `plugin/.mcp.json`, should keep `--draft-safe`, and should launch via `cwd: "."` + `./start_mcp.sh` unless a fresh `bash tools/validate-codex-plugin.sh` runtime smoke proves a different Codex contract. Before shipping, run `bash tools/dev-check.sh release`; the gate enforces fatal `ruff check`, `ruff format --check`, and `mypy --strict` for `plugin/apple_mail_mcp/`, then exact plugin zip/MCPB payloads, byte parity between `apple-mail-plugin.zip` and `apple-mail.plugin`, package deps/packages, install contracts, source syntax, and artifact freshness. Do not add new lint/type tools without asking.

## Related folders

`plugin/apple_mail_mcp/` (source of truth) · `plugin/` (shared Claude Code + Codex plugin runtime) · `.claude-plugin/` (Claude Code marketplace) · `.agents/plugins/` (Codex marketplace) · `apple-mail-mcpb/` · `tests/` · `tools/` · `docs/` · `tasks/`

**Repo agent skills:** Add under `.agents/skills/<name>/`; symlink `.claude/skills/<name>` → `../../.agents/skills/<name>` (not `.cursor/skills/`). Commit and push after adding or moving skills.
**Post-change ship:** Invoke `finalize-apple-mail-mcp` to sync docs, CLAUDE.md, manifests, then commit and push when the user asks.
