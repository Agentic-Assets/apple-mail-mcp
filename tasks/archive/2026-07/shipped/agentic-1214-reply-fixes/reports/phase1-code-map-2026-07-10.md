# AGENTIC-1214 Phase 1: code map of the reply/compose typed-insertion surface

Branch: `fix/agentic-1214-reply-body-truncation`. Read-only research; no files
outside this report were changed. All paths are relative to the repo root
unless noted.

## 1. Full control flow of `reply_to_email` native path

Entry point: `plugin/apple_mail_mcp/tools/compose/reply.py:52` (`reply_to_email`).

1. Guard checks (lines 126-164): `output_format` must be `text`/`json`;
   `native_format=False` requires `allow_windowless_fallback=True` or returns
   `WINDOWLESS_FALLBACK_DISABLED` (line 130); `message_id` is required or
   returns `_MESSAGE_ID_REQUIRED_ERROR` / `TARGET_SELECTOR_DEPRECATED`.
2. `_resolve_account` (line 166, `helpers.py:108`) resolves/validates the
   account name against Mail (up to 30s).
3. `_build_found_message_lookup` (line 171, `lookup_scripts.py:25`) builds the
   AppleScript fragment that resolves the source Inbox message by exact id.
4. `reply_body = _strip_cdata_wrappers(reply_body) or ""` (line 184,
   `payload.py:102`) strips stray `<![CDATA[...]]>` wrappers. `body_html` is
   accepted but always ignored (lines 96-97, 185-187) — replies are plain
   text over Mail's native quote, never HTML.
5. `_validate_from_address` (line 190) and `_resolve_signature_name` /
   `_validate_signature_name` (lines 198-201) validate optional sender
   override and signature name (each up to 30s).
6. Delivery mode resolved to `effective_mode` (`send`/`draft`/`open`, lines
   205-210); `compose._send_blocked` gates `send` under read-only/draft-safe
   (line 212); `mode="open"` checks the open-compose-window cap (lines
   218-221).
7. **Body temp file** written (lines 229-237): the full, untruncated
   `reply_body` is written to a `NamedTemporaryFile(delete=False, suffix=".txt",
   prefix="mail_reply_")`. `body_temp_path` is the only way the body text
   crosses into AppleScript — it is never interpolated into the script as a
   literal string (avoids `escape_applescript` size/escaping limits on long
   bodies).
8. CC/BCC loops, attachment script, and status-line escaping are built (lines
   239-264).
9. `mode_plan = _reply_mode_plan(effective_mode)` (line 266,
   `reply_scripts.py:21-42`) and `cleanup_script` (line 268, `rm -f` of the
   temp file) are built.
10. **Native branch** (`native_format=True`, the default; lines 272-306):
    `_build_reply_native_window_applescript` (`reply_scripts.py:295`)
    assembles the full AppleScript. No sender is pinned unless the caller
    explicitly passed `from_address` (comment lines 273-280: pinning the
    single-alias sender on the native window strips the logo signature).
11. `compose.run_applescript(script, timeout=...)` runs (line 338-340;
    `timeout` defaults to `None` → `run_applescript`'s own 120s default, see
    §6). Two outcomes:
    - **`GUARD_ABORT` / `GUARD_ABORT_SUBJECT`** (lines 341-418): the reply
      window never reached the exact-title focus state needed to type, so no
      keystroke happened and (per the script) any auto-created draft shell was
      closed. `_verify_saved_reply_draft` still runs (lines 344-352) as a
      best-effort probe for an orphaned artifact, and the tool returns a
      structured `REPLY_WINDOW_FOCUS_FAILED` or `REPLY_SUBJECT_GUARD_MISMATCH`
      `ToolError` (lines 361-418) with `suspected_draft_id` from that probe.
    - **Success text present** (line 419, `mode_plan.success_text in result`):
      `reply_subject`, `draft_id`, `quoted_needle` are parsed out of the
      AppleScript's `Field: value` output lines via `_extract_output_field`
      (lines 420-422, `verification.py:15`). `signature_requested_for_verify`
      is forced to `None` for the native default (no explicit
      `signature_name`) because the native window's own default signature is
      rich text that cannot be substring-matched (lines 423-429, comment).
      `_verify_saved_reply_draft` (line 430, `saved_draft_checks.py:43`) runs
      the real post-save check (§3). On failure,
      `_reply_draft_verification_error` (line 444, `verification.py:190`)
      returns a structured error (`REPLY_DRAFT_BODY_MISSING` /
      `REPLY_DRAFT_BODY_AFTER_QUOTE` / `REPLY_DRAFT_VERIFICATION_TIMEOUT` /
      `REPLY_DRAFT_VERIFICATION_ERROR`). On success, `output_format="json"`
      returns `_reply_success_payload` (line 451, `verification.py:126`,
      §3); otherwise the text result is appended with
      `_format_reply_verification_lines` (line 458, `verification.py:94`).
