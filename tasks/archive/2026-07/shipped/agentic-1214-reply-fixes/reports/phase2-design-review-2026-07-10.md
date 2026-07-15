# Phase 2 adversarial design review: AGENTIC-1214 reply fixes plan

Reviewed: `tasks/active/agentic-1214-reply-fixes/plan-undefined.md`, all four
phase-1 reports, and the live code end to end (`reply_scripts.py`, `reply.py`,
`saved_draft_checks.py`, `verification.py`, `manage.py`, plus `payload.py`,
`applescript_snippets.py`, `send.py`, `constants.py`, the compose facade, the
module-budget baseline, and the cited test anchors in
`tests/compose/test_compose_tools.py`).

Verdict: **AMEND**. The architecture is right (chunked typed insertion, no
clipboard, no `content` reassignment, full-body case-sensitive verify, hard
refusal on `create` + `in_reply_to`, docs-only for id drift). But the plan as
written contains two confirmed correctness bugs, one safety gap that widens an
existing cross-app leak window, and several deterministic false-mismatch classes
that would make the new delete-and-retype loop destroy good drafts. All are
fixable within the plan's structure; none require a different mechanism.

---

## 1. Confirmed defects in the plan (must fix before implementation)

### 1.1 `flattenForCompare` whitespace strip is a no-op (BLOCKING)

Plan B.2:

```applescript
repeat with stripDelim in {return, linefeed, tab, space, (character id 160)}
    set AppleScript's text item delimiters to (contents of stripDelim)
    set t to (text items of t) as string
end repeat
```

List-to-string coercion joins with the CURRENT text item delimiters. At the
join, the delimiters are still set to the strip character, so the split text is
rejoined with the exact character it was split on. `t` is unchanged for every
delimiter in the loop. The repo's own `stripLineBreaks`
(`saved_draft_checks.py:79-91`) and `sanitize_field_handler`
(`applescript_snippets.py:16-34`) both reset the delimiters before joining,
which is the correct idiom the plan's loop must copy:

```applescript
set AppleScript's text item delimiters to (contents of stripDelim)
set parts to text items of t
set AppleScript's text item delimiters to ""
set t to parts as string
```

Consequence if shipped as written: the "flattened" draft content still carries
Mail's soft-wrap line breaks and spaces at positions the source body does not
have, so the case-sensitive `contains` fails on virtually every wrapped body,
the retry deletes a good draft, retypes, fails again, and every long reply ends
in a false `REPLY_BODY_MISMATCH`. This single bug would make the branch strictly
worse than the current first-line needle. The script-shape unit tests cannot
catch it (they assert handler names, not behavior), so it must be fixed in the
plan text, and the live gate must include a multi-line wrapped body.

### 1.2 Per-chunk focus re-check does not prove Mail has system focus (BLOCKING)

Plan A.2 re-checks focus between chunks by reading `name of front window`
through the **Mail dictionary** only. Mail's dictionary `front window` is the
frontmost window OF MAIL even when Mail is in the background. System Events
`keystroke` posts events to the system-focused application; the
`tell process "Mail"` wrapper does not redirect key events (that is why the
existing guard sets `frontmost to true` and reads the System Events front
window before the single keystroke, `reply_scripts.py:435-451, 466`).

So under the plan: user switches to Slack at chunk 7 of 13; the Mail-dictionary
check still returns the reply subject; the check passes; chunks 7-13 type the
reply body into Slack. Today's single-keystroke path holds this race open for
milliseconds; the chunked path holds it open for 4-25+ seconds, so the plan
materially widens the one leak class the whole doctrine exists to prevent.

Fix: the per-chunk check must mirror the pre-typing guard's `seOk` semantics:
inside `tell application "System Events" / tell process "Mail"`, verify
`frontmost is true` and that the SE front-window title equals the adopted or
derived subject or is empty (the tolerated AX quirk). On any mismatch abort
with `interrupted:`; do not re-steal focus mid-typing.

### 1.3 Final error dispatch contradicts itself (B.4 vs B.6 vs test F.6)

B.4's control flow routes every non-`body_after_quote` failure, including
`not_found`, `verification_timeout`, and `applescript_error`, to
`_reply_body_mismatch_error`. B.6 says `_reply_draft_verification_error` is
kept for `body_after_quote`, `verification_timeout`, and `applescript_error`,
and test F.6 expects `NOT_FOUND` to return "the existing no-id error path".
As written, a verifier timeout would be reported as "the artifact does not
contain the full reply body", which is false (nothing was compared). Fix the
dispatch: `REPLY_BODY_MISMATCH` fires only for `body_missing` (and optionally
`body_after_quote` if the codes are merged); `not_found` keeps the plain
"Mail did not verify it" message; timeout and applescript_error keep
`_reply_draft_verification_error`.

