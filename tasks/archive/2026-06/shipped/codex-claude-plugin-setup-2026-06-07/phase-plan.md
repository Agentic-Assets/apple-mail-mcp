# Codex and Claude Plugin Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Apple Mail MCP install cleanly and verifiably across Claude Desktop, Claude Code, Codex Desktop, and Codex CLI without weakening the existing draft-safe defaults.

**Architecture:** Preserve the existing `plugin/` payload as the shared runtime. Add host-specific packaging around it: Claude keeps `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`; Codex gets `plugin/.codex-plugin/plugin.json`, `plugin/.mcp.json`, and `.agents/plugins/marketplace.json`. Extend the manifest validator and docs so Codex drift fails in the same release gate as Claude/MCPB drift.

**Tech Stack:** Python 3.10+, FastMCP, Bash launcher scripts, JSON manifests, Codex plugin marketplace, Claude Code plugin marketplace, Claude Desktop MCPB.

---

## Discovery Summary

- Corbis reference repo uses separate Claude and Codex manifests: `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, generated plugin `.claude-plugin/plugin.json`, generated `.codex-plugin/plugin.json`, and `.mcp.json`.
- Apple Mail started with strong Claude support: `plugin/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `apple-mail.plugin`, `apple-mail-plugin.zip`, and `apple-mail-mcp-v3.6.0.mcpb`.
- Apple Mail lacked Codex-native plugin files at discovery time: no `plugin/.codex-plugin/plugin.json`, no `plugin/.mcp.json`, and no `.agents/plugins/marketplace.json`.
- A validator-style subagent proved `codex plugin marketplace add .` can discover the existing Claude marketplace, but `codex plugin add apple-mail@apple-mail-mcp` fails with `missing or invalid plugin.json` until a `.codex-plugin/plugin.json` and `.mcp.json` are present.
- Initial discovery found root `AGENTS.md` still had stale Codex-path guidance from a mechanical migration. Current close-out guidance should point Codex agents at the real Claude docs plus `.agents/plugins/marketplace.json`, `plugin/.codex-plugin/plugin.json`, and `plugin/.mcp.json`.

## File Structure

- Create `plugin/.codex-plugin/plugin.json`: Codex plugin manifest with UI metadata, `skills: "./skills"`, and `mcpServers: "./.mcp.json"`.
- Create `plugin/.mcp.json`: Codex MCP config that launches `/bin/bash ${CLAUDE_PLUGIN_ROOT}/start_mcp.sh --draft-safe`.
- Create `.agents/plugins/marketplace.json`: Codex repo marketplace entry pointing at `./plugin`.
- Create `tools/validate-codex-plugin.sh`: temp `CODEX_HOME` smoke for Codex marketplace add/install/list when `codex plugin` exists.
- Modify `tools/validate_manifests.py`: validate Codex manifest/marketplace/MCP contract and optionally invoke the Codex smoke.
- Modify `tools/validate_manifests.sh`: check Codex version/tool-count claims and call the Python validator as source of truth.
- Modify `tests/test_validate_manifests.py`: add Codex contract regression tests.
- Modify `README.md`: add Codex Desktop/CLI install commands and update project structure/test count.
- Modify `AGENTS.md`, `CLAUDE.md`, `.claude-plugin/CLAUDE.md`, `plugin/docs/CLAUDE.md`, `docs/CLAUDE-conventions.md`, `tools/CLAUDE.md`, `tests/CLAUDE.md`, and `tasks/CLAUDE.md`: sync packaging docs and stale test counts.
- Modify `tasks/codex-claude-plugin-setup-2026-06-07/progress-log.md`: append execution and verification evidence.

### Task 1: Add Codex Plugin Surface

**Files:**
- Create: `plugin/.codex-plugin/plugin.json`
- Create: `plugin/.mcp.json`
- Create: `.agents/plugins/marketplace.json`

- [ ] **Step 1: Add Codex plugin manifest**

Create `plugin/.codex-plugin/plugin.json` with:

