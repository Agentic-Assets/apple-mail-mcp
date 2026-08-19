# tasks/archive/

Completed, superseded, or resolved planning artifacts. **Do not use these for current work.**

Start at [`../todo.md`](../todo.md) and [`../INDEX.md`](../INDEX.md).

## Archive buckets

| Path | What it was |
|------|-------------|
| [`2026-05-21/`](2026-05-21/) | 3.1.6 audit, phase plan, plan review, CLI report, annotation matrix — shipped through commit `f0ca077` |
| [`2026-05/`](2026-05/) | May workstreams: whose-elimination (v3.2.0 capability tokens), robustness completion audits, scalability hardening notes |
| [`2026-06/shipped/`](2026-06/shipped/) | Shipped June workstreams: Codex plugin setup, MCP tool-registration incident, doc-cleanup branch, agent-guidance audit (complete 2026-06-30), draft-verification simplification (helper module + manage_drafts builders + `draft-verify-smoke` CLI) |
| [`2026-06/issues/`](2026-06/issues/) | Resolved June issue trackers (draft verification, reply body, manage_drafts timeouts, etc.) |
| [`2026-07/shipped/`](2026-07/shipped/) | Apple Calendar tool surface (v3.10.0: 10 tools, hybrid AppleScript+EventKit engine, 2 skills, research/plan/9-phase reports, merged PR #70 and #71); manifest-release-hardening (v3.9.1-era version-surface hardening, parked per 2026-07-09 branch cleanup); marketplace offline release candidate (v3.11.3); Cursor marketplace source candidate (v3.11.4) |
| [`2026-07/shipped/reply-state-annotation/`](2026-07/shipped/reply-state-annotation/) | Automatic reply-state annotation, shipped in v3.11.0 (PR #73) |
| [`2026-07/shipped/agentic-1214-reply-fixes/`](2026-07/shipped/agentic-1214-reply-fixes/) | Native-reply hardening, shipped in v3.11.2 (PR #75) |
| [`2026-07/shipped/agentic-1277-compose-draft-verification/`](2026-07/shipped/agentic-1277-compose-draft-verification/) | AGENTIC-1277 exact-recipient smoke cleanup and AGENTIC-1191 bounded Drafts/perf hardening, integrated for v3.11.5 |
| [`2026-07/shipped/branch-review-v3.11.3/`](2026-07/shipped/branch-review-v3.11.3/) | v3.11.3 branch review and completed fix plan; its deferred product decisions remain historical forward-queue context |
| [`2026-07/shipped/v3.11.5-consolidated-release/`](2026-07/shipped/v3.11.5-consolidated-release/) | v3.11.5 consolidation, merged and tagged; successor distribution evidence is tracked in the active v3.11.6 lane |
| [`2026-08/shipped/`](2026-08/shipped/) | The three 2026-08-19 merges to `main`: identity gate + `search_emails` date window (PR #90, `dc56b3c`), trash-safety and zero-bounds (PR #91, `ab04304`), silent error channels + bare-`try` lint ratchet (PR #92, `b30d9c4`). Shipped on 3.11.7, which is still untagged |

## When archiving

1. Move the workstream folder or file under `archive/YYYY-MM/` (month bucket) or `archive/YYYY-MM-DD/` (single-date drop).
2. Add one line to the table above.
3. Remove or update any `active/` or `reference/` pointers in [`../INDEX.md`](../INDEX.md).

**Rule of thumb:** archive when shipped/superseded, or when the artifact is more than ~30 days old and no longer the active pointer in `todo.md`.