### 1.4 `replyBodyAboveQuoteStatus` regresses on bodies containing "wrote:"

The handler locates the FIRST occurrence of the flattened quote needle
(`wrote:`) in the flattened draft. If the reply body itself contains "wrote:"
("As Keynes wrote: ..."), that first occurrence is inside the typed body, so
`aboveQuote` is cut mid-body, the containment fails, `after_quote` is returned,
and the retry deletes a good draft. The current shipped check does NOT have
this failure (first-line offset is always before an in-body "wrote:"), so this
is a new regression class introduced by the plan. Fix: locate `flatBody` in
`flatDraft` first (case-sensitively, under `considering case`); if found,
require any quote occurrence to be at an offset >= bodyOffset + (length of
flatBody), or search for the quote needle only in the text after the body
match. Treat a quote needle that only appears inside the body region as "no
quote boundary".

---

## 2. False-mismatch classes that trigger the destructive retry on good drafts

The delete-and-retype step makes verifier false negatives destructive: a false
`body_missing` deletes a draft that was fine. Each class below is deterministic
per host setting, so "retry once" does not save it; both attempts fail the same
way and the caller gets a false `REPLY_BODY_MISMATCH` after the original draft
was destroyed and replaced.

### 2.1 Smart-dash hyphen-count asymmetry

Body contains `--`. Mail's smart-dash substitution (live by default per the
domain report) stores an em dash. The fold maps em dash to a SINGLE `-`, but
the source side keeps `--`. One char vs two: mismatch. Fix: after the dash
folds, collapse hyphen runs to a single `-` on BOTH sides (two `foldPair`
passes: `---` to `-`, then `--` to `-`), or delete `-` entirely from both
sides. Same audit should be applied to the ellipsis fold (`...` matches a
source `...`, fine, but a source `..` followed by substitution does not occur;
no action needed there).

### 2.2 Sentence auto-capitalization

macOS "Capitalize words automatically" (if honored by Mail's WebKit editor)
rewrites a lowercase sentence start: typed "thanks Geoff. best, Cayman" is
stored as "Thanks Geoff. Best, Cayman". The case-sensitive compare fails; the
documented B.2 fallback (alphanumeric-only projection, case preserved) does
NOT cover this, because the projection preserves exactly the letters whose
case changed. Casual reply bodies with lowercase sentence starts are common in
this repo's own field reports. Fix, pick one and write it into the plan:
(a) after flattening, fold the first alphabetic character at text start and
after each of `.`, `!`, `?` to lowercase on both sides before the
case-sensitive compare; or (b) keep the strict compare but gate the mismatch:
when the case-sensitive check fails and the case-insensitive check passes,
fail only if the draft slice shows an uppercase-ratio anomaly (the ALL-CAPS
signature), otherwise pass with a warning. Either preserves Bug 3 detection
(ALL CAPS changes every letter, not just sentence starts). The live gate must
include a lowercase-sentence-start body and must record the host's actual
Edit > Substitutions / autocorrect state in Mail.

Note: spelling autocorrect ("teh" to "the") remains an irreducible mismatch
class under any lexical compare; a mismatch there is arguably truthful (the
draft really differs from the requested body). The bounded single retype caps
the damage. Live report should note whether autocorrect is on.

### 2.3 Retry delete relies on the id the same plan documents as unstable

Section D documents Exchange reassigning Drafts ids across pure re-list calls
(103 -> 91058 -> 91061). B.5 deletes attempt-1's artifact by that same numeric
id, in a `try` block that swallows everything and returns nothing. If the id
drifted between verify and delete, nothing is deleted, the retype proceeds,
attempt 2 verifies FOUND, and the tool returns SUCCESS while a truncated
duplicate silently remains in Drafts. Fix: make the delete script return a
token (`DELETED|id` / `NOT_FOUND|id`); when deletion is unconfirmed, surface a
warning plus the stale artifact id in the retype success payload
(`stale_artifact_id` or a `warnings` entry) and in the REPLY_BODY_MISMATCH
remediation, so cleanup automation can act.

---

## 3. Gaps in the typed path

### 3.1 No timeout scaling for long bodies (new failure mode)

Typing time now scales linearly: chunks x (keystroke time + 0.35s). A ~20k
char body needs ~88s of delays plus ~8s overhead against the 120s default;
anything larger blows the budget. `AppleScriptTimeout` kills osascript
MID-TYPING: no abort branch runs, no `close ... saving no`, the partially
typed compose window stays open, and only the Python `finally` unlinks the
temp file. Worse, a retry call's guard can AXRaise that stale window (same
title) and type the full body after the partial text; the full-body
containment check then PASSES on a draft whose top carries a garbage partial
prefix. Today's single keystroke does not have a duration that scales with
body length, so this is a new regression class. Fix in Python: when the caller
passes `timeout=None`, compute projected typing seconds from
`len(reply_body)`, `CHUNK_SIZE`, and `INTER_CHUNK_DELAY`, and pass
`max(120, projected + fixed_overhead + slack)` to `run_applescript`; or refuse
bodies whose projected typing exceeds a documented cap with a structured error
naming the `timeout` parameter. Also document that the process-wide
single-flight AppleScript lock serializes other Mail tools behind the full
typing duration.