```json
{
  "name": "apple-mail",
  "version": "3.6.1",
  "description": "Natural language interface for Apple Mail -- search, compose, triage, organize, and analyze email with 28 MCP tools plus bundled workflow skills.",
  "author": {
    "name": "Agentic Assets",
    "url": "https://github.com/Agentic-Assets"
  },
  "homepage": "https://github.com/Agentic-Assets/apple-mail-mcp",
  "repository": "https://github.com/Agentic-Assets/apple-mail-mcp",
  "license": "MIT",
  "keywords": [
    "mcp",
    "mcp-server",
    "apple-mail",
    "email",
    "ai",
    "codex",
    "claude",
    "automation",
    "macos"
  ],
  "skills": "./skills",
  "mcpServers": "./.mcp.json",
  "interface": {
    "displayName": "Apple Mail",
    "shortDescription": "Draft-safe Apple Mail workflows for Codex.",
    "longDescription": "Natural language Apple Mail workflows for Codex and Claude: search, compose drafts, reply, forward, triage, organize, analyze, export, and manage attachments through a local macOS MCP server. Default plugin launches with --draft-safe so agents create reviewable drafts instead of sending mail.",
    "developerName": "Agentic Assets",
    "category": "Productivity",
    "capabilities": [
      "MCP",
      "Skills",
      "Email"
    ],
    "websiteURL": "https://github.com/Agentic-Assets/apple-mail-mcp",
    "defaultPrompt": [
      "Summarize my inbox and draft replies.",
      "Find emails from this week that need action.",
      "Create reviewable Apple Mail drafts."
    ],
    "brandColor": "#2563EB"
  }
}
```

- [ ] **Step 2: Add Codex MCP config**

