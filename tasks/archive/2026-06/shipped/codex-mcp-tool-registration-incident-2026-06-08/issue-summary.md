# Codex MCP Tool Registration Incident - 2026-06-08

## Why this exists

During a TU email triage session, Codex had the Apple Mail MCP plugin skills loaded, but the Apple Mail MCP tools were not available in the active tool surface. The agent fell back to AppleScript and created reply-shaped drafts outside the plugin's `reply_to_email` tool. Those drafts did not reliably preserve the prior conversation content the way the plugin is expected to.

This was not a `reply_to_email` behavior bug. The repository already makes the intended contract clear:

- `compose_email` is standalone-only and warns callers to use `reply_to_email(message_id=...)` for responses.
- `reply_to_email` now uses Mail's native reply composer, so Mail builds the quoted prior conversation and the tool verifies the saved draft before reporting success.
- The operator skill says reply drafts must use `reply_to_email(message_id=...)`, with already-replied checks first.

The failure was that Codex reported the plugin as installed and enabled, while the active session still exposed zero `mcp__apple-mail__*` tools.

## Observed evidence

- Marketplace path was correct:
  `/Users/caymanseagraves/.codex/.tmp/marketplaces/apple-mail-mcp/.agents/plugins/marketplace.json`
- `codex plugin list` reported:
  `apple-mail@apple-mail-mcp  installed, enabled  3.6.1`
- `codex mcp get apple-mail` reported:
  `command: /bin/bash`
  `args: ${CLAUDE_PLUGIN_ROOT}/start_mcp.sh --draft-safe`
- Running that literal command failed because `${CLAUDE_PLUGIN_ROOT}` was not expanded as an argv element.
- Running the installed plugin by absolute path succeeded:
  `/bin/bash /Users/caymanseagraves/.codex/.tmp/marketplaces/apple-mail-mcp/plugin/start_mcp.sh --draft-safe`
- A direct MCP client handshake against that absolute-path launch exposed all 28 tools, including `reply_to_email`.
- `tool_search` for `mcp__apple-mail`, `reply_to_email`, and `list_accounts` still returned zero Apple Mail MCP tools in the active Codex session.
- Older Codex logs also contained parse warnings against `plugin/.claude-plugin/plugin.json`: Codex expected a string where the Claude manifest has an inline `mcpServers` map. That may be stale, but it is worth testing because this repo intentionally ships both Claude and Codex manifests in one `plugin/` runtime.

## Current hypothesis

The server code, requirements, and `start_mcp.sh` launcher are healthy. The failing boundary is Codex plugin-to-MCP registration.

Most likely cause: Codex 0.133.0 materializes the plugin MCP config with a literal `${CLAUDE_PLUGIN_ROOT}/start_mcp.sh` argument and does not expand it before launching the stdio server. That leaves the plugin installed in the marketplace sense, but the MCP server never starts, so no Apple Mail tools are registered.

Second possible contributor: Codex may still inspect `.claude-plugin/plugin.json` in some local or cached paths and reject the Claude-style inline `mcpServers` object. Current Codex install appears to use `.codex-plugin/plugin.json`, but this needs a regression check because logs showed the parse warning repeatedly in earlier runs.

## What needs to be fixed

### 1. Add a Codex runtime smoke, not just an install smoke

`tools/validate-codex-plugin.sh` currently proves that Codex can add the marketplace, install the plugin, and list `apple-mail@apple-mail-mcp`. That is necessary but insufficient.

Add a runtime smoke that fails unless the installed plugin can expose Apple Mail MCP tools. At minimum it should:

1. Install the plugin into a temporary `CODEX_HOME`.
2. Read or query the installed `apple-mail` MCP registration.
3. Launch the registered server command in the same form Codex will use, or otherwise verify Codex has resolved plugin-root variables before launch.
4. Perform an MCP initialize/list-tools handshake.
5. Assert that the tool list includes `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview`.

This smoke should fail for the exact class of incident seen on 2026-06-08: installed and enabled, but no Apple Mail MCP tools available.

### 2. Decide the Codex-safe MCP launcher contract

