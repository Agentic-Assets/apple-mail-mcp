# Apple Mail MCP — Live Field Report (2026-06-04)

**Author:** Claude (agent), driving the plugin from a real session
**Context:** Real QA task — review the 10 reply drafts created today on the *TU - Cayman* Exchange account, confirm each was a genuine threaded reply to its original, and confirm appropriateness. Doing that exercised compose/reply, draft listing, search (INBOX + `All`), and `get_email_by_id` against a large Exchange mailbox. This report records what broke or got in the way, with code references, plus recommended fixes/additions/refinements.

**Plugin version exercised:** 3.4.0 (cache `~/.claude/plugins/cache/apple-mail-mcp/apple-mail/3.4.0/`)
**Source of truth:** `plugin/apple_mail_mcp/`

---

## TL;DR — ranked

| # | Severity | Issue | Root cause (code) | Fix shape |
|---|----------|-------|-------------------|-----------|
| 1 | **High** | Reply/forward create **duplicate drafts** | `save <msg>` **then** `close window 1 saving yes` persists twice (`tools/compose.py:1127,1135-1136,1688`) | Persist once |
| 2 | **High** | No **recipient (To/Cc)** in any read tool | Field tuple omits recipients (`tools/search.py:108-122`) | Extract + return `to`/`cc` |
| 3 | **High** | Can't **verify a draft is threaded / who it's to** | No In-Reply-To/References surfaced; no draft introspection | `get_draft()` / add headers to `get_email_by_id` |
| 4 | **Med** | Reply/forward are **GUI + clipboard + fixed-delay** fragile | `reply ... with opening window`, NSPasteboard paste, `delay 0.1–2.5`, positional `close window 1` (`tools/compose.py:1060-1138`) | Poll for readiness; target window by reference; stop clobbering clipboard |
| 5 | **Med** | `mailbox="All"` search **times out** on Exchange | Iterates every mailbox (`tools/search.py:365-369`); per-folder Exchange cost exceeds wrapper timeout | Partial results + per-mailbox structured timeout; `mailboxes=[...]` param |
| 6 | **Med** | `manage_drafts(action="list")` is **low-signal** | Returns subject + created date only | Add recipient, snippet, `message_id`, `hide_empty` |
| 7 | **Low** | **Orphaned empty drafts** accumulate | Aborted compose windows saved by `close ... saving yes` | Guard + cleanup |
| 8 | **Low** | **No dedup guard** before creating a reply | None | Warn if an unsent draft already exists for the thread |
| 9 | **Low** | `content_preview` **merges new body + quoted original** | Single plain-text dump | Split `body_new` vs `quoted`, or quote-stripped preview |

What works well (keep): native `reply` preserves the threaded HTML history; temp-file body passing avoids AppleScript escaping bugs; bounded-scan + `UNBOUNDED_SCAN_REQUIRED` guardrails; standalone tools refusing `Re:`/`Fwd:` subjects without `standalone_confirmed`.

---

## 1. Duplicate drafts on reply/forward — **High**

**Live evidence.** Today's TU drafts contained two exact-duplicate pairs, each pair created in the same second:

- `Re: Submission for Real Estate Finance and Investment Symposium 2026` — message ids **80042** and **80041**, both `2026-06-04T17:17:07`, byte-identical bodies.
- `Re: ESG survey - upgraded Corbis PDF extraction…` — ids **79989** and **79988**, both `2026-06-04T17:15:10`, byte-identical bodies.

**Root cause.** The reply (and forward) draft path explicitly saves the message **and then** closes the compose window "saving yes", which persists a second copy:

```
# tools/compose.py — reply, mode="draft"
1135    save replyMessage
1136    close window 1 saving yes
```
```
# tools/compose.py — forward
1688    post_forward_action = "save forwardMessage\n            close window 1 saving yes"
```

Contrast the clean standalone path, which saves once with no window and produces no duplicate:

```
# tools/compose.py — standalone draft
1435    send_command = "save newMessage\n            activate"
1440    send_command = "save newMessage"
```

`save <msg>` writes the draft; `close window … saving yes` then asks Mail to save the still-open compose window again. On Exchange/Mail this commits a second draft (the same-second, identical-body pairs are the fingerprint).

**Recommended fix.** Persist exactly once. Either:
- drop the explicit `save replyMessage` / `save forwardMessage` and let `close window 1 saving yes` be the single persist, **or**
- keep the explicit save and close with `saving no`.