### 3.2 Tabs in reply_body

`keystroke` of a tab character can move keyboard focus out of the body (full
keyboard access) or trigger editor indentation. If a chunk lands in the
Subject field, the window TITLE mutates, so (a) the next chunk's title check
fails, and (b) the abort's `close (every window whose name is replySubject)`
no longer matches the mutated title, leaving a corrupted compose window open.
The plan never mentions tabs. Fix in Python, before writing the temp file:
convert `\t` to spaces (documented in the docstring and CHANGELOG). This also
keeps the AppleScript side simple; do not try to type tabs safely.

### 3.3 Chunk-boundary and modifier-hygiene details

- Prefer a trailing space as a secondary chunk-boundary fallback when no
  newline is in the window, so words are not split across the inter-chunk
  pause (cheap: same backward scan, second target).
- Release shift immediately AFTER each chunk keystroke as well as before. The
  Apple-forums fix released shift after typing; short bodies fit in ONE chunk,
  so under the plan they get only the pre-clear and are otherwise identical to
  today's failing call. The post-keystroke `key up shift` is the only
  in-script hygiene a one-chunk body receives after its keystroke.
- Bug 3 mitigation honesty: for one-chunk short bodies the fix relies mostly
  on the case-sensitive verifier catching the miscase and the single retype
  repairing it (the field report says the symptom is intermittent). That is an
  acceptable design, but it makes fixing 1.1 and 2.2 non-negotiable, since the
  verifier is the actual Bug 3 backstop.
- Unicode: AppleScript `text i thru j` is grapheme-aware for composed
  characters and surrogate pairs; ZWJ emoji sequences may still split across
  two keystroke calls at a chunk boundary. Content scalars concatenate
  identically so verification is unaffected; note it in the live report if an
  emoji body is probed. No plan change required.

### 3.4 mode="send" gets chunked typing but no verification

The native send path types then sends; verification only runs for draft/open
(`reply.py:419`). A residual truncation or miscase in send mode goes out
unverified. This is pre-existing, but the plan should state it explicitly in
the CHANGELOG/docs and reaffirm the draft-first sequence
(draft -> verify_draft -> manage_drafts(action="send")) as the safe path for
consequential sends.

---

## 4. Smaller plan corrections

1. **TYPING_BOUNDS typing**: a dict literal mixing int and float infers
   `dict[str, float]` under mypy --strict, and `text 1 thru 80.0 of` is an
   AppleScript error. Use two module-level `Final` constants
   (`TYPING_CHUNK_SIZE: Final[int]`, `TYPING_INTER_CHUNK_DELAY: Final[float]`)
   or a TypedDict; keep `chunk_size: int` in the builder signature.
2. **Retry mechanics under-specified** (B.4): rewrite the SAME
   `body_temp_path` (AppleScript's `rm -f` removed the file but the built
   script embeds that path, so reusing it avoids a script rebuild and keeps
   the single `finally` unlink correct); re-extract `Subject`, `Draft ID`, and
   `Quote Needle` from the second run's output before re-verifying; route a
   second-run `GUARD_ABORT*`, `TYPING_INTERRUPTED`, or non-success result
   through the existing branches instead of re-verifying.
