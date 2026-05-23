# tools/ — validation scripts

Dev-infra guardrails — not MCP tools (`plugin/apple_mail_mcp/tools/` is the server).

## validate_manifests

| Script | Role |
|--------|------|
| `validate_manifests.sh` | Bash entry; **CI calls this** |
| `validate_manifests.py` | Python equivalent; covered by `tests/test_validate_manifests.py` |

Enforces (source of truth: `pyproject.toml` `[project].version`):

1. **Version sync** — `plugin.json`, `marketplace.json plugins[0].version`, `server.json` (×2), `apple-mail-mcpb/manifest.json`
2. **Tool count claims** — descriptions must match `rg "^@mcp\.tool" … | wc -l` (**28**)
3. **MCPB name parity** — `@mcp.tool` names ↔ `apple-mail-mcpb/manifest.json` `tools[]`
4. **Artifact freshness** — when `apple-mail-plugin.zip` or `apple-mail-mcp-v{version}.mcpb` exists locally, selected archive members must match the current plugin source/manifest bytes
5. **MCPB structural integrity** — the `.mcpb` must contain no zero-byte directory entries (names ending in `/`); raw `zip -r .` produces them and `mcpb unpack` / Claude Desktop's installer abort with `ENOENT`. Always build via `mcpb pack` (see `apple-mail-mcpb/build-mcpb.sh`).
6. **Release artifact presence** — opt in with `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1` to require both local distributables before shipping

```bash
bash tools/validate_manifests.sh
APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 bash tools/validate_manifests.sh
```

Skips marketplace `metadata.version` (1.0.0) — see [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md).

## check_wrapper_surface.py

| Script | Role |
|--------|------|
| `check_wrapper_surface.py` | Generated mcporter wrapper command-surface check; covered by `tests/test_wrapper_surface.py` |

Separate from **`validate_manifests`** — manifest validation checks Python `@mcp.tool` ↔ MCPB `tools[]` parity only. The generated `apple-mail` wrapper on PATH embeds schemas at generation time and can drift when new tools are added.

Verifies critical read commands (`get-email-by-id`, `search-emails`, `get-email-thread`, `list-inbox-emails`, `get-inbox-overview`) appear in `apple-mail --help`. Exit 0 when all present; exit 1 when missing. Skips gracefully (exit 0) if no wrapper on PATH.

```bash
python3 tools/check_wrapper_surface.py
python3 tools/check_wrapper_surface.py --wrapper /path/to/apple-mail
```

Run after regenerating the mcporter bundle or adding read tools agents rely on.

## dev-check.sh

Tiered local gate (no live Mail except `live` tier). Requires root `.venv/`.

| Tier | Runs |
|------|------|
| `default` | `validate_manifests.sh` + `pytest`; adds `check_wrapper_surface.py` when **staged** files touch `plugin/apple_mail_mcp/tools/`, tool registration, or MCPB `manifest.json` |
| `surface` | default + wrapper check always |
| `manifest` | manifests only |
| `live` | default + `.venv/bin/apple-mail quick-check --json` |
| `release` | default + wrapper + `tools/build-artifacts.sh` (rebuilds `apple-mail-plugin.zip` + `.mcpb`, runs `REQUIRE_DIST_ARTIFACTS` validate, runs `mcpb unpack`/`validate` smoke). **Run before every commit that touches `plugin/` or manifests** — finalize-apple-mail-mcp skill enforces this. |
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

[`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md) · [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md) · [`docs/CLAUDE-conventions.md`](../docs/CLAUDE-conventions.md)
