# docs/ — documentation index

Human- and agent-facing docs that survive outside the codebase. Plugin skills and root `README.md` cover user install; this folder covers **agent workflows** and **deep engineering conventions**.

## Agent orchestration

- Use **subagents** for research **and** implementation when the host exposes them and the task lane permits them.
- Use **plugin-dev experts** (`plugin-dev:plugin-validator`, `plugin-dev:skill-reviewer`, and the `plugin-dev:mcp-integration` / `plugin-dev:plugin-structure` skills) whenever available and docs or conventions touch manifests, skills, or MCP integration. If unavailable, say so and run local validation.

## Files

| Doc | Audience | Purpose |
|-----|----------|---------|
| [`AGENT_LIVE_TESTING.md`](AGENT_LIVE_TESTING.md) | Coding agents, maintainers | Live Mail verification via repo `.venv/bin/apple-mail` CLI |
| [`CLAUDE-conventions.md`](CLAUDE-conventions.md) | All agents editing Python/tools/skills | Deep rules: perf, escaping, versioning, skill authoring, plugin-dev agents, **distribution channels** (four install surfaces) |

## Who reads what

**Implementing or changing MCP tools** → start with root [`CLAUDE.md`](../CLAUDE.md) (architecture), then [`CLAUDE-conventions.md`](CLAUDE-conventions.md) (anti-patterns, **module line budget**). Run mocked tests per [`tests/CLAUDE.md`](../tests/CLAUDE.md).

**Verifying against real Mail.app** → [`AGENT_LIVE_TESTING.md`](AGENT_LIVE_TESTING.md): setup, permissions, `quick-check` / `perf-test` batteries, safe probes, MCP env vars (`DEFAULT_MAIL_ACCOUNT`, `DEFAULT_MAIL_SIGNATURE`, `USER_EMAIL_PREFERENCES`).

**Plugin shell / manifests / skills** → [`plugin/docs/CLAUDE.md`](../plugin/docs/CLAUDE.md), [`.claude-plugin/CLAUDE.md`](../.claude-plugin/CLAUDE.md), [`apple-mail-mcpb/CLAUDE.md`](../apple-mail-mcpb/CLAUDE.md), [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md). Codex routing lives in [`../.agents/plugins/marketplace.json`](../.agents/plugins/marketplace.json), [`../plugin/.codex-plugin/plugin.json`](../plugin/.codex-plugin/plugin.json), and [`../plugin/.mcp.json`](../plugin/.mcp.json). Run `plugin-dev:plugin-validator` after manifest edits; `plugin-dev:skill-reviewer` after skill edits.

**Planning / backlog** → [`tasks/CLAUDE.md`](../tasks/CLAUDE.md) (read § Agent requirements) and [`tasks/todo.md`](../tasks/todo.md).

## Plugin workflow skills (Claude Code and Codex)

Nine skills ship under [`plugin/skills/`](../plugin/skills/) and auto-load with Claude Code and Codex plugin installs. They teach **when** and **how** to call the 31 MCP tools — they do not implement tool logic.

| Skill | Use when the user wants… |
|-------|---------------------------|
| [`apple-mail-operator`](../plugin/skills/apple-mail-operator/) | MCP setup, accounts/mailboxes, safe read/search, performance troubleshooting |
| [`inbox-triage`](../plugin/skills/inbox-triage/) | 5–10 min read-first scan (needs-response, awaiting-reply) |
| [`email-management`](../plugin/skills/email-management/) | Sustained Inbox Zero habits and cross-cutting programs |
| [`mailbox-taxonomy`](../plugin/skills/mailbox-taxonomy/) | Folder strategy, noise diagnosis, structural `create_mailbox` |
| [`email-archive-cleanup`](../plugin/skills/email-archive-cleanup/) | Staged archive / bulk move / trash with dry runs + exports |
| [`mail-rules-advisor`](../plugin/skills/mail-rules-advisor/) | Mail filter / rule **proposals** (manual apply in Mail.app — no rule API) |
| [`email-drafting`](../plugin/skills/email-drafting/) | Compose, reply, forward, rich drafts (`--draft-safe` aware) |
| [`email-style-profile`](../plugin/skills/email-style-profile/) | Voice from Sent mail + `USER_EMAIL_PREFERENCES` before drafting |
| [`email-attachments`](../plugin/skills/email-attachments/) | List and save attachments with path safety |

**Routing index:** [`plugin/skills/CLAUDE.md`](../plugin/skills/CLAUDE.md) (sibling cheat sheet). **Authoring rules:** [`CLAUDE-conventions.md`](CLAUDE-conventions.md) § Skill authoring. **User install blurb:** root [`README.md`](../README.md) § Claude Code Skills.

Workflow entry points are skills-only. Do not add or restore legacy slash commands; use `plugin/skills/<name>/SKILL.md`.

## AGENT_LIVE_TESTING.md structure

1. Setup (venv, `DEFAULT_MAIL_ACCOUNT`)
2. macOS permissions (Automation, Mail Data Access)
3. Safe commands — batteries (`quick-check`, `perf-test`, `smoke-test`) and individual probes
4. Post-edit workflow (fast loop → full perf gate + thresholds)
5. Unit tests vs live Mail (CI = mocked only)
6. MCP config for agents (`mcp-config --repo`, draft-safe)

## CI vs live

CI never touches Mail.app. Manifest validation, **module line budget** report, and pytest ([`tools/CLAUDE.md`](../tools/CLAUDE.md)). Live testing is manual on macOS after local changes.

## Related

- User-facing install: root [`README.md`](../README.md)
- Cross-session backlog: [`tasks/todo.md`](../tasks/todo.md)
- Active robustness goal: [`tasks/reference/apple-mail-plugin-robustness-goal-2026-05-22.md`](../tasks/reference/apple-mail-plugin-robustness-goal-2026-05-22.md) · historical phase sequencing: [`tasks/reference/phase-plan-3.1.7.md`](../tasks/reference/phase-plan-3.1.7.md) · live baseline: [`tasks/reference/live-test-baseline-2026-05-21.md`](../tasks/reference/live-test-baseline-2026-05-21.md)
