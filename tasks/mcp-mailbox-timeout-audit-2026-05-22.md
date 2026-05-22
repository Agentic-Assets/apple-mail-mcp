# MCP Mailbox Timeout Audit - 2026-05-22

## Goal

Make the Apple Mail MCP plugin reliable on large real-world mailboxes by identifying and eliminating code paths, MCP tool behavior, wrapper flows, tests, and validation checks that can accidentally trigger broad inbox/mailbox scans, long synchronous Mail.app operations, or timeout-prone behavior.

The primary live validation account for this work is `TU - Cayman`, because it is the larger, more realistic mailbox where timeout and full-scan issues surface.

## Input Evidence

- `/Users/cayman-mac-mini/.openclaw/tasks/caiyman-orchestrator-report-2026-05-22.md` was referenced as an example report from another workspace agent. In this checkout it is an OpenClaw workspace/orchestration report rather than a technical Apple Mail timeout report, so this branch treats it as context evidence and relies on repo-local findings for the concrete fixes.
- Repo-local evidence came from `tasks/live-test-baseline-2026-05-21.md`, `LIVE_MCP_CLI_TESTING_REPORT_2026-05-21.md`, subagent scans, and live TU - Cayman perf runs.

## Fix Themes

- Scope default dashboard and perf probes to the selected/default account instead of silently fanning out across all accounts.
- Make compact overview flags control AppleScript work, not only output rendering.
- Keep mailbox counts opt-in in the repo CLI.
- Route subject-based attachment/export lookups through bounded search before exact-id operations.
- Keep `timeout=None` from disabling subprocess timeouts.
- Shrink analysis probe caps so the explicit heavy gate remains useful on `TU - Cayman`.

## TU - Cayman Validation

- `quick-check --account "TU - Cayman" --json`: pass.
- `perf-test --account "TU - Cayman" --profile production --json`: pass.
- `perf-test --include-analysis --allow-heavy-mail-scan --account "TU - Cayman" --profile production --json`: pass.