The current Codex MCP config is:

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/bin/bash",
      "args": ["${CLAUDE_PLUGIN_ROOT}/start_mcp.sh", "--draft-safe"]
    }
  }
}
```

That is portable if the host expands `${CLAUDE_PLUGIN_ROOT}` inside `args`. It is unsafe if Codex treats `args` literally.

Test these alternatives in a temporary Codex plugin install before changing the manifest:

- `command: "${CLAUDE_PLUGIN_ROOT}/start_mcp.sh", args: ["--draft-safe"]`
- `command: "/bin/bash", args: ["./start_mcp.sh", "--draft-safe"], cwd: "${CLAUDE_PLUGIN_ROOT}"`
- a Codex-specific installed wrapper path if Codex supports plugin-root interpolation only in path fields
- fallback documentation plus automated MCP-only registration using an absolute path if Codex cannot support plugin-relative stdio launch today

Do not change the validator to bless a new shape until the runtime smoke proves that shape produces tools in a fresh Codex session.

### 3. Split Claude and Codex manifest assumptions in validation

`tools/validate_manifests.py` currently enforces `${CLAUDE_PLUGIN_ROOT}/start_mcp.sh` for both Claude and Codex surfaces. That may encode the bug.

Keep Claude validation strict for `plugin/.claude-plugin/plugin.json`, because Claude Code's plugin root variable is the expected portable contract there.

For Codex, validate the contract Codex actually honors. If Codex requires a different variable, working directory, or installed-path convention, encode that separately in `_check_codex_plugin_contract` and update `tests/test_validate_manifests.py`.

### 4. Make reply-draft safety impossible to miss

The repo already has the right tool behavior. The incident still happened because the tools were absent and the agent used a fallback.

Add a troubleshooting note to the Apple Mail operator and/or email drafting skill:

- If `mcp__apple-mail__*` tools are absent, do not create reply drafts with generic AppleScript or standalone compose tools.
- First fix MCP registration or use the documented MCP-only absolute-path fallback.
- Reply drafting requires `reply_to_email(message_id=...)` unless the user explicitly approves a degraded fallback.

### 5. Keep the MCP-only fallback documented

The README already documents:

```bash
codex mcp add apple-mail -- /bin/bash /path/to/apple-mail-mcp/plugin/start_mcp.sh --draft-safe
```

Keep it, but clarify that this is not merely an alternate install path. It is the recovery path when Codex plugin marketplace install succeeds but the Apple Mail MCP tools are not exposed.

## What needs to be tested

### Required automated tests

- `tests/test_validate_manifests.py`
  - Codex manifest contract accepts the proven Codex-safe launcher shape.
  - Codex manifest contract rejects the known-bad shape if Codex cannot expand `${CLAUDE_PLUGIN_ROOT}` in `args`.
  - Claude manifest contract remains compatible with Claude Code.
- `tools/validate-codex-plugin.sh`
  - Installs the plugin into temporary `CODEX_HOME`.
  - Proves MCP tool registration, not only `codex plugin list`.
  - Fails if `reply_to_email` is not discoverable via MCP list-tools.
- A small direct MCP handshake test
  - Launches `plugin/start_mcp.sh --draft-safe`.
  - Calls initialize/list-tools.
  - Asserts the 28-tool surface includes reply and draft tools.

### Required manual or live checks before release

- Fresh Codex Desktop session:
  - Install `apple-mail@apple-mail-mcp` from `.agents/plugins/marketplace.json`.
  - Confirm `mcp__apple-mail__*` tools are exposed to the agent.
  - Confirm `reply_to_email` can create a draft reply with quoted prior content.
- Fresh Codex CLI session:
  - Repeat install and tool exposure check.
  - Confirm `codex mcp get apple-mail` does not show a launcher that will fail when executed literally.
- Claude Code:
  - Install `apple-mail@apple-mail-mcp` from `.claude-plugin/marketplace.json`.
  - Confirm Claude MCP tools are exposed.
  - Confirm `${CLAUDE_PLUGIN_ROOT}` remains valid for Claude.
- Claude Desktop / Cowork upload:
  - Build `apple-mail.plugin`.
  - Upload and confirm the MCP starts in draft-safe mode.
- MCPB:
  - Build and unpack/validate if `mcpb` is installed.

## Anything else to clean up

- Remove or isolate any stale local install/cache state before claiming a fix. The same machine can have marketplace, plugin cache, manual MCP config, and desktop session state all disagreeing.
- Add a clear "how to know it worked" section to the README:
  - `codex plugin list` showing installed is not enough.
  - The pass condition is that an agent can see Apple Mail MCP tools, and an MCP list-tools handshake returns `reply_to_email`.
- Consider adding a repo issue template or troubleshooting entry for "installed but tools absent."
- Check whether older Codex versions such as 0.133.0 differ from newer Codex versions in plugin-root variable expansion. If a Codex upgrade fixes the behavior, document the minimum working version and keep the runtime smoke to catch regressions.

## Immediate user-facing guidance

Until this is fixed and verified, treat Codex plugin install as incomplete unless `mcp__apple-mail__*` tools are visible in the active session. For reply drafting, do not use manual AppleScript fallback unless Cayman explicitly approves a degraded draft. The correct path is to restore MCP tool exposure and then use `reply_to_email(message_id=..., mode="open" or "draft")`.
