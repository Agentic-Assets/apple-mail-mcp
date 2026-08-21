# Active Pointer: apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; local gates enforce).

**Current branch:** `fix/post-3.11.7-defect-audit` (base: `origin/main` @ `3e77be6`). It carries **v3.11.8**: every defect from the post-3.11.7 audit, plus four the audit's own tooling was too blind to see. Not yet merged, not yet tagged.

**Main state:** **v3.11.7 is tagged and pushed** — signed tag `v3.11.7` on `3e77be6`, the merge of PR #93. That closed the release opened by PRs #90 (`dc56b3c`), #91 (`ab04304`), and #92 (`b30d9c4`), all merged 2026-08-19. The tag is immutable, artifacts are rebuilt from source, the `.mcpb` filename embeds the version, and `serverInfo.version` now reports the real version — so this branch bumps to **3.11.8** rather than amending a shipped release. Notes go under the dated `## 3.11.8` section; `## Unreleased` must be empty at gate time.

## What this branch fixes

One shape throughout: **a failure that reported success**. v3.11.7 closed that class in the three branches it merged; this closes the ones still standing after it.

**Wrong answers returned confidently:**
- `list_calendars` could return duplicate Calendar.app display names with no
  usable way to select the intended calendar. AGENTIC-2470 now preserves the
  opaque `calendarIdentifier`; scoped calendar operations accept that ID and
  reject an ambiguous display name.
- `search_emails` answered `has_more: false` from behind the 50-message scan ceiling, next to a `recent_days_applied` claiming a 90-day window. Paging could not escape it — `offset=30, limit=20` re-clamped and reported `has_more: false` again. `has_more` is deliberately unchanged; a saturated scan now reports `scan_ceiling_reached` plus which mailboxes hit it. Fires on runtime saturation, not the static clamp, so a small folder read in full stays silent.
- `export_emails(scope="correspondent")` omitted messages under a success banner. Five bare tries each wrapped a whole recipient `repeat`, so one unresolvable recipient hid every later one in that list. This is the call [`email-archive-cleanup`](../plugin/skills/email-archive-cleanup/SKILL.md) prescribes as the evidence snapshot taken *before* an irreversible `delete_permanent` — a silent under-export meant mail was permanently deleted that was never written to disk.
- `get_email_thread` printed `FOUND N` and rendered fewer than N; JSON mode returned a truncated thread with no count at all.
- `get_mailbox_unread_counts` could drop a mailbox, its entire child subtree, or a whole offline account, indistinguishable from zero-unread under the default `include_zero=False`.
- `list_inbox_emails` rendered a short list as complete while `__COUNT__|||sentCount` sat on the wire next to a header built from `messageCount`, never compared.
- `search_emails` dropped messages that had *already matched* the filter: `collectLimit` decremented after the append, so the page came back full-shaped and one row short.
- `get_statistics` returned a derived `read` / `read_percent` with no provenance on the field itself, beside its own note warning not to derive one.

**Safety and hang paths:**
- `build_bounded_message_scan` bound an empty result on a stale-low count, where the old code enumerated real contents. Now slice-first: the bounded window is requested before the count is consulted, and a count contradicted by a probe past its end raises rather than binding empty. `messages 1 thru 0` — which returns the *first* message, not none — is never emitted.
- `manage_drafts(action="delete")` could satisfy its own safety check by failing to read a recipient, blinding the reverse "no unexpected actual recipient" test. Now fails closed before `delete foundDraft`.
- `get_inbox_overview(max_recent=…)` had no clamp anywhere in the package; a large value enumerated a live 25,012-message Exchange inbox. `max_recent <= 0` still means "skip the recent block" and is passed through unclamped — flooring it at 1 would rewrite "read nothing" into "read the first message."

**Contract:**
- `serverInfo.version` advertised the `mcp` library's version (`1.29.0`) to every client; the package had no runtime version source of truth at all.
- The CLI exited 0 on every structured error, and `search --json` had three different error envelopes. Now one envelope with a machine-readable code; gate-facing subcommands keep their own exit contracts.