3. **Python-source brace escaping** (A.2): the handler text contains literal
   AppleScript braces (`key up {shift, ...}`, `{return, linefeed, ...}`);
   inside a Python f-string these must be doubled. The plan shows the emitted
   AppleScript only; say so explicitly so the implementer does not paste it
   verbatim into an f-string (the osacompile hook will catch it, but the plan
   should not rely on the hook for known issues).
4. **CREATE_CANNOT_THREAD ordering**: placing the refusal after the
   required-args check means a caller missing `body` learns about the
   threading refusal only on the second call. Acceptable, but consider
   checking `in_reply_to` first since it is the more fundamental contract
   error. Non-blocking.
5. **Interrupted detail hygiene**: `"interrupted:" & frontName` embeds an
   arbitrary window title into the line-oriented output that
   `_extract_output_field` parses; a title containing a return would inject
   lines. The GUARD_ABORT path has the same exposure today, so this is not
   new, but flattening the title through the existing sanitize idiom is one
   line. Optional.

---

## 5. B2 decision (public verify_draft quote false-pass)

Recommendation: **INCLUDE**. The new `REPLY_BODY_MISMATCH` remediation tells
agents to "inspect the draft with verify_draft(draft_id=...)". If
`verify_draft`'s `expected_body_contains` still matches text found only in the
quoted original (`draft_verification.py:105` against the full
`content as string`), the recommended remediation loop can false-clear the
exact defect this branch exists to catch. The fix is small (scope the needle
to the above-quote region, add a warning when the needle appears only below)
and the test seam (`tests/compose/test_draft_verification_helpers.py`) already
exists. If the lead defers it anyway, reword the REPLY_BODY_MISMATCH
remediation so it does not direct agents to a check known to false-pass, and
put AGENTIC-1192 item 2 in the forward queue explicitly.

---

## 6. Repo-rule and scope audit (passes, with notes)

- **Forbidden whose**: B.5's `every message of draftsMailbox whose id is N` is
  the allowed exact-id predicate (same pattern at `saved_draft_checks.py:221`,
  `manage.py:103`). The `close (every window whose name is ...)` abort is a
  window predicate already shipped in the GUARD_ABORT path. No new violations.
- **Module budget**: baseline `modules` is `{}` confirmed, so any file
  crossing 600 fails CI. New leaf `typing_scripts.py` keeps
  `reply_scripts.py` (539) safe; reply.py (469) plus the retry loop, the
  TYPING_INTERRUPTED branch, and the delete helper will approach the high 500s,
  so the plan's `reply_runner.py` extraction trigger at ~560 is correct; treat
  it as expected, not optional.
- **Pipe fields**: wire tokens unchanged; no new user text enters pipe rows.
- **Tests**: cited anchor lines verified against the file (988, 1494, 1528,
  2159, 2351 all real). All new tests mock `run_applescript`; nothing needs
  live Mail. Remember `tools/expected_test_count.txt` (currently 1392).
- **Guard parameterization**: all three `_standalone_compose_thread_warning`
  call sites pass positionally, so the trailing `tool_name` kwarg with a
  default is backward compatible. Confirmed at `send.py` (compose_email),
  `manage.py:140`, `rich_draft.py:79`.
- **Version/merge order**: pyproject and server.json confirmed at 3.10.1;
  `feat/reply-state-annotation` exists locally and on origin. The plan's H
  caveat (merge 3.11.0 first, else renumber) is accurate and should be quoted
  in the handoff verbatim.
- **Issue coverage**: guard message fix (C.2), draft-id docs (D),
  forward_email statement (E), JSON contract additions (B.7 + G.2) all
  present. `manage_drafts(action="create")` breaking change for Agentic-Inbox
  is justified: the current behavior silently produces exactly the broken
  artifact the field report complains about, and the refusal names the correct
  replacement tool. Note the break prominently in the CHANGELOG so the
  Agentic-Inbox lane updates its flow.

---

## 7. What live verification MUST exercise (draft mode only)

1. The field-report regression: ~1000-char body and a ~5000-char multi-line
   body, 3 runs each on the Exchange account; verify_draft shows the complete
   body above the quote in original case, with the rich quote bar and logo
   signature intact, and the tool returns success without a retype.
2. Short-body ALL CAPS probe: 3 short casual bodies; correct case in the saved
   draft, and no false REPLY_BODY_MISMATCH from the case-sensitive verifier.
3. Substitution probe: one body containing straight quotes, `--`, `...`, an
   accented name, and a lowercase sentence start; must verify successfully
   (no delete-and-retype of a good draft); record Mail's Edit > Substitutions
   and autocorrect state in the report.
