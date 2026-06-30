# Apple Mail Plugin Robustness Codex Goal

- Date: 2026-05-22
- Branch: `feat/apple-mail-plugin-robustness`
- Base: `main` at `43462c056385ef61ce84caf4a7c80fd14e634da3`
- Primary reference: https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex
- Monitoring cadence: 15 minutes

## Approved Goal

`/goal` Make `apple-mail-mcp` feel like a robust, polished, production-ready Apple Mail plugin rather than only a working MCP server. The desired end state is a dependable Claude Code plugin and Claude Desktop MCPB bundle that agents can install, discover, use through skills, call through the CLI/wrapper, validate locally, and operate against large real-world Mail.app accounts without surprise hangs, unsafe defaults, stale manifests, or confusing entry points.

Use the OpenAI Codex Goals guide as the operating model: this is a thread-scoped completion contract, not a vague improvement pass. Keep working until the evidence says the plugin is materially stronger or until a clear blocker prevents further safe progress.

Start from `main` and read the repo instructions first: `CLAUDE.md`, `plugin/docs/CLAUDE.md`, `plugin/apple_mail_mcp/CLAUDE.md`, `plugin/apple_mail_mcp/tools/CLAUDE.md`, `plugin/skills/CLAUDE.md`, `tools/CLAUDE.md`, `apple-mail-mcpb/CLAUDE.md`, `docs/CLAUDE-conventions.md`, `docs/AGENT_LIVE_TESTING.md`, and `tasks/todo.md`. Use subagents for research, implementation, validation, docs, plugin review, and live CLI checks. Use `plugin-dev:plugin-architect` before designing plugin/package changes and `plugin-dev:plugin-validator` before completion. Use skill review where bundled skills are edited.

Investigate the plugin as a whole system: the Python MCP tools, AppleScript execution paths, CLI commands, generated `apple-mail` wrapper, Claude Code plugin manifest, bundled workflow skills, plugin startup script, MCPB bundle, marketplace metadata, tests, validation scripts, and distributable artifacts including `apple-mail-plugin.zip` and `apple-mail-mcp-v3.1.8.mcpb`. Look for inconsistencies, stale claims, missing validation coverage, fragile installation behavior, poor agent guidance, confusing CLI/wrapper parity gaps, broad mailbox scans, silent errors, destructive-action ambiguity, missing docs, and places where the plugin works only because the local checkout is already configured.

Implementation should be evidence-driven. Prefer small durable fixes that make the plugin safer, clearer, faster, and easier for agents to use. Preserve existing public tool names and documented behavior unless the repo evidence shows a change is necessary. Do not add new slash commands; ship workflow entry points as skills only. Keep Mail operations draft-safe by default. Do not send, delete, move, or synchronize real mail unless the action is dry-run or explicitly approved. Expensive mailbox scans must stay bounded or gated behind explicit opt-ins.

Validation must include the repo's mocked gates and distribution gates: `bash tools/validate_manifests.sh`, `.venv/bin/pytest tests/ -q`, wrapper surface checks, plugin manifest validation, MCPB validation/rebuild where applicable, and regeneration/reconstruction of the drop-in plugin zip/bundle artifacts. Where local Mail access is available and safe, run read-only live checks against the realistic account with bounded commands: `quick-check --json`, production `perf-test --profile production --json`, and the explicit heavy analysis gate only when appropriate. Capture exact command results in the final report.

Completion requires concrete evidence: changed code/docs/tests/artifacts where needed, regenerated plugin zip or MCPB artifacts if stale, passing validation output, a concise audit of what was improved, a list of residual risks, and a PR-ready branch. Stop and report instead of guessing if plugin-dev agents are unavailable, live Mail permissions fail, bundle generation depends on unavailable tooling, credentials are missing, or repo instructions conflict.