**Both gates meant to catch this class were themselves vacuous:**
- The unbounded-`whose` lint was a line-by-line regex with four demonstrated evasions (one triggered by an accidental `ruff format` reflow), and three of its five rules were rooted below the package, leaving `core/`, `bounded_scan.py`, and `calendar_core/` unscanned. It had already gone vacuous once, silently, through the whole v3.11.6 subject-filter bug. Rebuilt on the AST foundation its bare-`try` sibling proved.
- The AppleScript compile hook exited 0 having compiled **nothing** on five of the modules it was pointed at: its loader registered a half-built module under its package-qualified name before executing it, and every package here is a re-export facade, so the parent's `from .script import …` raised `ImportError` — reported as a skip. Coverage 0 → **82 scripts across 36 modules**. Negative control: deleting one `end try` from the module of the original 3.3.0 regression makes it exit 2.

## Gate and verification state

Run before opening the PR (results recorded in the PR body, not here):
`bash tools/gates/dev-check.sh release` — fatal `ruff check` / `ruff format --check` / `mypy --strict`, identity scan, module line budget, artifact rebuild + byte parity, `mcpb unpack + validate`, `claude plugin validate --strict`, tasks layout.

**Two-sided ratchets.** `test_no_bare_applescript_try.py` and the rebuilt `test_no_unbounded_whose.py` both fail when a count comes in *under* baseline, so a fix fails the ratchet until the baseline is regenerated with `python3 tests/core/test_no_bare_applescript_try.py --write-baseline`. Confirm every number moved **down**. Recount tests with `PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests` and restamp `tools/expected_test_count.txt`.

**Verify through `.venv/bin/apple-mail`, never through MCP tools.** MCP tools execute the *installed* plugin, which stays at v3.11.6 until the marketplace promotes a payload — so they return a clean answer from stale code.

**Still not verified live.** #91's destructive paths (`manage_trash`, `update_email_status`) have never been executed against real mail under the new code; the `empty_trash` safety proof is static. The disposable-fixture matrix in [`active/native-reply/`](active/native-reply/) remains the outstanding gate and still needs Cayman.

**Next action:** open the PR, then **await Cayman's merge approval** — merging is founder-gated and no approval has been given. After merge: cut the signed `v3.11.8` tag (`bash tools/gates/create-release-tag.sh` previews; `--confirm-create` signs) *after* the merge, since the gate stamp binds HEAD's SHA and merging invalidates a stamp cut before it. Then decide on marketplace promotion.

**Roadmap:** [`reference/roadmap-2026-07-10.md`](reference/roadmap-2026-07-10.md). Next three builds: port `get_email_source` forward, add junk + colored-flag actions to `update_email_status`, then the typed-`AppleScriptError` error-contract pass.

## Open lanes

**Carried forward from the audit, not fixed here.** Filed to Linear rather than folded into this branch: **AGENTIC-2421** (promote the v3.11.7 payload; the installed plugin is still v3.11.6), **AGENTIC-2422** (two bounded-in-practice raw enumerations still allowlisted in the rebuilt `whose` lint), and **AGENTIC-2423** (the compile hook still cannot reach `reply_runner.py` or `attachments.py`) are new, with **AGENTIC-2371** and **AGENTIC-2357** updated with the audit's findings. Highest-value remaining by impact: AGENTIC-2372 (`get_awaiting_reply` lists threads that were already answered), AGENTIC-2373 (thread export prints `Exported: 0` on localized/Exchange accounts), AGENTIC-2376 (mutation tools discard partial-work output on error), AGENTIC-2377. AGENTIC-2375 is a founder policy decision on whether file-writing tools should refuse paths outside the home directory. AGENTIC-781's human-operated native-reply and attachment-contract checks stay open; AGENTIC-1093 and AGENTIC-842 remain founder-controlled; AGENTIC-1191 needs a fresh sanitized reproduction before implementation.

