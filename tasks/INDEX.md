# Tasks Index

Navigation hub for cross-session planning. **Start at [`todo.md`](todo.md)** for the current state and next action.

**Agents:** placement rules are mandatory — read [`CLAUDE.md`](CLAUDE.md) § Agent requirements before creating or moving files here. Local gates enforce layout via `tools/validators/validate_tasks_layout.py`.

## Layout

| Folder | Role |
|--------|------|
| [`todo.md`](todo.md) | Tiny active pointer (current state, open lanes, roadmap link) |
| [`active/`](active/) | Open workstreams from the last ~30 days |
| [`reference/`](reference/) | Durable specs, goals, baselines, and the roadmap |
| [`archive/`](archive/) | Shipped, superseded, or resolved artifacts (do not edit for current work) |

## Active workstreams

| Folder | Purpose | Status |
|--------|---------|--------|
| [`active/native-reply/`](active/native-reply/) | Native-format reply drafts (v3.8.0 ship + live TO-TEST) | Shipped; live verification pending (needs Cayman at the machine) |
| [`active/id-first-search-retirement/`](active/id-first-search-retirement/) | v4 fuzzy-selector retirement, metadata-index spike, `allow_filter_scan` decision | Decision brief awaiting sign-off; follow-up branches not started |
| [`active/v4-performance-consolidation-2026-05-27/`](active/v4-performance-consolidation-2026-05-27/) | v4 perf, FTS, strict-gate | Module split shipped (v3.9.1); perf/FTS stalled since 2026-05-27; confirm resume vs archive |
| [`active/branch-review-v3.11.3/`](active/branch-review-v3.11.3/) | xhigh code review of the v3.11.3 branch vs main: 10-finder synthesis, fix plan, forward queue | Fixes in progress on the branch; archive with the branch closeout |
| [`active/v3.11.5-consolidated-release/`](active/v3.11.5-consolidated-release/) | Four-branch consolidation, cross-client marketplace hardening, and local release proof | Merged and tagged; central marketplace admission superseded by v3.11.6 |
| [`active/v3.11.6-cursor-adapter/`](active/v3.11.6-cursor-adapter/) | Explicit Cursor plugin-root launcher and host-specific validator | Shipped on `main` in v3.11.6; local 41-tool Cursor Agent acceptance passed; marketplace/UI admission unverified |
| [`active/central-marketplace-source-contract/`](active/central-marketplace-source-contract/) | Source payload, identity, signed-tag, and local-gate contract for the shared multi-plugin marketplace | Implementation in progress on `chore/central-marketplace-source-contract` |

## Reference

| File | Purpose |
|------|---------|
| [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md) | Forward roadmap: new tools, skills, enhancements, hardening backlog, documented macOS refusals |
| [`reference/id-first-refactor-spec.md`](reference/id-first-refactor-spec.md) | Shipped ID-first mutations + `allow_filter_scan` gate (v3.7.0) |
| [`reference/phase-3-annotation-matrix.md`](reference/phase-3-annotation-matrix.md) | Canonical tool-annotation matrix (`server.py` `ToolAnnotations` presets) |
| [`reference/apple-mail-plugin-robustness-goal-2026-05-22.md`](reference/apple-mail-plugin-robustness-goal-2026-05-22.md) | Whole-plugin robustness goal and completion contract |
| [`reference/robustness-backlog-2026-05-22.md`](reference/robustness-backlog-2026-05-22.md) | Robustness backlog sidecar (Phases 1-4 shipped; Deferred items carried in the roadmap) |
| [`reference/phase-plan-3.1.7.md`](reference/phase-plan-3.1.7.md) | Historical release sequencing after 3.1.6; verify against current source |
| [`reference/live-test-baseline-2026-05-21.md`](reference/live-test-baseline-2026-05-21.md) | Live perf baseline (production vs light account) |
| [`reference/mcp-mailbox-timeout-audit-2026-05-22.md`](reference/mcp-mailbox-timeout-audit-2026-05-22.md) | Timeout audit reference |

## Archive

See [`archive/README.md`](archive/README.md).

| Bucket | Contents |
|--------|----------|
| [`archive/2026-05-21/`](archive/2026-05-21/) | Shipped 3.1.6 audit and planning artifacts |
| [`archive/2026-05/`](archive/2026-05/) | May workstreams (whose-elimination, robustness audits, scalability hardening) |
| [`archive/2026-06/shipped/`](archive/2026-06/shipped/) | Shipped June workstreams (Codex plugin setup, MCP registration incident, doc cleanup, agent-guidance audit, draft-verification simplification) |
| [`archive/2026-06/issues/`](archive/2026-06/issues/) | Resolved June issue trackers and investigation notes |
| [`archive/2026-07/shipped/`](archive/2026-07/shipped/) | Apple Calendar surface (v3.10.0), manifest-release-hardening (parked), marketplace offline release candidate (v3.11.3), and Cursor marketplace source candidate (v3.11.4) |
| [`archive/2026-07/shipped/agentic-1277-compose-draft-verification/`](archive/2026-07/shipped/agentic-1277-compose-draft-verification/) | Compose-smoke identity verification and bounded reply-state performance hardening, integrated for v3.11.5 |
| [`archive/2026-07/shipped/reply-state-annotation/`](archive/2026-07/shipped/reply-state-annotation/) | Automatic reply-state annotation, shipped in v3.11.0 (PR #73) |
| [`archive/2026-07/shipped/agentic-1214-reply-fixes/`](archive/2026-07/shipped/agentic-1214-reply-fixes/) | Native-reply hardening, shipped in v3.11.2 (PR #75) |
