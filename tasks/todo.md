# Active Pointer: apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; CI enforces).

**Current branch:** `fix/agentic-1214-reply-body-truncation` (off `main` @ v3.10.1, merged up to `main` @ v3.11.0).

**Current workstream:** [`tasks/active/agentic-1214-reply-fixes/`](active/agentic-1214-reply-fixes/). AGENTIC-1214 field report: native `reply_to_email` drafts truncated long bodies (~320-480 chars) and typed some short bodies in ALL CAPS, `manage_drafts(action="create", in_reply_to=...)` silently dropped threading, and Exchange Drafts ids drift across `action="list"` calls with no writes in between. Orchestration record: [`orchestration-2026-07-10.md`](active/agentic-1214-reply-fixes/orchestration-2026-07-10.md); implementation plan: [`plan-2026-07-10.md`](active/agentic-1214-reply-fixes/plan-2026-07-10.md); recon/design reports under [`reports/`](active/agentic-1214-reply-fixes/reports/).

**Implemented on this branch:** chunked, focus-guarded typed insertion for the native reply body (new `typing_scripts.py`, `TYPING_CHUNK_SIZE`/`TYPING_INTER_CHUNK_DELAY` constants) replacing the single-keystroke insert that dropped long-body tails and could leak ALL-CAPS shift state; full-body post-save verification above the quote; a fail-closed persisted-Drafts identity for native draft/open replies. The identity is emitted only when a complete bounded before/after snapshot gains exactly one new RFC `Message-ID` whose `In-Reply-To` exactly links to the source. The verifier and delete path revalidate both headers at the numeric Drafts ID, so caps, indexing delay, ambiguity, malformed headers, and ID drift all disable automatic cleanup. Reliable quote-boundary handling for `verify_draft(expected_body_contains)` ignores authored `wrote:` prose and checks the whole preview when no boundary is available; `REPLY_BODY_MISMATCH` and `REPLY_BODY_TYPING_INTERRUPTED` remain actionable structured errors; `manage_drafts(action="create", in_reply_to=...)` refuses with `CREATE_CANNOT_THREAD` instead of silently dropping threading; and `manage_drafts` docstrings document Exchange draft-id instability. Skills (`email-drafting`, `apple-mail-operator`), `docs/CLAUDE-conventions.md`, `README.md`, and `CHANGELOG.md` now reflect the final contract.

**Next action:** retain the shipped proof in the release closeout after final focused and release gates plus a draft-mode live-verification pass. No PR is authorized for this repo; merge needs Cayman's explicit phrase.

**Main state:** `main` @ **v3.11.0** (automatic reply-state annotation, PR #73; tasks roadmap refresh, PR #74; v3.10.1 archive human-sender screen, PR #72).

**Roadmap:** [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md): new tools, new skills, enhancements, hardening backlog, and documented macOS refusals. Next three builds: port `get_email_source` forward, add junk + colored-flag actions to `update_email_status`, then the typed-`AppleScriptError` error-contract pass.

## Open lanes

**Live verification, needs Cayman.** [`active/native-reply/`](active/native-reply/): remaining native-reply TO-TEST items that cannot be mocked. Send a saved native draft to self and confirm the logo survives the actual SEND; live-exercise attachments + native reply, `reply_to_all` native on a real multi-recipient thread, and `GUARD_ABORT` under real focus contention. See [`native-reply-handoff-2026-06-30.md`](active/native-reply/native-reply-handoff-2026-06-30.md).

**Planning, awaiting sign-off.** [`active/id-first-search-retirement/`](active/id-first-search-retirement/): v4 fuzzy-selector retirement. Decision brief ready for maintainer sign-off; `mailbox="All"` opt-in, v4 legacy-selector removal, and metadata-index live measurement not started. This also owns the open `allow_filter_scan` product decision for `move_email` / `update_email_status` / `manage_trash`. Also the home for the AGENTIC-1192 Archive-reply gap (`reply_to_email` lookup is Inbox-only; replying to a message moved to Archive returns not-found).

**Stale, confirm resume-vs-archive.** [`active/v4-performance-consolidation-2026-05-27/`](active/v4-performance-consolidation-2026-05-27/): module-split work shipped (v3.9.1); perf/FTS lanes have not moved since 2026-05-27. Over the 30-day archive threshold; kept active pending a resume-or-archive decision.

**Other open branch (no task folder).** `fix/github-issues-mcp-hardening-20260617` holds an unmerged `get_email_source` tool (raw RFC822/MIME by id). The roadmap flags porting it forward as the top next build; the branch itself can be dropped once ported.

**Caveats (carried, not blockers):**
- Native reply needs Mail window focus + Accessibility permission (`native_format=False` avoids it; returns `REPLY_WINDOW_FOCUS_FAILED` when focus cannot be acquired).
- Logo not repainted in the reopened draft editor = native Mail behavior (not our bug); SEND-level confirmation still pending.

**Recently shipped** (detail under [`archive/`](archive/)): v3.11.0 automatic reply-state annotation, v3.10.1 archive human-sender screen, v3.10.0 Apple Calendar surface, v3.9.4 skill-example accuracy, v3.9.1 module line-budget splits, v3.8.0 native-format reply drafts.
