# Active Pointer: apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; local gates enforce).

**Current branch:** `fix/native-reply-attachment-verification` (base: `origin/main` @ `ed9e1ee`).

**Active implementation:** [`tasks/active/native-reply/`](active/native-reply/). This branch now applies the draft-first attachment contract to standalone compose, replies, explicit-path forwards, and rich EML drafts. The local release gate is green; protected disposable-fixture checks remain required before the work can be considered live-release-complete. Do not merge without Cayman's new literal approval phrase.

**Most recent shipped workstream:** [`tasks/active/v3.11.6-cursor-adapter/`](active/v3.11.6-cursor-adapter/). Its explicit `${CURSOR_PLUGIN_ROOT}` launcher is on `main` at v3.11.6, the Codex adapter remains independent, the full local release gate passed, and live 41-tool Cursor Agent acceptance passed. Cursor marketplace/UI admission remains unverified.

**Already shipped:** AGENTIC-1214 merged in v3.11.2. It added chunked, focus-guarded native reply typing; full-body verification above the quote; persisted header-linked Drafts identity; safe artifact cleanup; quote-boundary verification; and the `CREATE_CANNOT_THREAD` refusal for standalone draft creation with `in_reply_to`. Its closeout is archived under [`archive/2026-07/shipped/agentic-1214-reply-fixes/`](archive/2026-07/shipped/agentic-1214-reply-fixes/).

**Next action:** keep AGENTIC-781's human-operated native-reply and attachment-contract checks open. AGENTIC-1093 and AGENTIC-842 remain founder-controlled, and AGENTIC-1191 needs a fresh sanitized reproduction before implementation. The implemented Mail, Calendar, and EML workflows still need their documented protected live fixtures before PR #83 can be treated as live-release-complete.

**Main state:** `origin/main` @ `ed9e1ee` (PR #83). The latest release tag is **v3.11.6** (`04f9d60`; explicit Cursor plugin-root adapter plus the consolidated offline runtime, sent-mailbox, and compose-recipient verification work).

**Roadmap:** [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md): new tools, new skills, enhancements, hardening backlog, and documented macOS refusals. Next three builds: port `get_email_source` forward, add junk + colored-flag actions to `update_email_status`, then the typed-`AppleScriptError` error-contract pass.

## Open lanes

**Live verification, needs Cayman.** [`active/native-reply/`](active/native-reply/): remaining native-reply and attachment-contract TO-TEST items that cannot be mocked. The attachment ordering and fail-closed verifier fix is recorded in [`native-reply-attachment-closeout-2026-08-10.md`](active/native-reply/native-reply-attachment-closeout-2026-08-10.md); its disposable-fixture acceptance matrix and locale-independent quote follow-ups are in [`native-reply-attachment-forward-queue-2026-08-10.md`](active/native-reply/native-reply-attachment-forward-queue-2026-08-10.md). Using a disposable fixture only, live-exercise standalone plain/HTML, native reply/reply-all, explicit-path forward, and rich draft attachments, then confirm proof scope, any current Drafts locator, filename/count/readability, quote/logo preservation where applicable, and `GUARD_ABORT` under real focus contention. Only RFC-backed reply identity may enter the guarded cleanup test; transaction-only iCloud proof is verify-and-inspect only.

**Planning, awaiting sign-off.** [`active/id-first-search-retirement/`](active/id-first-search-retirement/): v4 fuzzy-selector retirement. Decision brief ready for maintainer sign-off; `mailbox="All"` opt-in, v4 legacy-selector removal, and metadata-index live measurement not started. This also owns the open `allow_filter_scan` product decision for `move_email` / `update_email_status` / `manage_trash`. The former AGENTIC-1192 Archive-reply gap is implemented in the current Linear backlog lane and remains subject to release and live proof.

**Research complete, implementation not started.** [`active/fast-search-index/`](active/fast-search-index/): index-backed fast path for metadata search, measured against a live 87K-message `Envelope Index` in [`research-fast-metadata-search-2026-08-17.md`](active/fast-search-index/research-fast-metadata-search-2026-08-17.md) (AGENTIC-2345). Metadata queries run 0.2 to 74 ms against the index versus roughly 10 s through Mail's window, `messages.ROWID` is the AppleScript `message id`, and the only safe read pattern is a `clonefile(2)` snapshot because a `mode=ro` connection still mutates the `-shm`. Body text is a dead end through any public API. Phase 0 is AGENTIC-2344, a shipped `search_emails` subject-filter bug that returns 0 results on every account and blocks the lane; the unread-count divergence in section 6.4 is AGENTIC-2346. Read section 10 before implementing: it holds the open product decisions.

**Stale, confirm resume-vs-archive.** [`active/v4-performance-consolidation-2026-05-27/`](active/v4-performance-consolidation-2026-05-27/): module-split work shipped (v3.9.1); perf/FTS lanes have not moved since 2026-05-27. Over the 30-day archive threshold; kept active pending a resume-or-archive decision.

**Other open branch (no task folder).** `fix/github-issues-mcp-hardening-20260617` holds an unmerged `get_email_source` tool (raw RFC822/MIME by id). The roadmap flags porting it forward as the top next build; the branch itself can be dropped once ported.

**Caveats (carried, not blockers):**
- Native reply needs Mail window focus + Accessibility permission (`native_format=False` avoids it; returns `REPLY_WINDOW_FOCUS_FAILED` when focus cannot be acquired).
- Logo not repainted in the reopened draft editor = native Mail behavior (not our bug); SEND-level confirmation still pending.

**Recently shipped** (detail under [`archive/`](archive/)): v3.11.0 automatic reply-state annotation, v3.10.1 archive human-sender screen, v3.10.0 Apple Calendar surface, v3.9.4 skill-example accuracy, v3.9.1 module line-budget splits, v3.8.0 native-format reply drafts.