Then add a regression test asserting one reply call yields exactly one new Drafts message (the suite already has `tests/test_compose_tools.py` / `tests/test_compose_none_handling.py` to extend). This is the highest-value, lowest-risk fix.

---

## 2. No recipient (To/Cc) returned by any read tool — **High**

**Live evidence.** `search_emails`, `get_email_by_id`, and `manage_drafts(action="list")` never returned a recipient. To confirm today's drafts were addressed to the right people I had only the greeting line in the body ("Hi Luis", "Dear Thies") to go on — I could not see the actual `To:` address.

**Root cause.** The per-message field tuple stops at sender; recipients are never extracted:

```
# tools/search.py:108-122  (record built per message)
108     "subject":  parts[2].strip(),
109     "sender":   parts[3].strip(),
110     "mailbox":  parts[4].strip(),
112     "is_read":  parts[6].strip().lower() == "true",
113     "received_date": parts[7].strip(),
122     record["content_preview"] = parts[8].strip()
```

No `to recipients` / `cc recipients` are read from the AppleScript message.

**Recommended fix.** Add `to` and `cc` (comma-joined addresses) to the AppleScript field emission and the record dict, behind the same content-cost guard as `content_preview` (recipients are cheap relative to body). Most valuable on Drafts and Sent, where "who is this going to" is the whole question.

---

## 3. Can't verify a draft's threading / origin — **High** (compounds #2)

**Live evidence.** Two of today's drafts (`Corbis at Walton`, `Recommendation Request`) include the quoted original inline, so they're provably threaded. The other eight are short and end at "Best, Cayman" with no quoted text, and there is **no API way** to confirm they are attached to the original thread (In-Reply-To/References) versus standalone drafts wearing a `Re:` subject. For a draft-review/QA workflow this is the single biggest blind spot — I had to hedge the conclusion.

**Recommended fix.** Either:
- add `internet_headers` (`In-Reply-To`, `References`, `Message-ID`) to `get_email_by_id`, or
- add a dedicated `get_draft(message_id)` / `verify_draft(message_id)` returning `{to, cc, bcc, subject, in_reply_to, references, has_quoted_original, body_snippet}`.

This makes "is this draft a real, correctly-addressed reply to the right thread?" answerable without opening Mail. Pairs naturally with #2.

---

## 4. Reply/forward GUI + clipboard + fixed-delay fragility — **Med**

**Observations / root cause.** The reply path opens a GUI window, pastes the body via NSPasteboard, and relies on hard-coded sleeps and positional window addressing:

```
# tools/compose.py
1062    set replyMessage to reply foundMessage with opening window
...     (HTML written to clipboard, pasted)
1117    delay 0.5
1135    save replyMessage
1136    close window 1 saving yes
```
Fixed `delay` values appear throughout (`0.1, 0.2, 0.3, 0.5, 1, 1.5, 2.5` — e.g. lines 513, 774, 835, 1080, 1117, 1193, 1647). Consequences seen/likely:
- **Empty-subject drafts** — the live Drafts list shows several blank-subject rows, the classic signature of a compose window that was saved before paste/populate completed.
- **Clipboard clobbering** — pasting via NSPasteboard overwrites whatever the user had copied.
- **`close window 1` is positional** — under load or if another compose/window is frontmost, it can close the wrong window.
- Timing is machine-load dependent, so failures are intermittent and hard to reproduce.

**Recommended fix (incremental).**
1. Replace fixed `delay`s with bounded polling (wait until the reply window exists / body length > 0, up to a timeout) — removes the race without inflating latency on fast machines.
2. Address the window by the reference returned when opening it, not `window 1`.
3. Save/restore the user's pasteboard around the paste, or prefer a content-injection path that doesn't touch the system clipboard.

---

## 5. `mailbox="All"` search times out on Exchange — **Med**

**Live evidence.** `search_emails(account="TU - Cayman", mailbox="All", recent_days=14, subject_keywords=[…])` returned:
```
"errors": ["TU - Cayman"],
"error_details": [{"type": "timeout", "message": "AppleScript execution timed out"}]
```
The whole call failed with zero results — no partial data — so I had to fall back to many sequential single-mailbox INBOX searches to locate originals (and several originals were in folders I never reached).

