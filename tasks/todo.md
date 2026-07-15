# Active Pointer: apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; CI enforces).

**Current branch:** `chore/consolidate-active-branches-20260715` (off `main` @ v3.11.2).

**Most recent workstream:** [`tasks/archive/2026-07/shipped/agentic-1277-compose-draft-verification/`](archive/2026-07/shipped/agentic-1277-compose-draft-verification/). v3.11.3 now verifies the standalone compose smoke against the exact persisted To-recipient set and only performs identity-guarded cleanup. AGENTIC-1191's bounded Inbox path now caps Drafts at 50, reports truncated draft-state knowledge as `null`, and uses the mailbox response envelope for the perf threshold.

**Already shipped:** AGENTIC-1214 merged in v3.11.2. It added chunked, focus-guarded native reply typing; full-body verification above the quote; persisted header-linked Drafts identity; safe artifact cleanup; quote-boundary verification; and the `CREATE_CANNOT_THREAD` refusal for standalone draft creation with `in_reply_to`. Its closeout is archived under [`archive/2026-07/shipped/agentic-1214-reply-fixes/`](archive/2026-07/shipped/agentic-1214-reply-fixes/).

**Next action:** review the combined v3.11.5 PR, then merge only after Cayman's literal approval phrase. Closeout: [`active/v3.11.5-consolidated-release/closeout-2026-07-15.md`](active/v3.11.5-consolidated-release/closeout-2026-07-15.md). Forward queue: [`active/v3.11.5-consolidated-release/forward-queue-2026-07-15.md`](active/v3.11.5-consolidated-release/forward-queue-2026-07-15.md). After merge, create the signed annotated source tag and run the central marketplace promotion and admission workflow. Keep AGENTIC-781's human-operated native-reply checks open; AGENTIC-1192, AGENTIC-995, and AGENTIC-996 remain distinct backlog work.

**Main state:** `main` @ **v3.11.2** (AGENTIC-1214 native reply hardening, PR #75; automatic reply-state annotation, PR #73; tasks roadmap refresh, PR #74; v3.10.1 archive human-sender screen, PR #72).

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