12. `finally` (lines 466-469): the temp file is unlinked defensively
    (`suppress(OSError)`), in case the AppleScript's own `cleanup_script`
    never ran (e.g. `AppleScriptTimeout` killed `osascript` mid-run).

## 2. Every `System Events keystroke` / clipboard paste in the package

Grep across `plugin/apple_mail_mcp/` for `keystroke` and
`clipboard|NSPasteboard|pasteboard`:

| Location | What | Text typed/pasted | Clipboard touched? |
|---|---|---|---|
| `tools/compose/reply_scripts.py:470` | `keystroke replyBodyText` inside `tell process "Mail"` | The **entire** `reply_body` (from the temp file, read into `replyBodyText` at line 378) in **one** call | No — explicitly avoided (docstring lines 325-326: "never the clipboard, which clobbered the pasteboard and leaked bodies into the wrong thread") |
| `tools/compose/send.py:155` | `keystroke "v" using command down` inside `tell process "Mail"` | Nothing literal — pastes whatever is on the system pasteboard | Yes — see below |

**`send.py` line 155 in detail** (`_send_html_email`, `compose_email`'s
`body_html` path, lines 25-191):

- **What flows through it:** the caller's `body_html` (raw HTML string,
  e.g. `<b>Hello</b>`), written to a temp `.html` file (lines 94-102),
  read back with `do shell script "cat ..."` (line 110), and placed on the
  pasteboard as `NSPasteboardTypeHTML` data (lines 116-118). The Cmd+V paste
  at line 155 lands that HTML into the compose window body so Mail renders
  actual rich text; a `keystroke` of literal `<b>Hello</b>` would type the
  tags as plain text instead (this is the same reason `rich_draft.py:47-48`
  gives for preferring `.eml` generation over raw-HTML AppleScript
  assignment: "setting raw HTML through AppleScript often stores the literal
  markup instead").
- **Is the pasteboard saved/restored?** Partially. `oldClip` is captured as
  `pb's stringForType:(NSPasteboardTypeString)` **before** the overwrite
  (line 114) and restored via `pb's setString:oldClip forType:
  NSPasteboardTypeString` **after** send (lines 166-169), but only if
  `oldClip is not missing value`. This restores the **plain-text
  representation only** — if the original pasteboard held rich content in
  other UTIs (an image, RTF, a file reference), those are lost; only the
  string type is captured and restored. This is a pre-existing, narrower
  version of the exact clobber problem the reply-path docstring says was
  reverted for replies.
- **Why does `compose_email`'s HTML path paste while `reply_to_email`
  types?** Two different content types and two different risk profiles.
  `compose_email(body_html=...)` needs genuine **rich HTML** (bold, links,
  colors) in a **brand-new, empty** compose window with no existing thread to
  leak into, and Mail's object model cannot reliably render raw HTML assigned
  via `content of` — pasteboard-HTML + Cmd+V is the only way to get real
  formatting into the body. `reply_to_email`'s `reply_body` is always **plain
  text** inserted above Mail's own rich quote/signature in a window that
  already contains sensitive quoted content from a real thread; a clipboard
  overwrite there risks the exact failure mode the reverted implementation
  hit (clobbering whatever the user had copied, and — worse — a paste racing
  against window/focus state could land in the wrong compose window's
  thread). Keystroke was kept for reply specifically because it does not
  touch shared OS state.

`forward_email` (`forward.py:251-252`, comment lines 155-156, 251) and
`create_rich_email_draft` (`rich_draft.py`) use **neither** keystroke nor
clipboard for body insertion — see §4.

## 3. `_verify_saved_reply_draft`: behavior and limits

`plugin/apple_mail_mcp/tools/compose/saved_draft_checks.py:43-282`.

**Needle construction** (lines 57-71, called from `reply.py:430-441`):
- `safe_body_needle = escape_applescript(_first_non_empty_line(reply_body))`
  (line 59). `_first_non_empty_line` (`verification.py:24-30`) returns only
  the **first non-blank line** of `reply_body`, truncated to 500 chars. For a
  multi-line reply body only the first line is ever checked — the rest of the
  body (including a truncated tail) is invisible to this check.
- `safe_quoted_needle` is likewise only the first line of the passed
  `quoted_needle` (usually the literal string `"wrote:"` for the native path,
  set at `reply_scripts.py:503`).
- The AppleScript `replyBodyIsBeforeQuote` handler (lines 93-102) strips
  CR/LF from the draft's `content as string` first (`stripLineBreaks`, lines
  79-91) — this exists specifically because Mail's compose window soft-wraps
  long lines and `content as string` renders those wraps as line breaks
  (sometimes mid-word), which would otherwise defeat even a first-line
  substring match on a long reply.
