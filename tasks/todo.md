# Active Pointer: apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; local gates enforce).

**Current branch:** `chore/central-marketplace-source-contract` (isolated worktree from `main` @ v3.11.6).

**Active implementation:** [`tasks/active/central-marketplace-source-contract/`](active/central-marketplace-source-contract/). Follow the checked to-do list in [`phase-plan-2026-07-15.md`](active/central-marketplace-source-contract/phase-plan-2026-07-15.md): publish the source-owned payload and signed-release contracts, make the central catalog the primary Agentic Assets install path, retain the source catalog as an explicit compatibility lane, add local blockers, run the full release gate, and stop before merge without fresh Cayman approval.

**Most recent shipped workstream:** [`tasks/active/v3.11.6-cursor-adapter/`](active/v3.11.6-cursor-adapter/). Its explicit `${CURSOR_PLUGIN_ROOT}` launcher is on `main` at v3.11.6, the Codex adapter remains independent, the full local release gate passed, and live 41-tool Cursor Agent acceptance passed. Cursor marketplace/UI admission remains unverified.

**Already shipped:** AGENTIC-1214 merged in v3.11.2. It added chunked, focus-guarded native reply typing; full-body verification above the quote; persisted header-linked Drafts identity; safe artifact cleanup; quote-boundary verification; and the `CREATE_CANNOT_THREAD` refusal for standalone draft creation with `in_reply_to`. Its closeout is archived under [`archive/2026-07/shipped/agentic-1214-reply-fixes/`](archive/2026-07/shipped/agentic-1214-reply-fixes/).

**Next action:** finish adversarial review and local release verification for the central marketplace source-contract branch, install and read back the checked-in local blockers, then open its review PR. Do not merge without Cayman's new literal approval phrase. After that contract merges, the first future release must create a signed annotated tag with the new payload bindings before central marketplace promotion and admission. Keep AGENTIC-781's human-operated native-reply checks open; AGENTIC-1192, AGENTIC-995, and AGENTIC-996 remain distinct backlog work.

**Main state:** `main` @ **v3.11.6** (`04f9d60`; explicit Cursor plugin-root adapter plus the consolidated offline runtime, sent-mailbox, and compose-recipient verification work).

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
