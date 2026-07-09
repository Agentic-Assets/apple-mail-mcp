# .claude-plugin/ — marketplace manifest

Top-level **Claude Code marketplace** registration → [`plugin/`](../plugin/) via `"source": "./plugin"`.
That source is relative inside the GitHub marketplace checkout. User install
docs must register `Agentic-Assets/apple-mail-mcp`, not a local checkout path,
so Claude can keep the marketplace tied to its GitHub source.

Codex Desktop/CLI uses a separate marketplace file at [`../.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json) plus [`../plugin/.codex-plugin/plugin.json`](../plugin/.codex-plugin/plugin.json). Keep Claude and Codex plugin identities aligned, but do not add Codex fields to this Claude marketplace manifest.

## Two version fields

| Field | Example | Meaning |
|-------|---------|---------|
| `metadata.version` | `1.0.0` | **This marketplace JSON** — not the plugin. Don't bump on every release. |
| `plugins[0].version` | `3.9.1` | **Plugin release**: sync with `pyproject.toml`, `plugin.json`, `server.json`, mcpb manifest. |

`validate_manifests.sh` checks `plugins[0].version`, tool-count in `plugins[0].description`, `source`, listed skill paths, plugin name/version parity with `plugin/.claude-plugin/plugin.json`, **and the dual-component-conflict rule below**.

## Components live in plugin.json — not here

Claude Code rejects the install with *"conflicting manifests: both plugin.json and marketplace entry specify components"* when both manifests declare any of `commands`, `agents`, `skills`, `hooks`, `mcpServers` and `strict` is not `true` on the marketplace entry.

This repo keeps **all** component specs (only `mcpServers` today) in `plugin/.claude-plugin/plugin.json`. The marketplace entry stays metadata-only: `name`, `displayName`, `description`, `version`, `author`, `homepage`, `repository`, `category`, `keywords`, `source`, `strict: false`. Skills auto-discover from `plugin/skills/<name>/SKILL.md` — do not re-list them here.

The check lives in `tools/manifest_checks/install_contracts.py::_check_marketplace_contract` (entry point `tools/validators/validate_manifests.py`; regression tests in `tests/infra/test_validate_manifests.py`: `test_marketplace_contract_rejects_dual_component_declarations` and `test_marketplace_contract_allows_dual_components_when_strict_true`). If a future change needs component fields in the marketplace entry, set `"strict": true` in the same edit so installs keep working.

## Not here

- Plugin manifest → `plugin/.claude-plugin/plugin.json`
- Codex marketplace → `.agents/plugins/marketplace.json`
- Codex plugin manifest → `plugin/.codex-plugin/plugin.json`
- Desktop bundle → [`apple-mail-mcpb/`](../apple-mail-mcpb/)

## Local install

```bash
# From GitHub (users)
claude plugin marketplace add Agentic-Assets/apple-mail-mcp --scope user
claude plugin marketplace update Agentic-Assets
claude plugin install apple-mail@Agentic-Assets --scope user

# From repo checkout (maintainer/offline)
claude plugin marketplace add .
claude plugin install apple-mail@Agentic-Assets
```

Installs the MCP server (31 tools, **`--draft-safe`** by default) plus **nine** auto-discovered workflow skills under `plugin/skills/` — see [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md).

Codex users install through the sibling Codex marketplace:

```bash
codex plugin marketplace add https://github.com/Agentic-Assets/apple-mail-mcp.git
codex plugin add apple-mail@Agentic-Assets
```

For a local checkout, use `codex plugin marketplace add .` before the same `codex plugin add apple-mail@Agentic-Assets` command.

After edits: `plugin-dev:plugin-validator` when available + `tools/gates/validate_manifests.sh` (+ `plugin-dev:skill-reviewer` when skills change).

## Related

[`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) · [`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md) · [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)
