# .claude-plugin/ — marketplace manifest

Top-level **Claude Code marketplace** registration → [`plugin/`](../plugin/) via `"source": "./plugin"`.

## Two version fields

| Field | Example | Meaning |
|-------|---------|---------|
| `metadata.version` | `1.0.0` | **This marketplace JSON** — not the plugin. Don't bump on every release. |
| `plugins[0].version` | `3.2.1` | **Plugin release** — sync with `pyproject.toml`, `plugin.json`, `server.json`, mcpb manifest. |

`validate_manifests.sh` checks `plugins[0].version`, tool-count in `plugins[0].description`, `source`, listed skill paths, plugin name/version parity with `plugin/.claude-plugin/plugin.json`, **and the dual-component-conflict rule below**.

## Components live in plugin.json — not here

Claude Code rejects the install with *"conflicting manifests: both plugin.json and marketplace entry specify components"* when both manifests declare any of `commands`, `agents`, `skills`, `hooks`, `mcpServers` and `strict` is not `true` on the marketplace entry.

This repo keeps **all** component specs (only `mcpServers` today) in `plugin/.claude-plugin/plugin.json`. The marketplace entry stays metadata-only: `name`, `displayName`, `description`, `version`, `author`, `homepage`, `repository`, `category`, `keywords`, `source`, `strict: false`. Skills auto-discover from `plugin/skills/<name>/SKILL.md` — do not re-list them here.

The check lives in `tools/validate_manifests.py::_check_marketplace_contract` (regression tests: `test_marketplace_contract_rejects_dual_component_declarations` and `test_marketplace_contract_allows_dual_components_when_strict_true`). If a future change genuinely needs component fields in the marketplace entry, set `"strict": true` in the same edit so installs keep working.

## Not here

- Plugin manifest → `plugin/.claude-plugin/plugin.json`
- Desktop bundle → [`apple-mail-mcpb/`](../apple-mail-mcpb/)

## Local install

```bash
# From GitHub (users)
claude plugin marketplace add Agentic-Assets/apple-mail-mcp
claude plugin install apple-mail@apple-mail-mcp

# From repo checkout (dev)
claude plugin marketplace add .
claude plugin install apple-mail@apple-mail-mcp
```

Installs the MCP server (28 tools, **`--draft-safe`** by default) plus **nine** auto-discovered workflow skills under `plugin/skills/` — see [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md).

After edits: `plugin-dev:plugin-validator` + `tools/validate_manifests.sh` (+ `plugin-dev:skill-reviewer` when skills change).

## Related

[`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) · [`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md) · [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)