Create `plugin/.mcp.json` with:

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/bin/bash",
      "args": [
        "${CLAUDE_PLUGIN_ROOT}/start_mcp.sh",
        "--draft-safe"
      ]
    }
  }
}
```

- [ ] **Step 3: Add Codex repo marketplace**

Create `.agents/plugins/marketplace.json` with:

```json
{
  "name": "apple-mail-mcp",
  "interface": {
    "displayName": "Apple Mail MCP"
  },
  "plugins": [
    {
      "name": "apple-mail",
      "source": {
        "source": "local",
        "path": "./plugin"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

- [ ] **Step 4: Run manifest validation**

Run: `bash tools/dev-check.sh manifest`

Expected: initially fail until Task 2 teaches validators about Codex, then pass.

### Task 2: Validate Codex Manifests and Install Smoke

**Files:**
- Modify: `tools/validate_manifests.py`
- Modify: `tools/validate_manifests.sh`
- Modify: `tests/test_validate_manifests.py`
- Create: `tools/validate-codex-plugin.sh`

- [ ] **Step 1: Add Python Codex contract validator**

In `tools/validate_manifests.py`, add `_check_codex_plugin_contract(expected_version, actual_tool_count, errors)` that verifies:

- `.agents/plugins/marketplace.json` exists.
- Top-level marketplace `name` is `apple-mail-mcp`.
- Top-level `interface.displayName` is `Apple Mail MCP`.
- `plugins[0].name` is `apple-mail`.
- `plugins[0].source` is `{"source": "local", "path": "./plugin"}`.
- `plugins[0].policy.installation` is `AVAILABLE`.
- `plugins[0].policy.authentication` is `ON_INSTALL`.
- `plugins[0].category` is `Productivity`.
- `plugin/.codex-plugin/plugin.json` exists.
- Codex manifest `name`, `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `skills`, `mcpServers`, and `interface` are present.
- Codex manifest `version` equals `pyproject.toml` version.
- Codex manifest description claims the actual `@mcp.tool` count.
- `skills` is `./skills` and resolves to `plugin/skills`.
- `mcpServers` is `./.mcp.json` and resolves to `plugin/.mcp.json`.
- `plugin/.mcp.json` has `mcpServers.apple-mail.command == "/bin/bash"`.
- `plugin/.mcp.json` has first arg `${CLAUDE_PLUGIN_ROOT}/start_mcp.sh`.
- `plugin/.mcp.json` includes `--draft-safe`.

- [ ] **Step 2: Wire validator into `main()`**

Call `_check_codex_plugin_contract(expected_version, actual_count, errors)` after `_check_marketplace_contract(...)` and before MCPB checks.

- [ ] **Step 3: Add Bash checks**

In `tools/validate_manifests.sh`, add:

```bash
assert_version "plugin/.codex-plugin/plugin.json" "version" \
  "plugin/.codex-plugin/plugin.json version"
```

Also include Codex manifest description in the tool-count claim Python block.

- [ ] **Step 4: Add Codex smoke script**

Create `tools/validate-codex-plugin.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not on PATH; skipping Codex plugin install smoke"
  exit 0
fi

if ! codex plugin --help >/dev/null 2>&1; then
  echo "codex CLI does not expose plugin commands; skipping Codex plugin install smoke"
  exit 0
fi

TMP_HOME="$(mktemp -d)"
trap 'rm -rf "$TMP_HOME"' EXIT

export CODEX_HOME="$TMP_HOME"

codex plugin marketplace add .
codex plugin add apple-mail@apple-mail-mcp
codex plugin list --marketplace apple-mail-mcp | grep -F "apple-mail@apple-mail-mcp" >/dev/null

echo "Codex plugin install smoke OK"
```

- [ ] **Step 5: Add tests**

Add tests that:

- Construct a malformed temp Codex marketplace/manifest and assert useful errors.
- Construct a valid temp Codex marketplace/manifest/MCP config and assert no Codex errors.
- Run the current repo validator test with the new files included.

- [ ] **Step 6: Run focused tests**

Run: `.venv/bin/pytest tests/test_validate_manifests.py -q`

Expected: all tests pass.

### Task 3: Add Easy Install Commands and Fix Agent Docs

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `.claude-plugin/CLAUDE.md`
- Modify: `plugin/docs/CLAUDE.md`
- Modify: `docs/CLAUDE-conventions.md`
- Modify: `tools/CLAUDE.md`
- Modify: `tests/CLAUDE.md`
- Modify: `tasks/CLAUDE.md`

- [ ] **Step 1: Add Codex install docs**

In `README.md` Quick Install, add a Codex section:

```bash
codex plugin marketplace add Agentic-Assets/apple-mail-mcp
codex plugin add apple-mail@apple-mail-mcp
```

For local checkout:

```bash
cd /path/to/apple-mail-mcp
codex plugin marketplace add .
codex plugin add apple-mail@apple-mail-mcp
```

For MCP-only fallback:

```bash
codex mcp add apple-mail -- /bin/bash /path/to/apple-mail-mcp/plugin/start_mcp.sh --draft-safe
```

- [ ] **Step 2: Fix AGENTS navigation**

Update `AGENTS.md` so it references:

- `.agents/plugins/marketplace.json` for Codex marketplace.
- `plugin/.codex-plugin/plugin.json` for Codex plugin metadata.
- Existing nested `CLAUDE.md` files as the authoritative area docs until mirrored `AGENTS.md` files exist.

- [ ] **Step 3: Sync packaging docs**

Update Claude-facing docs to mention four supported install surfaces:

- Claude Code plugin marketplace.
- Claude Desktop/Cowork `.plugin` upload.
- Claude Desktop `.mcpb`.
- Codex Desktop/CLI plugin marketplace via `.agents/plugins/marketplace.json`.

- [ ] **Step 4: Sync test-count claims**

Update stale visible claims to `798 tests + 30 subtests` based on the latest collected pytest count.

### Task 4: Execute Plugin-Dev and Simplifier Passes

**Files:**
- No predetermined file ownership; apply only fixable review findings in touched files.

- [ ] **Step 1: Run plugin-validator style pass**

Delegate a worker/subagent to review the changed manifests and docs as `plugin-dev:plugin-validator`. Required output: blockers, warnings, and exact paths.

- [ ] **Step 2: Run skill-reviewer style pass**

If any `plugin/skills/*/SKILL.md` files are touched, delegate `plugin-dev:skill-reviewer`. If no skill bodies are touched, record "not required" in the progress log.

- [ ] **Step 3: Run code-simplifier style pass**

Delegate simplification review for `tools/validate_manifests.py`, `tools/validate_manifests.sh`, and `tests/test_validate_manifests.py`. Apply only behavior-preserving simplifications.

### Task 5: Verify Release Readiness

**Files:**
- Modify: `tasks/codex-claude-plugin-setup-2026-06-07/progress-log.md`

- [ ] **Step 1: Run focused validation**

Run:

```bash
bash tools/dev-check.sh manifest
.venv/bin/pytest tests/test_validate_manifests.py -q
bash tools/validate-codex-plugin.sh
```

Expected: pass or skip Codex smoke only when the installed Codex CLI does not expose plugin commands.

- [ ] **Step 2: Run full release gate**

Run:

```bash
bash tools/dev-check.sh release
```

Expected: release gate passes; artifacts are rebuilt and validator reports version `3.6.1`, tools `28`.

- [ ] **Step 3: Record evidence**

Append commands, pass/fail results, and caveats to `progress-log.md`.

- [ ] **Step 4: Final status**

Run:

```bash
git status --short
git diff --stat
```

Expected: only scoped packaging/docs/validation/task files changed.
