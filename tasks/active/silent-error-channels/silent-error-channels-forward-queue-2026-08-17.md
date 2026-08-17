# Forward queue after silent-error-channels (2026-08-17)

Candidate work surfaced while closing the bare-`try` defect class on `fix/silent-error-channels-and-file-clobber` (PR #92). **This is a menu, not a roadmap.** Confidence is stated per item; verify before acting.

Items with a Linear issue are marked. Items without one are here because they did not clear the bar for a ticket but would be expensive to rediscover.

## Correctness / bugs

- **`get_awaiting_reply` lists threads the user already answered** (AGENTIC-2372, confidence: verified gap). A bare `try` around the whole `repeat with aHeader in (headers of aMessage)` block leaves `inReplyTo`/`refsHeader` empty, under-populating `replied_to_ids`, while the JSON path hardcodes `"errors": []`. This is a confidently *wrong non-empty* answer, which is worse than a zero — it sends the user to reply to mail they have already handled. Highest-impact item in this queue.

- **`fetch_replied_ids` fails open invisibly** (AGENTIC-2372, confidence: verified gap). `core/replied.py` returns `set()` on any exception and logs a warning that never reaches the response. Same consequence as above, reached by a different route. Fixing means changing a `-> set[str]` signature with three callers.

- **Thread-scope export prints `Exported: 0` on localized/Exchange accounts** (AGENTIC-2373, confidence: verified gap). `export_helpers.py` hand-rolls two inbox name guesses (`"Inbox"`, `"INBOX"`) with a bare `try` and an *empty* `on error` arm, bypassing `core/script_fragments.py::inbox_mailbox_script`, which iterates 11 localized names and raises a deliberate loud error for exactly this case. A guard exists and this path routes around it.

- **A failed id lookup is reported as a wrong diagnosis** (AGENTIC-2373, confidence: verified gap). `export_helpers.py` renders a Mail lookup *failure* as `⚠ No email found for message_id X`, telling the caller the message does not exist. `exportFailureCount` never increments, so no `Failed:` and no `PARTIAL:` hint.

- **Mutation tools discard partial-work output** (AGENTIC-2376, confidence: verified gap). All three wrap their script in an outer handler that does `return "Error: " & errMsg`, *replacing* `outputText`. A throw after six deletions tells the caller "an error occurred" and nothing about the six — and the natural response to that message is to retry. Needs one coordinated decision across `status.py`, `trash.py`, `move.py`; wait for #91.

- **Non-positive caps on the two destructive tools** (AGENTIC-2374, confidence: verified in source, reach never executed). The shared helper is fixed so `max_deletes=0` no longer resolves a message, but `manage_trash` and `move_email` still lack boundary refusals. One line each once #91 lands.

- **`save_email_attachment`: one probe failure disables three guards at once** (AGENTIC-2370, confidence: verified gap). An outer `try` returning `"0|||-1"` zeroes `matchCount`, which makes the ambiguity guard unreachable *and* skips both the size cap and the disk-space check. The existing in-source comment endorsing "safe fail-open" describes a *different* try block and covers only one of the three guards.

- **Calendar detail silently drops attendees and alarms** (AGENTIC-2377, confidence: verified gap). Cheapest fix in this queue: the enclosing block already emits `ERROR_EVENT|||` and `records.py` already routes it into `calendar_errors`. The broken arms just need to use the channel sitting two lines away.

- **`get_email_by_id`/`get_email_by_ids` return a bare error string in JSON mode** (AGENTIC-2371, confidence: verified gap). `json.loads()` throws instead of yielding an error envelope, and it happens on the *timeout* path — the one a large Exchange mailbox hits most.

- **`messageHasCorrespondent` can drop a message before it is counted** (AGENTIC-2373, confidence: hypothesis). Five bare `try` blocks; all five must throw. Listed for completeness, not as a likely live bug.

## Hardening

- **Work the ratchet down** (confidence: verified gap). The new baseline records **236 sites across 50 modules** — 178 with no error arm at all. This branch closed roughly a dozen. The ratchet stops growth but does nothing about the backlog. A sensible cadence is one module per session, lowering its baseline entry each time. `tests/fixtures/bare_applescript_try/baseline.json` is the worklist.

- **`min(timeout, 30)` idiom is wrong at 26 sites** (confidence: verified gap). It clamps only the upper side, so a negative timeout propagated into the account pre-flight. The new `run_applescript` guard catches the consequence, but each site is still wrong. Wants a shared clamp helper.

- **No minimum-timeout guard on write paths** (confidence: verified gap). `timeout=1` is accepted and passes validation, but `subprocess.run` kills osascript at the deadline mid-script. On a multi-step compose or move transaction that is a **torn write**, not a clean failure. There is no principled sub-second cutoff, which is why it was not fixed inline — but a per-path minimum on write tools deserves its own decision.

- **`AppleScriptRunner` Protocol carries no validation contract** (confidence: verified gap). Injected runners in `core/replied.py`, `core/reply_state.py`, and `tools/reply_state_wiring.py` bypass the new timeout guard by construction. Harmless today since all production paths default to the real function; the Protocol docstring should say the deadline contract is the implementation's job.

- **Offset paging over a rescan is inherently fragile** (confidence: verified, unfixable as specified). `correspondent` export re-derives `globalMatchedCount` each call, so a message that throws once and reads fine next time shifts every later matched position. Not fixable within an integer-offset API. Worth knowing before anyone trusts resume semantics.

## Simplification

- **The `pad2`/`month_number`/`iso_datetime` handler trio still has 2 uncollapsed copies** (confidence: verified). `iso_datetime_handlers()` now exists in `applescript_snippets.py` and `by_id.py`/`thread.py` use it, but `search/script.py` and `core/reply_state.py` still carry verbatim copies (5 repo-wide originally). Both are one-line adoptions; `script.py` is PR #90-owned so wait.

- **`_INBOX_ERROR_PREFIX` duplicates `_STATISTICS_ERROR_PREFIX`** (confidence: verified). Same literal `"__APPLE_MAIL_MCP_ERROR__|||"`, two independent parsers, one wire format. Wants a core-level constant.

- **Three tools return non-`str` types at all** (confidence: verified). `list_accounts`, `list_account_addresses`, `get_mailbox_unread_counts`. This is the *only* reason the `ToolError` envelope boundary needs an exception. Normalizing the surface to `-> str` would remove the carve-out entirely — but it changes published `outputSchema`, a client-visible contract change, so it needs a deliberate call.

- **`reply_scripts.py` sits at 598/600 LOC** (confidence: verified). Two lines of headroom, and it is the file most likely to be edited next by reply work. Extract before touching it, not during.

- **Integer-first AppleScript concatenation in four export builders** (AGENTIC-2373, confidence: verified). `exportCount & "_" & ...` with an integer first yields a **list**, not text; it renders correctly only because `sanitize_delimiter_block` happens to leave text item delimiters as `""`. Latent, works today.

## Process / docs

- **`REPLY_SENDER_OVERRIDE_FAILED` is undocumented in the packaged skill** (confidence: verified gap). `plugin/skills/email-drafting/` documents the other reply error codes for agents. That directory is PR #91-owned, so it was skipped here. Add after #91 merges.

- **Full-suite runs are untrustworthy on a shared checkout** (confidence: verified the hard way). Two lanes independently reported phantom failures that did not reproduce, with the collected count shifting mid-run, because sibling agents were writing test files concurrently. Any single full-suite result during parallel work needs a second run before it means anything. Worth stating in `tests/CLAUDE.md`.

- **Briefs should hand subagents claims to verify, not facts to act on** (confidence: verified the hard way). Three separate briefs this session carried premises that investigation overturned — a wrong file location, a wrong "one-line guard" characterization, and a non-existent case distinction. Every one was caught only because the lane was told to confirm before fixing. Framing findings as claims rather than facts is what made that work.

## Evaluation

- **Nothing in this branch was live-verified** (confidence: certain). Every claim is source-level, mocked, or `osacompile`-parse-checked. The honest measure of whether this work achieved its goal is a live run against a disposable fixture: does a genuinely failing read now produce a reported failure rather than a confident zero? The matrix in `tasks/active/native-reply/` is the vehicle. Until then the claim is "the emitted script now reports the failure", not "a failure was observed being reported".

- **The 236-site baseline is itself a measurement** (confidence: verified). Tracking it down over time is the cleanest available signal that the defect class is actually shrinking rather than being displaced.