**Root cause.** `All` enumerates and scans every mailbox, capped only at `MAX_MAILBOXES_PER_SEARCH`:
```
# tools/search.py:365-369
365     if mailbox == "All":
367         set searchMailboxes to every mailbox of targetAccount
368         if (count of searchMailboxes) > {_max_mailboxes_per_search} then
369             set searchMailboxes to items 1 thru {_max_mailboxes_per_search} of searchMailboxes
```
On a large Exchange account, materializing + scanning that many remote folders blows the wrapper timeout before any result is emitted.

**Recommended fix.**
- Emit **partial results per mailbox** and attach a structured per-mailbox timeout, instead of failing the whole call (the code already has per-mailbox error plumbing at `tools/search.py:807,869` — extend it so a timeout in folder N still returns folders 1..N-1).
- Add a `mailboxes=[...]` parameter so callers can target a few folders (e.g. `["Archive", "Sent"]`) without paying for `All`.
- Lower the default `All` mailbox cap on Exchange, and surface the cap in the result (it already warns at `tools/search.py:225,252`).

---

## 6. `manage_drafts(action="list")` is low-signal — **Med**

**Live evidence.** Listing TU drafts returned only `subject` + `Created:` date. To review them I had to run a separate `search_emails(mailbox="Drafts", include_content=True)` to get bodies and ids, and even then had no recipients. The list also contained many blank-subject rows (see #7), which are noise.

**Recommended fix.** Extend the list payload with `message_id`, recipient (`to`), a short body snippet, and add `hide_empty=True` to suppress orphaned empty drafts. That makes the list directly triageable for exactly the "review my drafts" workflow.

---

## 7. Orphaned empty drafts accumulate — **Low**

Multiple blank-subject drafts appear in the TU Drafts list. These are consistent with compose windows that were saved (via `close … saving yes`) before being populated, or abandoned mid-flow. Tie a fix to #1/#4 (persist deterministically) and optionally add a maintenance action to list/remove empty drafts (subject blank **and** body empty), with a dry-run + cap like the other destructive operations.

---

## 8. No dedup guard before creating a reply — **Low**

Independent of the #1 root cause, there is no check for an existing unsent draft on the same thread before creating another. A lightweight guard ("an unsent draft already exists for this thread — reuse/replace/create anyway?") would have prevented today's duplicates outright and protects against agent-side retries.

---

## 9. `content_preview` merges new body + quoted original — **Low**

For threaded replies the preview is dominated by the quoted original (e.g. the `Corbis at Walton` draft preview was mostly Tim Riley's quoted thread + signature), making it hard to see just the newly written text. Offer a quote-stripped preview or split `body_new` vs `quoted` so review tools can show the actual new content.

---

## Suggested sequencing

1. **Ship now (small, high value):** #1 persist-once fix + regression test; #6 richer draft list; #2 add `to`/`cc` to records.
2. **Next:** #3 draft introspection / headers (unblocks reliable draft-QA); #5 partial-results for `All` + `mailboxes=[...]`.
3. **Hardening:** #4 poll-instead-of-delay + window-by-reference + clipboard restore; #7 orphan cleanup; #8 dedup guard; #9 preview split.

Per repo `CLAUDE.md`: route the compose/search edits through `generalPurpose` subagents, run `plugin-dev:plugin-validator` + `skill-reviewer` + `code-simplifier` on the "ready to ship" pass, and finish with `bash tools/dev-check.sh release` (ruff/mypy/artifact parity gate). Tool-count claims unchanged unless #3/#5 add a new tool — if so, bump the `grep -c "^@mcp.tool"` count across all five manifests.

---

*All findings above were observed live against the TU - Cayman Exchange account on 2026-06-04 and cross-checked against source in `plugin/apple_mail_mcp/`. No mail was sent and no drafts were deleted in producing this report.*

---

## 3.5.0 reproduction update (2026-06-05)

**Context.** Live task: draft replies to the day's TU inbox (a revision-thread reply-all and a dinner RSVP) on the *TU - Cayman* Exchange account, plugin upgraded to **3.5.0**. The duplicate/clipboard findings below were **not fixed** in 3.5.0 and one got materially worse. To land **one** correct revision draft I needed 2 `reply_to_email` attempts + 1 `manage_drafts(create)` + 4 trash deletions. For unattended/agent drafting this is effectively unusable.

**What 3.5.0 improved (confirmed live):** read tools now return `to`, `cc`, and `has_quoted_original` (findings #2/#3 addressed on the read side), and `manage_drafts(action="list")` now reports `Id` + `To` + body snippet (finding #6). These were genuinely helpful for triage and were the only reason the bugs below were catchable.

### A. Cross-thread body leak — **Critical** (escalation of #4; data-integrity, not just fragility)

A **single** `reply_to_email(message_id=80094)` call (the dinner thread) produced **three** drafts:
- `80256` — Re: Dinner… → meagan, correct body ✓
- `80257` — Re: Dinner… → meagan, **empty (signature only)**
- `80254` — **Re: Notes from today's meeting.** → **hmaleki@business.rutgers.edu**, body = **the dinner reply text**

The dinner body was pasted into a draft replying to a **different message/thread** (Hosein Maleki's "Notes from today's meeting", msg 80088) that the call never referenced. This is the positional `close window 1` + NSPasteboard race (finding #4) manifesting as **content for thread A written into a reply to thread B** — i.e. a real risk of sending the wrong content to the wrong recipient. This should be its own top-severity finding, above the duplicate-persist issue.

### B. Duplicate-persist now also hits the standalone `manage_drafts(create)` path — **High** (regression vs 2026-06-04)

On 6/4 the standalone create path saved exactly once (it was the "clean" contrast in finding #1). On 6/5, `manage_drafts(action="create", ...)` produced **two** drafts from one call:
- `80268` — To: Stace, Cc: Mariya, body ✓ (keeper)
- `80269` — **To: blank**, body present

So the `save … then close … saving yes` double-persist (finding #1) is no longer confined to reply/forward; it now reproduces on the standalone path too, and the duplicate can carry **degraded recipients** (blank To). Intermittent — same call shape was single-draft on 6/4.

### C. Empty-body race on reply — **High** (compounds #4)

The first revision retry (`reply_to_email(message_id=80095, cc=Mariya)`) produced a single draft (`80265`) with **correct recipients and correct quoted original but no new body text** — the "Thanks for the nudge, Mariya…" paste was lost entirely (`has_quoted_original=true`, body = signature + quote only). Opposite failure mode to A's empty `80257`. Confirms the body paste is timing-dependent and silently drops.

### D. `reply_to_all=True` drops a To recipient — **High**

Mariya forwarded the thread To: Cayman + Stace; Stace replied. `reply_to_email(message_id=80095, reply_to_all=True)` set **To: Stace only** — Mariya was dropped, even though the message body literally opens "Thanks for the nudge, Mariya." Had to force her in via explicit `cc=`. Reply-all is silently under-populating recipients, which for a coauthor R&R thread is a correctness bug, not cosmetic.

### E. Verification-tooling gaps surfaced while QA'ing the above

- `manage_drafts(action="create")` success message echoes `To` but **not `Cc`** — can't confirm Cc without a follow-up `get_email_by_id` (which *does* now return `cc`). Echo Cc/Bcc in the create/reply confirmation.
- `search_emails(mailbox="Drafts", subject_keyword="8296082", recent_days=1)` returned **zero** for a draft created seconds earlier and dated today. Newly-created (esp. standalone) drafts aren't reliably findable by subject/date search — likely a null/odd `received_date` on the create path. Forces a fall back to the full `manage_drafts(list)` (100 rows) just to find one's own just-made draft. Either stamp a sane date on created drafts or return the new draft's `message_id` from `create`/`reply`.

### Recommended priority shift

1. **Cross-thread body leak (A)** is now the highest-value fix — it is a correctness/data-integrity hazard, not just flakiness. Implement finding #4's window-by-reference + poll-for-readiness + pasteboard isolation, and gate persist on "this is the window we opened AND body length matches what we wrote."
2. **Return the created draft's `message_id`** from `reply_to_email` / `forward_email` / `manage_drafts(create)`. Every bug above was only diagnosable because of post-hoc list/get; the tools should hand back the id they just wrote so a caller can verify in one cheap `get_email_by_id` instead of a 100-row list scan.
3. **Persist-once (B/#1)** and **reply-all recipient capture (D)** remain High and now have fresh 3.5.0 repros.

*Observed live against TU - Cayman on 2026-06-05, plugin 3.5.0. Net Drafts side effects from this session were reconciled (junk drafts 80253/80254/80257/80265/80269 moved to Trash; keepers 80256 dinner, 80268 revision). No mail was sent.*
