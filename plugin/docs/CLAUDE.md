# plugin/ — shared plugin install surface

**Shared Claude Code + Codex install surface** — registers the MCP server, ships skills, bootstraps user-local venv. Tool logic lives in `apple_mail_mcp/`; see root `CLAUDE.md` for server architecture.

## Agent orchestration

Plugin/MCP/skill changes: delegate implementation to subagents when available and permitted; run **`plugin-dev:plugin-validator`** and **`plugin-dev:skill-reviewer`** (and `plugin-dev:mcp-integration` / `plugin-dev:plugin-structure` skills) before merge when those experts are available. If not, document the gap and run the local validation gates. See root [`CLAUDE.md`](../../CLAUDE.md), Agent orchestration section.

## Key files

| File | Role |
|------|------|
| `.claude-plugin/plugin.json` | Plugin manifest: `mcpServers` (includes `--draft-safe` in server args by default), keywords, version |
| `.codex-plugin/plugin.json` | Codex plugin manifest: interface metadata, `skills: "./skills"`, `mcpServers: "./.mcp.json"` |
| `.mcp.json` | Codex MCP config launching `/bin/bash ./start_mcp.sh --draft-safe` with `cwd: "."` |
| `start_mcp.sh` | Self-healing venv bootstrap (see below) + `fastmcp` import verify, then exec server |
| `apple_mail_mcp.py` | Thin entry shim → `apple_mail_mcp.__main__.main()` |
| `requirements.txt` | Runtime deps installed into `plugin/venv/` (not root `.venv/`) |

## MCP wiring

```
Claude Code → /bin/bash ${CLAUDE_PLUGIN_ROOT}/start_mcp.sh → plugin/venv/bin/python3 apple_mail_mcp.py
Codex      → cwd=<installed plugin root> /bin/bash ./start_mcp.sh → plugin/venv/bin/python3 apple_mail_mcp.py
```

`${CLAUDE_PLUGIN_ROOT}` resolves to this `plugin/` directory for Claude Code. Codex 0.133.0 does **not** expand `${CLAUDE_PLUGIN_ROOT}` inside argv; keep Codex `.mcp.json` on the `cwd: "."` + `./start_mcp.sh` contract unless a runtime smoke proves a new Codex launcher shape.

`plugin.json` passes **`--draft-safe`** to `start_mcp.sh` by default so send tools stay blocked in shared agent workspaces. Override in user MCP config only when intentional.

## Venv self-healing (`start_mcp.sh`)

The plugin venv at `plugin/venv/` is **self-healing** — no manual rebuild when Homebrew or system Python upgrades break the interpreter symlink.

| Flag | Behavior |
|------|----------|
| *(default)* | `ensure_venv`: create venv on first run; rebuild if interpreter missing/broken; reinstall deps if `fastmcp` import fails; then exec server |
| `--ensure-only`, `--check`, `--doctor` | Build/repair venv and verify imports, then **exit 0** without launching the server (installers / health checks) |

Repair triggers: dangling `venv/bin/python3` (Python removed/upgraded), missing venv, or stale/missing dependencies after a one-pass `pip install -r requirements.txt`. Logs go to stderr (`[Apple Mail MCP] …`) for Claude Desktop / Code logs.

Fresh-install test: remove `plugin/venv/` and run `./start_mcp.sh --doctor` from `plugin/`.

## Subfolders

- **`apple_mail_mcp/`**: Python package (source of truth for all 41 MCP tools)
- **`skills/`**: Procedural workflows (eleven shipped workflow skills; see `skills/CLAUDE.md`)
- **`ui/`** — Inbox dashboard HTML via `mcp-ui-server` (`dashboard.py`, `templates/`)

## Related distribution shapes

- **`../../.claude-plugin/marketplace.json`** — Top-level Claude Code marketplace manifest (`name`: `Agentic-Assets`); `plugins[0].source` → `./plugin` inside the GitHub marketplace checkout; install with `claude plugin marketplace add Agentic-Assets/apple-mail-mcp --scope user`, `claude plugin marketplace update Agentic-Assets`, then `claude plugin install apple-mail@Agentic-Assets --scope user`
- **`../../.agents/plugins/marketplace.json`** — Top-level Codex marketplace manifest (`name`: `Agentic-Assets`); `plugins[0].source` → `./plugin` inside the GitHub marketplace checkout; install with `codex plugin marketplace add https://github.com/Agentic-Assets/apple-mail-mcp.git` then `codex plugin add apple-mail@Agentic-Assets`
- **`../../apple-mail-mcpb/`** — Claude Desktop `.mcpb` bundle build (separate manifest)

## When to change what

- **Manifest edits** (`plugin.json`, marketplace, mcpb, Codex `.mcp.json`): bump version in all versioned files (see root `CLAUDE.md`); keep `.agents/plugins/marketplace.json` pointed at `./plugin` and `plugin/.mcp.json` draft-safe unless intentionally changing send semantics; run **`plugin-dev:plugin-validator`** before merge when available.
- **Launcher / deps**: edit `start_mcp.sh`, `requirements.txt`, or `pyproject.toml`; keep plugin and PyPI dependencies/packages aligned (`mcp-ui-server`, `plugin/ui`); test fresh venv by removing `plugin/venv/`; run `bash tools/gates/dev-check.sh release`.
- **New MCP tools**: implement under `apple_mail_mcp/tools/` and register in `apple_mail_mcp/__init__.py` — not in this wrapper layer.
- **New user entry points**: add skills under `skills/` only. Do not restore `commands/`; release validation fails if the retired legacy command directory reappears.
- **Venvs**: `plugin/venv/` = user install (gitignored); `../../.venv/` = dev pytest/editable install.