**Live verification, needs Cayman.** [`active/native-reply/`](active/native-reply/): remaining native-reply and attachment-contract TO-TEST items that cannot be mocked, now also carrying the destructive-path gate above. The attachment ordering and fail-closed verifier fix is in [`native-reply-attachment-closeout-2026-08-10.md`](active/native-reply/native-reply-attachment-closeout-2026-08-10.md); its acceptance matrix and locale-independent quote follow-ups are in [`native-reply-attachment-forward-queue-2026-08-10.md`](active/native-reply/native-reply-attachment-forward-queue-2026-08-10.md). Using a disposable fixture only, live-exercise standalone plain/HTML, native reply/reply-all, explicit-path forward, and rich draft attachments, then confirm proof scope, any current Drafts locator, filename/count/readability, quote/logo preservation, and `GUARD_ABORT` under real focus contention. Only RFC-backed reply identity may enter the guarded cleanup test; transaction-only iCloud proof is verify-and-inspect only.

**Planning, awaiting sign-off.** [`active/id-first-search-retirement/`](active/id-first-search-retirement/): v4 fuzzy-selector retirement. Decision brief ready; `mailbox="All"` opt-in, v4 legacy-selector removal, and metadata-index live measurement not started. Also owns the open `allow_filter_scan` product decision for `move_email` / `update_email_status` / `manage_trash`.

**Research complete, implementation not started.** [`active/fast-search-index/`](active/fast-search-index/): index-backed fast path for metadata search, measured against a live 87K-message `Envelope Index` in [`research-fast-metadata-search-2026-08-17.md`](active/fast-search-index/research-fast-metadata-search-2026-08-17.md) (AGENTIC-2345). Metadata queries run 0.2 to 74 ms against the index versus roughly 10 s through Mail's window, `messages.ROWID` is the AppleScript `message id`, and the only safe read pattern is a `clonefile(2)` snapshot because a `mode=ro` connection still mutates the `-shm`. Body text is a dead end through any public API. Phase 0 (AGENTIC-2344) shipped in v3.11.7. Read section 10 before implementing: it holds the open product decisions.

**Distribution evidence open.** [`active/central-marketplace-source-contract/`](active/central-marketplace-source-contract/) (source/identity/signed-tag contract; shared Marketplace admission remains a separately authorized external gate) and [`active/v3.11.6-cursor-adapter/`](active/v3.11.6-cursor-adapter/) (shipped on `main`; local 41-tool Cursor Agent acceptance passed, Cursor marketplace/UI admission unverified).

**Stale, confirm resume-vs-archive.** [`active/v4-performance-consolidation-2026-05-27/`](active/v4-performance-consolidation-2026-05-27/): module-split work shipped (v3.9.1); perf/FTS lanes have not moved since 2026-05-27. [`active/linear-backlog-2026-07-31/`](active/linear-backlog-2026-07-31/) shipped in PR #83 and is retained pending its live gates. Both are over the 30-day archive threshold.

**Other open branch (no task folder).** `fix/github-issues-mcp-hardening-20260617` holds an unmerged `get_email_source` tool (raw RFC822/MIME by id). The roadmap flags porting it forward as the top next build; the branch can be dropped once ported.

**Caveats (carried, not blockers):**
- Native reply needs Mail window focus + Accessibility permission (`native_format=False` avoids it; returns `REPLY_WINDOW_FOCUS_FAILED` when focus cannot be acquired).
- Logo not repainted in the reopened draft editor = native Mail behavior (not our bug); SEND-level confirmation still pending.

**Recently shipped** (detail under [`archive/`](archive/)): v3.11.7 and its three merges, v3.11.0 automatic reply-state annotation, v3.10.1 archive human-sender screen, v3.10.0 Apple Calendar surface, v3.9.4 skill-example accuracy, v3.9.1 module line-budget splits, v3.8.0 native-format reply drafts. The post-3.11.7 defect audit remains active on this unmerged branch under [`active/post-3.11.7-defect-audit/`](active/post-3.11.7-defect-audit/).
