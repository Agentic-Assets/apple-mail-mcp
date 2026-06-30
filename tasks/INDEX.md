# Tasks Index

Navigation hub for cross-session planning. **Start at [`todo.md`](todo.md)** for the current branch and next action.

**Agents:** placement rules are mandatory — read [`CLAUDE.md`](CLAUDE.md) § Agent requirements before creating or moving files here. CI enforces layout via `tools/validate_tasks_layout.py`.

## Layout

| Folder | Role |
|--------|------|
| [`todo.md`](todo.md) | Tiny active pointer (branch, workstream, next action, blockers) |
| [`active/`](active/) | Open workstreams from the last ~30 days |
| [`reference/`](reference/) | Durable specs, goals, and baselines still cited by code/docs |
| [`archive/`](archive/) | Shipped, superseded, or resolved artifacts (do not edit for current work) |

## Active workstreams

| Folder | Purpose | Status |
|--------|---------|--------|
| [`active/native-reply/`](active/native-reply/) | Native-format reply drafts (v3.8.0 ship + live TO-TEST) | Shipped; live verification pending |
| [`active/id-first-search-retirement/`](active/id-first-search-retirement/) | v4 selector retirement, metadata-index spike, decision briefs | Planning / next major lane |
| [`active/agent-guidance-audit/`](active/agent-guidance-audit/) | Skills, docs, and training-surface consistency audit | In progress |
| [`active/draft-verification-simplification/`](active/draft-verification-simplification/) | Compose/manage_drafts decomposition recommendations | Open research |
| [`active/v4-performance-consolidation-2026-05-27/`](active/v4-performance-consolidation-2026-05-27/) | v4 perf, FTS, strict-gate, module decomposition | Open; strict package gate green |

## Reference

| File | Purpose |
|------|---------|
| [`reference/id-first-refactor-spec.md`](reference/id-first-refactor-spec.md) | Shipped ID-first mutations + `allow_filter_scan` gate (v3.7.0) |
| [`reference/apple-mail-plugin-robustness-goal-2026-05-22.md`](reference/apple-mail-plugin-robustness-goal-2026-05-22.md) | Whole-plugin robustness goal and completion contract |
| [`reference/robustness-backlog-2026-05-22.md`](reference/robustness-backlog-2026-05-22.md) | Detailed robustness backlog sidecar |
| [`reference/phase-plan-3.1.7.md`](reference/phase-plan-3.1.7.md) | Historical release sequencing after 3.1.6; verify against current source |
| [`reference/live-test-baseline-2026-05-21.md`](reference/live-test-baseline-2026-05-21.md) | Live perf baseline (production vs light account) |
| [`reference/mcp-mailbox-timeout-audit-2026-05-22.md`](reference/mcp-mailbox-timeout-audit-2026-05-22.md) | Timeout audit reference |

## Archive

See [`archive/README.md`](archive/README.md).

| Bucket | Contents |
|--------|----------|
| [`archive/2026-05-21/`](archive/2026-05-21/) | Shipped 3.1.6 audit and planning artifacts |
| [`archive/2026-05/`](archive/2026-05/) | May workstreams (whose-elimination, robustness audits, scalability hardening) |
| [`archive/2026-06/shipped/`](archive/2026-06/shipped/) | Shipped June workstreams (Codex plugin setup, MCP registration incident, doc cleanup) |
| [`archive/2026-06/issues/`](archive/2026-06/issues/) | Resolved June issue trackers and investigation notes |
