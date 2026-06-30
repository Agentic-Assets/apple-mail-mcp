# tasks/archive/

Completed, superseded, or resolved planning artifacts. **Do not use these for current work.**

Start at [`../todo.md`](../todo.md) and [`../INDEX.md`](../INDEX.md).

## Archive buckets

| Path | What it was |
|------|-------------|
| [`2026-05-21/`](2026-05-21/) | 3.1.6 audit, phase plan, plan review, CLI report, annotation matrix — shipped through commit `f0ca077` |
| [`2026-05/`](2026-05/) | May workstreams: whose-elimination (v3.2.0 capability tokens), robustness completion audits, scalability hardening notes |
| [`2026-06/shipped/`](2026-06/shipped/) | Shipped June workstreams: Codex plugin setup, MCP tool-registration incident, doc-cleanup branch |
| [`2026-06/issues/`](2026-06/issues/) | Resolved June issue trackers (draft verification, reply body, manage_drafts timeouts, etc.) |

## When archiving

1. Move the workstream folder or file under `archive/YYYY-MM/` (month bucket) or `archive/YYYY-MM-DD/` (single-date drop).
2. Add one line to the table above.
3. Remove or update any `active/` or `reference/` pointers in [`../INDEX.md`](../INDEX.md).

**Rule of thumb:** archive when shipped/superseded, or when the artifact is more than ~30 days old and no longer the active pointer in `todo.md`.
