# Active Pointer — apple-mail-mcp

**Branch:** `main` (3.5.0 field-report hardening staged, not yet committed)

**Active workstream:** v3.5.0 live-field-report hardening — draft-QA fixes from `LIVE_FIELD_REPORT_2026-06-04.md`. Duplicate-draft persist, Exchange search-hang/silent-0-row regressions, `get_email_by_id` threading+recipient metadata, `search_emails(mailboxes=[...])`, triageable `manage_drafts` list + `cleanup_empty`, plus CLI parity (`search --mailboxes`, `drafts list --hide-empty`, `drafts cleanup-empty`).

**Next action:** commit + push the 3.5.0 change set on a `fix/*` branch and open a PR (awaiting "Cayman approved this merge" before any merge to `main`).

**Latest verification (2026-06-05):** all draft/search/CLI changes live-verified against the 24K TU Exchange inbox (one reply → exactly one threaded draft; INBOX search returns rows again; targeted `mailboxes=[...]` and `cleanup_empty` dry-run confirmed). Rendered AppleScript for the three new scripts compiles via `osacompile`. `bash tools/dev-check.sh release` green; `validate_manifests: OK (version=3.5.0, tools=28)`; full pytest suite passes.

**Blockers / caveats:** deferred field-report items by design — #4 GUI `delay`→polling/window-by-reference (highest regression risk), #8 compose dedup guard (#1 already cures observed dupes), #9 preview split (cosmetic). Per-message recipient resolution stays out of bulk `search_emails` (hangs on Exchange); recipients come from `get_email_by_id` / drafts list. See project memory `exchange-applescript-footguns.md`.