- Then it does a plain `textOffset` substring search for the needle inside
  the flattened draft content and classifies as `found` / `after_quote` (body
  found but after the quote marker) / `missing`.

**Case sensitivity:** the substring search uses AppleScript's default
`textOffset`/`text item delimiters` comparison, which is **case-insensitive**
unless wrapped in `considering case`. This script never uses `considering
case`. So a draft whose body was typed in ALL CAPS (Bug 3) still matches a
mixed-case `replyBodyNeedle` — confirmed by the field report and structurally
true from the code (no `considering case` block anywhere in this file or in
`text_offset_handler()` in `applescript_snippets.py`).

**Quoted-original ordering check:** `replyBodyIsBeforeQuote` (lines 93-102)
returns `"found"` only when the body needle's offset is strictly less than
the quoted-needle's offset (or the quoted needle is empty/not found), else
`"after_quote"`. This is a real ordering check, but it only orders the
**first line** of the body relative to the quote — it says nothing about
whether the rest of the body is present, truncated, or mangled in case.

**Artifact id capture:** the outer script first tries an **exact id** lookup
(`every message of draftsMailbox whose id is targetDraftId`, lines 218-227)
when a `draft_id` was returned by the compose step. If that id is not yet
resolvable (Drafts sync lag — see the "unstable draft ids" observation in the
task context), it falls back to a **bounded newest-N scan** (`headEnd` capped
at `DRAFT_LIST_CAP`, lines 230-257) filtered by subject match, and returns
whichever of `FOUND` / `BODY_AFTER_QUOTE` / `BODY_MISSING` it first hits
across up to 20 retry passes with 1s delays (lines 215, 259-261 — up to ~20s
of polling before giving up). `_reply_verification_from_output`
(`verification.py:46-81`) parses the pipe-delimited response into
`_ReplyDraftVerification` (`ok`, `status`,
`matched_artifact_id`/`body_missing_artifact_id`, attachment/signature
status).

**Cleanup semantics:** this function is read-only/probe-only — it never
deletes anything. "Cleanup" is left to the **caller**: on verification
failure, `_reply_draft_verification_error` (`verification.py:190-234`)
surfaces the matched/missing artifact id in `remediation.artifact_message_id`
/ `remediation.draft_id` and tells the agent to inspect/delete it via
`verify_draft` or `manage_drafts(action="delete", draft_id=...)`.