4. Mid-typing interruption: steal focus (switch apps and click another Mail
   window in separate runs) during a long body; expect
   REPLY_BODY_TYPING_INTERRUPTED, no partial compose window left open, no
   partial draft in Drafts (or its id surfaced as suspected_draft_id), and
   critically NO text typed into the other application.
5. Retype path on Exchange: induce a mismatch (tune CHUNK_SIZE up or use a
   forced miscase) and confirm the attempt-1 artifact is actually deleted so
   exactly one draft remains; if the id drifted and the delete missed, confirm
   the stale-artifact warning surfaces.

---

## 8. Amendment list (imperative, one sentence each)

1. Fix `flattenForCompare`'s strip loop to reset text item delimiters to ""
   before the list-to-string join, matching the repo's stripLineBreaks idiom,
   because as written the whitespace strip is a no-op and every wrapped body
   false-mismatches.
2. Extend the per-chunk focus re-check to System Events state (process "Mail"
   `frontmost is true` and SE front-window title equal to the adopted or
   derived subject or empty), aborting on mismatch, because the Mail-dictionary
   front window check passes even while another app holds system focus and
   keystrokes would leak into it.
3. Correct the B.4 error dispatch so `REPLY_BODY_MISMATCH` fires only for
   `body_missing` (optionally `body_after_quote`), while `not_found` keeps the
   plain no-artifact message and `verification_timeout` / `applescript_error`
   keep `_reply_draft_verification_error`, consistent with B.6 and test F.6.
4. Rework `replyBodyAboveQuoteStatus` to locate the flattened body first under
   `considering case` and only treat quote-needle occurrences after the body
   match as the quote boundary, so reply bodies containing "wrote:" do not
   false-fail and trigger a destructive retype.
5. Collapse hyphen runs to a single "-" on both sides after the dash folds (or
   delete "-" entirely from both sides) so smart-dash substitution of "--"
   cannot false-mismatch.
6. Neutralize sentence auto-capitalization: fold the first letter at text
   start and after each ".", "!", "?" to lowercase on both compare sides (or
   gate mismatch on an uppercase-ratio anomaly when only case differs), and
   add a lowercase-sentence-start body to the live gate, because the
   alphanumeric-projection fallback does not cover this class.
7. Convert tabs in `reply_body` to spaces in Python before writing the temp
   file, and document it, because a typed tab can move focus out of the body
   and a subject-field keystroke mutates the window title that the abort's
   close-by-name depends on.
8. Scale the compose-script timeout with projected typing time (chunks x delay
   plus fixed overhead plus slack) when the caller passes timeout=None, or
   refuse over-budget bodies with a structured error, so AppleScriptTimeout
   cannot kill osascript mid-typing and leave a partial compose window open.
9. Make the B.5 delete script return a DELETED/NOT_FOUND token and surface a
   stale-artifact warning with the id in the retype success payload and in
   REPLY_BODY_MISMATCH remediation when deletion is unconfirmed, because
   Exchange id drift can strand a truncated duplicate behind a success result.
10. Specify the retry mechanics: rewrite the same `body_temp_path`, re-extract
    Subject / Draft ID / Quote Needle from the second run's output, and route a
    second-run GUARD_ABORT / TYPING_INTERRUPTED / non-success result through
    the existing branches before re-verifying.
11. Release shift immediately after each chunk keystroke in addition to the
    pre-chunk clear, since short bodies fit one chunk and otherwise receive no
    in-script modifier hygiene after typing.
12. Replace the mixed-type TYPING_BOUNDS dict with typed Final constants (or a
    TypedDict) so mypy --strict passes and chunk_size stays an int in the
    generated AppleScript.
13. Include B2 (scope verify_draft's expected_body_contains to the above-quote
    region with an only-in-quote warning), or if deferred, reword the
    REPLY_BODY_MISMATCH remediation so it does not point agents at a check
    known to false-pass and add AGENTIC-1192 item 2 to the forward queue.
14. State in the CHANGELOG and docs that mode="send" native replies get
    chunked typing but no post-save verification, reaffirming
    draft-then-verify-then-send as the safe sequence.
15. Add a trailing-space fallback to the chunk-boundary scan (after the
    newline preference) so words are not split across inter-chunk pauses, and
    note in the plan that the handler's literal AppleScript braces must be
    doubled inside the Python f-string.
