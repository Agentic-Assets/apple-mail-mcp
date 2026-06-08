# Active Pointer — apple-mail-mcp

**Branch:** `main` working tree.

**Active workstream:** Codex + Claude plugin setup hardening. Goal: keep existing Claude Code/Cowork/MCPB paths working while adding a real Codex plugin surface (`plugin/.codex-plugin/plugin.json`, `plugin/.mcp.json`, `.agents/plugins/marketplace.json`), easy Codex install commands, and validator coverage.

**Plan:** [`tasks/codex-claude-plugin-setup-2026-06-07/phase-plan.md`](codex-claude-plugin-setup-2026-06-07/phase-plan.md)

**Next action:** review final diff, then commit/push when requested.

**Latest verification (2026-06-08):** Native reply follow-up is fixed in the working tree: live `reply_to_email(message_id="80833", mode="draft")` created a Mail-native reply draft with prior messages included automatically, bounded `manage_drafts(action="list", subject_contains=...)` found it, bounded inspection confirmed Mail's native quote header, and uniquely marked smoke drafts were deleted/verified gone. `bash tools/dev-check.sh release` passed at version `3.6.1`; `bash tools/validate-codex-plugin.sh` passed after installing `apple-mail@apple-mail-mcp` into a temporary `CODEX_HOME`, launching the registered Codex MCP command, and proving `reply_to_email`, `compose_email`, `manage_drafts`, `list_accounts`, and `get_inbox_overview` via MCP `list_tools`.

**Blockers / caveats:** `mcpb` CLI is not installed locally, so the release script skipped optional `mcpb unpack + validate`; structural MCPB checks still passed through `tools/validate_manifests.py`. Fresh Codex Desktop/Claude Desktop UI-session confirmation is still manual because this pass verified CLI temp install plus MCP handshake rather than restarting desktop clients.