**JSON fields from `_reply_success_payload`** (`verification.py:126-158`,
only reachable for `mode="draft"`/`"open"` per `reply.py:215-216`): `mode`,
`sent` (always `False`), `subject`, `draft_id` (Mail-returned id, or the
verified fallback id if Mail's own id capture failed), `captured_draft_id`
(exactly what Mail returned, may be `None`), `draft_id_source`
(`mail_returned`/`verification_fallback`/`unavailable`), `verified_draft_id`,
`verification_status`, `exact_id_verified` (true only when the verified id
equals the Mail-returned id, `_reply_exact_id_verified`, line 84-86),
`body_present` (`status == "found"`), `attachment_status`,
`attachment_count`, `attachments_applied`, `signature_status`, `mailbox`.
Note `body_present` is **first-line presence**, not full-body presence — a
truncated-but-first-line-intact draft reports `body_present: true`.

## 4. `forward_email` and `create_rich_email_draft`: not in the truncation class

- **`forward_email`** (`tools/compose/forward.py:202-296`): builds the full
  body in Python/AppleScript string concatenation (`fwdLeadText & fwdHeader &
  origContent`, line 246) and assigns it in one shot via `make new outgoing
  message with properties {... content:fullBody}` (line 252). Comment at
  lines 154-156 and 251 is explicit: "Object-model draft: NO window, NO
  clipboard, NO System Events." There is no `keystroke` and no window-focus
  dependency anywhere in this tool. It cannot suffer the reply path's
  truncation or case-mangling failure modes because there is no UI-scripted
  typing step at all — the whole body is a single AppleScript property
  assignment. The optional lead `message` text goes through the same
  temp-file pattern as reply (`forward.py:157-173`), but only to avoid
  AppleScript string-escaping issues, not to avoid a keystroke step.
- **`create_rich_email_draft`** (`tools/compose/rich_draft.py:26-196`):
  builds a real MIME `EmailMessage` in Python (`email.message.EmailMessage`,
  lines 107-120: `Subject`/`From`/`To`/`Cc`/`Bcc`/`X-Unsent` headers,
  `set_content(plain_body)` + `add_alternative(rich_body, subtype="html")`),
  writes it to a `.eml` file (line 143), then `open -a Mail <path>`
  (line 156) which lets Mail parse the `.eml` natively (its own MIME parser,
  not AppleScript typing). The subsequent save uses
  `_save_new_compose_window_as_draft` (`helpers.py:265-330`), which is a pure
  object-model `save targetMessage` against the diffed new outgoing-message
  id — again no `keystroke`, no clipboard, no window-title guard. Not in the
  truncation class either.

Only `reply_to_email`'s native path (`native_format=True`, the default) types
the body via `keystroke`; every other compose tool inserts body text through
either a direct AppleScript `content of`/`make new outgoing message`
assignment or a locally-parsed `.eml` file.

## 5. Existing tests covering the reply native path and verification

All in `tests/compose/`. `_main_reply_script(scripts)`
(`test_compose_tools.py:33-38`) filters captured scripts down to the one
containing `"reply foundMessage"`; `_assert_ordered` (line 24-30) asserts
snippets appear in relative order (not exact adjacency), and
`_saved_reply_draft_output(...)` (line 49-67) fabricates the AppleScript
success-text the native/object-model script would return.

**`tests/compose/test_compose_tools.py`, class `ReplyToEmailSenderOverrideTests`
(starts line 958):**

| Test | Line | Pins |
|---|---|---|
| `test_reply_uses_native_mail_reply_and_preserves_native_quote_by_default` | 959 | Ordered sequence ending in the single literal `"keystroke replyBodyText"` (line 988); asserts `NotIn` for `set content of replyMessage`, `NSPasteboard`, `set the clipboard`, `'keystroke "v"'`. **Will break** if the fix replaces the single keystroke with a chunked/looped typing construct that no longer contains the literal substring `"keystroke replyBodyText"`, or if it needs a new ordered assertion for the chunk loop. |
| `test_reply_defaults_to_draft_mode` | 1462 | Same literal `"keystroke replyBodyText"` (line 1494) plus `NotIn` assertions for `set quotedOriginalText`, `set composedReplyContent`, `set content of replyMessage`. |
| `test_reply_draft_success_runs_bounded_saved_draft_verifier` | 1503 | Pins the exact verifier needle `'set replyBodyNeedle to "Reply body"'` (line 1528) — this is the literal first-line needle. **Will need updating** if the fix changes the needle to the full body or a different construction. |
| `test_reply_draft_verifier_falls_back_when_exact_id_is_not_yet_resolvable` | 1533 | Calls `compose_tools._verify_saved_reply_draft(...)` directly; asserts exact-id branch precedes the bounded-fallback branch in the emitted script (lines 1554-1558). |
| `test_reply_signature_verification_runs_when_signature_requested_without_resolved_name` | 1560 | `native_format=False` (object-model path) — not affected by a native-typing fix. |
| `test_include_signature_false_suppresses_default_signature_and_verifies_one_draft` | 2105 | `native_format=False`; pins `f'set replyBodyNeedle to "{body_sentinel}"'` (line 2159) where `body_sentinel` is only the **first line** of a two-line `reply_body` — direct evidence the needle is first-line-only by design/test contract today. |
| `test_native_default_skips_signature_verification` | 2163 | Pins `"set signatureWasRequested to missing value"` for the native default and `"reply foundMessage with opening window"`. Independent of the typing mechanism but co-located in the native script; would still need to pass after a typing-path change. |
| `test_native_reply_guard_abort_returns_focus_failed_error` | 2195 | Fakes a `GUARD_ABORT` AppleScript response directly (no real script assertions on the keystroke construct) — safe against a typing-mechanism change. |
| `test_native_reply_guard_abort_reports_suspected_artifact_id` | 2233 | Same shape as above. |
| `test_native_reply_subject_guard_mismatch_returns_distinct_error` | 2265 | Same shape as above (`GUARD_ABORT_SUBJECT`). |
| `test_native_reply_script_normalizes_double_re_subject_guard` | 2310 | Pins `"keystroke replyBodyText"` (line 2351) plus all the subject-core-match assertions (lines 2333-2348). Uses a **short** body (`"Thanks for the invite."`), so it exercises the guard/typing path but does not currently assert case-preservation (would be a natural place to add a Bug 3 regression assertion). |
| `test_reply_to_all_uses_native_mail_reply_all` | 1969 | Pins `"keystroke replyBodyText"` (line 1995) plus `"reply foundMessage with opening window and reply to all"`. |
| `test_reply_without_all_uses_native_plain_reply` | 2003 | Pins `"keystroke replyBodyText"` (line 2026) plus the absence of `"reply to all"`. |
| `test_windowless_fallback_disabled_without_ack` | 2353 | Asserts `mock_run.assert_not_called()` before any script runs — unaffected by native-path internals. |

**Object-model-path tests** (`native_format=False`, no `keystroke` at all —
pin `"set composedReplyContent to replyBodyText & return & return &
quotedOriginalText"` and `"set content of replyMessage to (composedReplyContent
as rich text)"` instead): `test_empty_reply_body_keeps_body_assignment_guarded`
(1021), the attachments/reply-to-all block around 1380-1460, `test_reply_signature_is_applied_before_body_insert`
(2034), `test_include_signature_false_still_inserts_reply_body` (2071),
`test_reply_draft_success_outputs_attachment_and_signature_verification`-style
tests around 1112-1350. These are **not** in the truncation class (§4-adjacent
reasoning applies: the object-model path assigns `content` directly, one
shot) and should be unaffected by a native-keystroke fix, but any change to
`_verify_saved_reply_draft`'s needle logic affects them too since they share
the same verifier.

**Pure verifier-output parser tests** (no live script, direct function
calls): `test_reply_verification_parser_preserves_pipe_in_attachment_filename`
(1274) and `test_reply_success_text_hides_attachment_count_when_not_requested`
(1287) call `compose_tools._reply_verification_from_output(...)` directly with
hand-built pipe strings — these pin the `FOUND|id|attach|sig|count|rows`
wire format itself (`verification.py:46-81`) and would need new fixtures if
the wire format grows a full-body-verified flag.

**`tests/compose/test_compose_security.py:179`
(`test_bug2_html_temp_path_uses_quoted_form_in_reply`):** asserts every
AppleScript emitted by a `reply_to_email(body_html=...)` call uses `quoted
form of` around the temp-file path (both `cat` and `rm -f`). This "Bug 2" is
an unrelated historical shell-quoting bug from a different fix, **not**
AGENTIC-1214's Bug 2 (`in_reply_to` ignored on create) — same label, two
different bugs; worth flagging so the naming doesn't get confused when
writing the fix's own regression tests.

**`tests/compose/test_compose_none_handling.py`:** `test_reply_with_message_id_not_found_returns_string`
(145), `test_reply_with_subject_keyword_returns_structured_deprecation_error`
(158), `test_reply_to_email_no_none_in_applescript` (235),
`test_reply_with_none_cc_bcc_no_none_literal` (424) — None-literal /
error-shape regression tests, independent of the typing mechanism.

**`tests/compose/test_draft_verification_helpers.py`:** covers only
`_build_verify_draft_payload` (the `verify_draft`/`verify_drafts` tool
payload builder in `apple_mail_mcp/tools/draft_verification.py`), **not**
`_verify_saved_reply_draft` or `_reply_verification_from_output` — those are
exercised only inline inside `test_compose_tools.py` as shown above. There is
currently no dedicated test module for the reply-specific verifier; a fix
that reworks needle construction will touch tests spread across
`test_compose_tools.py` only.

**Manage-drafts Bug 2 coverage:** grep found no existing test asserting that
`manage_drafts(action="create", in_reply_to=...)` either threads the draft or
explicitly documents/refuses the no-op — `ManageDraftsCreateSenderOverrideTests`
(`test_compose_tools.py:2673`) covers sender override only. This confirms the
field report: there is no regression test today that would catch Bug 2
(`in_reply_to` silently ignored on `action="create"`).

## 6. Timeout budget math (native reply script)

Effective timeout: `reply_to_email(timeout=None)` → `reply.py:338-340` calls
`compose.run_applescript(script)` with no `timeout` kwarg →
`core/applescript.py:44`, `effective_timeout = 120 if timeout is None else
timeout`. **Default budget: 120s** for the whole native script (subject
lookup + window open + guard loop + keystroke + save/close), enforced by
`subprocess.run(..., timeout=effective_timeout)` (line 53) which raises
`AppleScriptTimeout` on expiry (mapped to a `str` error in `reply.py:460-463`).
`_verify_saved_reply_draft` runs as a **separate** `run_applescript` call
with its own budget (`saved_draft_checks.py:71`: `verification_timeout = 60
if timeout is None else max(30, min(timeout, 120))`), so it does not eat into
the 120s compose-script budget.

Explicit `delay` statements inside `_build_reply_native_window_applescript`
(`reply_scripts.py`, grep-verified list of every `delay` literal):

| Line | Delay | When |
|---|---|---|
| 384 | 1.2s | Immediately after `reply foundMessage ...` opens the window |
| 386 | 0.4s | After the first `activate` |
| 434 | 0.3s | Top of each guard-loop attempt, after re-`activate` |
| 438 | 0.3s | After `set frontmost to true` |
| 447 | 0.3s | Before capturing `guardSE` (System Events front-window title) |
| 476 | 0.5s | End of a **failed** guard-loop attempt (before retrying, up to 3 times) |
| 506 | 0.4s | After the guard loop, before the mode-specific post-action |
| 216/218/221 (`_native_reply_post_action`) | 0.5s (send) / 0.8s (open) / 1.0s (draft) | After `send`/`save` |

Plus, only if `attachments` were passed: `reply.py:258`, `delay 1` **per
attachment**, injected before the guard loop even starts (attachment script
runs inside the same `tell application "Mail"` block that opens the window).

**Sums** (no attachments, `mode="draft"`, the field-report default):

- **Best case** (guard matches on attempt 1 — 0.3+0.3+0.3 = 0.9s in the
  loop, no trailing 0.5s): `1.2 + 0.4 + 0.9 + 0.4 + 1.0 = 3.9s`.
- **Worst case that still succeeds** (matches on attempt 4 of 4 — three
  failed attempts at 0.3+0.3+0.3+0.5=1.4s each, plus one successful 0.9s):
  `1.2 + 0.4 + (3×1.4 + 0.9) + 0.4 + 1.0 = 8.1s`.
- **Guard exhausted, `GUARD_ABORT` returned** (all 4 attempts fail, no
  post-action delay incurred): `1.2 + 0.4 + 4×1.4 = 7.2s`.

None of these `delay` statements scale with `reply_body` length — the
`keystroke replyBodyText` call itself (line 470) is a single AppleScript
command with no explicit `delay` around or inside it. Its wall-clock cost for
a ~1000-character string is entirely internal to System Events' CGEvent
posting loop, bounded only by the outer 120s subprocess timeout, not by any
delay literal in this script. That makes the keystroke call itself — not the
explicit delay budget — the most likely place where a per-character
throttle, coalescing limit, or shift-state carry-over (Bug 3) originates.

**Headroom for a chunked-typing fix:** even the worst realistic (still
successful) path consumes only ~8.1s of the 120s budget — **≈112s of
headroom** before hitting the timeout. Adding attachments costs +1s each
(negligible at realistic attachment counts). A chunked-typing approach (e.g.
splitting `replyBodyText` into fixed-size chunks with a short inter-chunk
`delay` to let Mail's text view and System Events settle) has enormous room:
even a conservative 50 chunks × 0.5s inter-chunk delay = 25s, or 20 chunks ×
0.3s = 6s, both land comfortably inside the ~112s headroom without touching
the default 120s ceiling, let alone requiring a caller-supplied larger
`timeout`. The task context's note that "`timeout=240` did not change it" is
consistent with this: the truncation is not a timeout-budget problem, it is
almost certainly a per-call keystroke throughput/coalescing limit that a
bigger `timeout` cannot fix because the `keystroke` command returns (or the
draft settles) well before 120s even elapses.

For contrast, the **object-model** reply script
(`_build_reply_objectmodel_applescript`, `reply_scripts.py:90-205`) has no
keystroke-adjacent delays at all: `_reply_command_options` (line 45-54)
returns an empty settle delay for `mode="draft"`/`"send"` and only `"delay
0.6"` for `mode="open"` (used only to let the window render before the
`content of` assignment, not before a keystroke) — because the body is
assigned in one `set content of replyMessage to (composedReplyContent as rich
text)` statement (line 170), there is no typing-throughput concern on that
path at all, which is consistent with it never having exhibited the
truncation bug.

## 7. Body temp file lifecycle

**Who writes:** `reply.py:229-237` (`reply_to_email`) writes the **full,
untruncated** `reply_body` string to
`tempfile.NamedTemporaryFile(mode="w", suffix=".txt", prefix="mail_reply_",
delete=False, encoding="utf-8")` before either AppleScript branch is built.
The path is captured as `body_temp_path` and threaded into both the native
and object-model script builders (`reply_scripts.py`). Analogous pattern
exists in `forward.py:161-169` for the optional lead `message` (different
temp file, `prefix="mail_fwd_"`), and in `send.py:94-102` for the HTML body
(`prefix="mail_html_"`).

**Who reads:** the AppleScript itself, via `do shell script "cat " & quoted
form of "{body_temp_path}"` — native path at `reply_scripts.py:378`
(`replyBodyText`), object-model path at line 154. This is a **full read**
(no truncation happens here); the AppleScript variable `replyBodyText` always
holds the complete body. Truncation, when it happens, occurs later at the
`keystroke replyBodyText` step (native path only, §6), never at this read.

**Who deletes, and when:** three layers, in order of how often each actually
fires:
1. **Inside the AppleScript itself**, via `cleanup_script` (`reply.py:268`,
   `do shell script "rm -f " & quoted form of "{body_temp_path}"`), invoked
   at **every** exit point of the native script: not-found (line 367),
   guard-abort (line 499), success (line 517), and the top-level `on error`
   handler (line 535). Same pattern in the object-model script (success line
   195, error line 200).
2. **Python `finally` block** (`reply.py:466-469`): `Path(body_temp_path).
   unlink(missing_ok=True)` wrapped in `suppress(OSError)`, runs
   unconditionally after the `try` block regardless of which return path was
   taken. This is the backstop for the case where `AppleScriptTimeout` killed
   `osascript` mid-script, before its own `cleanup_script` line ever ran.
3. There is **no** deferred/lazy deletion — by the time control returns from
   `compose.run_applescript(...)` at `reply.py:338-340`, the temp file is
   already gone in the overwhelmingly common case (AppleScript's own cleanup
   ran as part of the script), and it is *guaranteed* gone by the time
   `reply.py` returns to its caller (the Python `finally` block).

**Implication for full-body post-save verification:** the temp file is
**not** available to reuse by the time `_verify_saved_reply_draft` runs
(`reply.py:430`, called after `run_applescript` has already returned and,
per the above, already deleted the file). But this is not actually a
blocker: `reply_body` is still a live Python string in `reply_to_email`'s own
stack frame at that point (it is passed directly into
`_verify_saved_reply_draft(..., reply_body, ...)` at `reply.py:433`) — the
full body was never *unavailable* to Python, only unavailable to the
**AppleScript verifier subprocess**, which only receives whatever is baked
into the generated script via `escape_applescript(...)` literals (as
`safe_body_needle` does today, `saved_draft_checks.py:59`, but only for the
first line). A full-body verification fix has two live options that do not
require resurrecting the deleted temp file: (a) write a **second**, separate
temp file at verification time (or keep the original file open and read it
before its cleanup fires) so the verifier script can `cat` the full body
instead of embedding it as an inline `escape_applescript` literal (avoids any
AppleScript double-quoted string length practicality limits for a ~1000+
char needle); or (b) keep embedding a literal needle but chunk/compare the
full body in Python after fetching the draft's real content via the existing
`verify_draft`-style content read, rather than only substring-matching a
needle inside AppleScript. Either way, the fix does not need the *original*
`mail_reply_*` temp file to survive past `run_applescript` — it only needs a
decision about how the (still fully in-memory) `reply_body` reaches the
verifier AppleScript process.

## Cross-references confirmed against the task's stated root-cause chain

- `reply_scripts.py:470` (`keystroke replyBodyText`, single call, whole body)
  — confirmed as the only body-typing keystroke in the package (§2, §4).
- `saved_draft_checks.py:59` (`_first_non_empty_line(reply_body)` needle) —
  confirmed; also confirmed the AppleScript-side `textOffset`/`text item
  delimiters` comparison used to search for that needle is case-insensitive
  by default with no `considering case` block anywhere in this file (§3),
  which structurally explains why ALL CAPS drafts (Bug 3) still pass.
- `manage_drafts(action="create")` ignoring `in_reply_to` — confirmed:
  `manage.py:136-226` builds the create script purely from `subject`/`body`/
  `to`/`cc`/`bcc`/sender, with `in_reply_to` referenced nowhere in that
  branch (only used in the `action="find"` branch, `manage.py:126-134`
  → `drafts_scripts.py`'s `_build_manage_drafts_find_script`). The reply-like
  guard that blocks the first call (`_standalone_compose_thread_warning`,
  `payload.py:117-145`, invoked at `manage.py:140`) takes `subject`/`body`/
  `standalone_confirmed` only — it has no `in_reply_to` parameter and its
  returned message (`payload.py:138-145`) never mentions `in_reply_to` or
  explains that `action="create"` cannot thread a draft, matching the field
  report exactly.
- Clipboard-reverted-for-reply history — confirmed in `reply_scripts.py`
  docstrings (module docstring lines 1-6, `_build_reply_native_window_
  applescript` docstring lines 320-337) and structurally confirmed by grep:
  the only clipboard/pasteboard code in the whole package lives in
  `send.py` (compose's HTML path), not in any reply code path.
- `content of replyMessage` never reassigned on the native path — confirmed:
  `reply_scripts.py:295-539` (`_build_reply_native_window_applescript`) has
  no `set content of replyMessage` anywhere; multiple tests pin this
  (`test_compose_tools.py:993, 1497, 1498, 2350`).
- `send.py:155` cmd-V paste — confirmed unrelated to reply; flows HTML body
  content for `compose_email`'s rich-formatting path only (§2).
