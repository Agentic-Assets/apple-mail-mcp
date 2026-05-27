# apple-mail-mcpb/ — Claude Desktop bundle

Build files for the **`.mcpb`** distributable. Same Python server as [`plugin/`](../plugin/) — copied at build, not a separate codebase.

> **One of three artifacts.** See root [`CLAUDE.md`](../CLAUDE.md) § Distribution channels for the full map:
> `.mcpb` here ships the Claude Desktop chat extension; `apple-mail-plugin.zip` is the Claude Code marketplace zip; `apple-mail.plugin` is the byte-identical Cowork upload artifact. All three rebuild from `tools/build-artifacts.sh` in one shot.

| File | Role |
|------|------|
| `manifest.json` | Version, `tools[]`, `user_config`, server entry |
| `build-mcpb.sh` | Stage `plugin/` → zip `../apple-mail-mcp-v{VERSION}.mcpb` |

```bash
bash tools/dev-check.sh release
```

Use `cd apple-mail-mcpb && ./build-mcpb.sh` only for bundle-only debugging; the release gate rebuilds both distributables and runs the validator/test/smoke stack.

Copies `apple_mail_mcp.py`, `start_mcp.sh`, `requirements.txt`, `apple_mail_mcp/`, mirrored `plugin/skills` → **`skills/`**, and `ui/` in build output. No venv in bundle — user machine creates it via `start_mcp.sh`. Keep embedded README Python 3.10+ claim in sync.

**Build must use `mcpb pack`** when available (official CLI, `npm install -g @anthropic-ai/mcpb`). Raw `zip -r .` emits zero-byte directory entries that `mcpb unpack` and Claude Desktop's installer treat as files — install fails with `ENOENT: no such file or directory, open '.../ui/'`. `build-mcpb.sh` prefers `mcpb pack` and falls back to `zip -X -D` only when the CLI is missing. `validate_manifests.py` enforces exact artifact membership plus the no-directory-entry rule on every commit.

## tools[] must match code

Full `tools[]` in `manifest.json` must list every `@mcp.tool` name in code; description must claim correct count (**28**). Validated by [`tools/validate_manifests.sh`](../tools/validate_manifests.sh).

## vs plugin/ and Cowork

| | Claude Code | Claude Desktop (chat) | Claude Desktop (Cowork) |
|---|-------------|------------------------|--------------------------|
| Manifest | `plugin/.claude-plugin/plugin.json` | `manifest.json` (DXT) | `plugin/.claude-plugin/plugin.json` |
| Discovery | `.claude-plugin/marketplace.json` | Direct `.mcpb` install via "Add Custom Plugin" / "Install from file" | Customize → Add plugin → Upload plugin (accepts `.plugin`) |
| Artifact | `apple-mail-plugin.zip` | `apple-mail-mcp-v{VERSION}.mcpb` | `apple-mail.plugin` (byte-identical to the `.zip`) |
| Entrypoint | `start_mcp.sh` via `mcpServers` in `plugin.json` | `start_mcp.sh` via `manifest.json` `server.mcp_config` | `start_mcp.sh` via `mcpServers` in `plugin.json` |

Version sync: five files per [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md). Deferred release/backlog items live in [`tasks/robustness-backlog-2026-05-22.md`](../tasks/robustness-backlog-2026-05-22.md).

## Related

[`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md) · [`tools/CLAUDE.md`](../tools/CLAUDE.md)
