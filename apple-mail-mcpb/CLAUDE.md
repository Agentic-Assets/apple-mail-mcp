# apple-mail-mcpb/ — Claude Desktop bundle

Build files for the **`.mcpb`** distributable. Same Python server as [`plugin/`](../plugin/) — copied at build, not a separate codebase.

| File | Role |
|------|------|
| `manifest.json` | Version, `tools[]`, `user_config`, server entry |
| `build-mcpb.sh` | Stage `plugin/` → zip `../apple-mail-mcp-v{VERSION}.mcpb` |

```bash
cd apple-mail-mcpb && ./build-mcpb.sh
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash ../tools/validate_manifests.sh
```

Copies `apple_mail_mcp.py`, `start_mcp.sh`, `requirements.txt`, `apple_mail_mcp/`, mirrored `plugin/skills` → **`skills/`** in build output, optional `ui/`. No venv in bundle — user machine creates it via `start_mcp.sh`. Keep embedded README Python 3.10+ claim in sync.

**Build must use `mcpb pack`** (official CLI, `npm install -g @anthropic-ai/mcpb`). Raw `zip -r .` emits zero-byte directory entries that `mcpb unpack` and Claude Desktop's installer treat as files — install fails with `ENOENT: no such file or directory, open '.../ui/'`. `build-mcpb.sh` prefers `mcpb pack` and falls back to `zip -X` only when the CLI is missing. `validate_manifests.py` enforces the structural rule on every commit.

## tools[] must match code

Full `tools[]` in `manifest.json` must list every `@mcp.tool` name in code; description must claim correct count (**28**). Validated by [`tools/validate_manifests.sh`](../tools/validate_manifests.sh).

## vs plugin/

| | Claude Code | Claude Desktop |
|---|-------------|----------------|
| Manifest | `plugin/.claude-plugin/plugin.json` | `manifest.json` |
| Discovery | `.claude-plugin/marketplace.json` | Direct `.mcpb` install |

Version sync: five files per [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md). Deferred release/backlog items live in [`tasks/robustness-backlog-2026-05-22.md`](../tasks/robustness-backlog-2026-05-22.md).

## Related

[`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) · [`tools/CLAUDE.md`](../tools/CLAUDE.md)
