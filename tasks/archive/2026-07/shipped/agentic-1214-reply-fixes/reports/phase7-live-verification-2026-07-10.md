# AGENTIC-1214 reply-fix branch: live Mail.app verification (phase 7)

Branch `fix/agentic-1214-reply-body-truncation` @ `479d291` (v3.11.1, all gates
green). Account: `Cayman - Agentic Assets`. Editable venv `.venv/` installed
from this checkout (`pip show mcp-apple-mail` reports a stale `3.10.0`
metadata cache from egg-info, but the editable install location resolves to
this repo tree, and the running code visibly matches the branch: chunked
typing, `body_verified`/`retyped`/`stale_artifact_id` in the JSON payload,
`REPLY_BODY_TYPING_INTERRUPTED`, `CREATE_CANNOT_THREAD`, and
`body_needle_only_in_quote` all confirmed present and behaving as described
below).

All calls were made by importing `reply_to_email`, `verify_draft`, and
`manage_drafts` directly from `plugin/apple_mail_mcp/tools/compose/*` and
invoking them from small one-shot scripts under `.venv/bin/python`, one Mail
call at a time, per `docs/AGENT_LIVE_TESTING.md`. Every reply used
`mode="draft"` (never `mode="send"`). Every draft created in this session was
deleted before finishing (see Check 8).

Discovery targets were pulled from `list_inbox_emails(exclude_replied=True,
exclude_drafted=True)` on the account, restricted to automated/newsletter
senders (Ollama, IREI Newslines, Mercury, Linear notifications, Attio,
Google Calendar). No thread that already had a draft was used, and each
check used a different target message.

## Overall result: PARTIAL PASS

Five of eight checks passed cleanly (1, 2, 3, 6, 7). Check 4 surfaced a real
typing defect unrelated to the punctuation-normalization claim it was meant
to test. Check 5 did not reach either of the two outcomes the plan
anticipated; it produced a third outcome (a corrupted draft caught by
`REPLY_BODY_MISMATCH` instead of a clean `REPLY_BODY_TYPING_INTERRUPTED` or a
clean success). No email was sent in any check, and cleanup fully succeeded.

---

## Check 1: LONG BODY (~1200 chars, 3 paragraphs, mixed case)

Target: message_id `119542` (Ollama newsletter). Sentinel: "The truncation
sentinel Zz9 ends the final paragraph." (body length 1107 chars).

`reply_to_email(mode="draft", output_format="json", timeout=240)`: **13.03s**.

```json
{
  "mode": "draft", "sent": false, "draft_id": "119544",
  "verification_status": "found", "body_present": true,
  "body_verified": "full_above_quote", "retyped": false,
  "stale_artifact_id": null
}
```

`verify_draft(draft_id="119544", expected_body_contains=<sentinel>)`: 0.54s.
`body_contains_expected: true`. The saved body preview showed the full
mixed-case text intact above the quote, ending exactly with the sentinel
sentence, no truncation, no case change.

**PASS.** `retyped` was `false` (no retype needed).

---

## Check 2: VERY LONG BODY (~5000 chars, scaled timeout)

Target: message_id `119532` (yesterday's IREI Newsline) was tried first and
returned `Error: No email found for message_id=119532` in 0.4s. The message
had aged out of the live inbox between discovery and use (a normal artifact
of testing against an actively-flowing real inbox, not a tool defect).
Re-discovered fresh ids and retargeted to `119578` (today's Institutional
Real Estate Newsline). Body length 5031 chars, same tail sentinel.

`reply_to_email(mode="draft", output_format="json", timeout=240)`:
**37.55s** (this is the wall-clock the check asked to record; comfortably
inside the 240s timeout and inside the branch's scaled-timeout math: 5031
chars / 80-char chunks ≈ 63 chunks × 1.0s ≈ 63s projected typing + 50s fixed
overhead/slack ≈ 113s natural need, well under the 240s ceiling passed).

```json
{
  "draft_id": "119595", "verification_status": "found",
  "body_verified": "full_above_quote", "retyped": false
}
```

**PASS** on the tool's own contract (JSON success, no retype, scaled timeout
held).

Finding (not a regression): a follow-up `verify_draft(expected_body_contains=
<tail sentinel>)` reported `body_contains_expected: false` with warning
`expected_body_missing`. This is `verify_draft`'s own `body_preview`, which
is hard-capped at 5000 characters in `verify_tools.py` (`if length of
draftBodyPreview > 5000 then set draftBodyPreview to text 1 thru 5000...`);
this cap is unrelated to AGENTIC-1214 and not touched by this branch. Because the request
body itself was 5031 characters, the tail sentinel fell past the cap in
`verify_draft`'s preview and could never match. Re-running `verify_draft`
with a needle from earlier in the body (still within the first 5000 chars)
returned `body_contains_expected: true` with no warnings, confirming the
draft content itself is fully intact; `reply_to_email`'s own internal
verifier (`saved_draft_checks.py`) has no such 5000-char cap and correctly
reported `"found"`/`"full_above_quote"`. Worth a note for anyone writing
`expected_body_contains` checks against replies longer than 5000 characters:
use `verify_draft` on the body prefix, not the tail.

---

## Check 3: SHORT BODY, twice ("Geoff, thanks for passing this along, good
read. Best, Cayman")

| Target | reply elapsed | draft_id | verification_status | retyped | body_contains_expected (exact case) |
|---|---|---|---|---|---|
| `119577` (Real Assets Adviser Newsline) | 5.83s | `119597` | found | false | true |
| `119554` (Mercury) | 5.64s | `119598` | found | false | true |

Both saved bodies previewed with the exact requested casing ("Geoff, thanks
for passing this along, good read. Best, Cayman"): no ALL CAPS, no case
drift. This is the exact bug class (Bug 3) the branch targets.

**PASS**, both instances, 2/2.

---

## Check 4: SUBSTITUTIONS BODY

Target: message_id `119582` (Linear notifications). Body:

> Quick note before the weekend: the "roadmap" for next quarter is still in
> flux--we are waiting on final budget approval. Renée mentioned she might
> have bandwidth to help review it... the market looks fine. Talk soon.

`reply_to_email(mode="draft", output_format="json", timeout=240)`: 55.31s.

Result: **`REPLY_BODY_MISMATCH`**, not a clean success:

```json
{
  "error": true, "code": "REPLY_BODY_MISMATCH",
  "message": "... saved Drafts artifact 119599 does not contain the full reply body above the quoted original ... typed body was truncated or miscased ...",
  "remediation": { "verification_status": "body_missing", "retyped": false, "draft_id": "119599" }
}
```

**Root cause, confirmed by inspecting the actual saved draft** (`verify_draft(
draft_id="119599")`, no `expected_body_contains`): the saved body reads
"**Renae** mentioned she might have bandwidth..." The accented character
"é" in "Renée" was corrupted to "ae" during native chunked-keystroke typing.
The curly-quote, em-dash, and ellipsis substitutions all rendered correctly
(`"roadmap"` → `“roadmap”`, `flux--we` → `flux—we`, `it...` → `it…`) and did
**not** cause the mismatch by themselves.

**Diagnostic (4b):** re-ran the identical body with the accent removed
("Renee" instead of "Renée"), same quotes/hyphen/ellipsis/lowercase-start
substitutions, on a fresh target (`119580`, Attio). This isolates the
variable cleanly.

`reply_to_email(...)`: 8.19s. Result: `verification_status: "found"`,
`retyped: false`, `draft_id: "119603"`, no mismatch.

**Conclusion:** the branch's smart-punctuation-folding claim holds: straight
quotes, double hyphens, ellipsis, and lowercase sentence starts do not
false-trigger `REPLY_BODY_MISMATCH`. But the literal check-4 scenario as
specified (which includes an accented name) **fails**, because the native
keystroke-typing path (System Events `keystroke`, not this branch's
verification layer) corrupts at least this one non-ASCII character before it
ever reaches the verifier. The verifier did its job correctly here: it
caught the corruption and refused to report false success, and no email was
sent. This looks like a pre-existing typing-fidelity gap, not a regression
introduced by AGENTIC-1214, but it is real and reproducible and should be
filed as a follow-up (accented/composed Unicode characters in reply bodies
are not safe with the native path today).

**Secondary finding:** the automatic delete-and-retype safety net added by
this branch (`retyped`) did not engage for the `119599` failure
(`"retyped": false`) even though a concrete artifact id was present and the
failure was `body_missing`, the documented trigger condition. Static review
of `reply_scripts.py` (native window template, lines ~182–192) shows a
plausible mechanism: `replyDraftId` is read via `id of replyMessage`
immediately after `save`, wrapped in a bare `try ... end try` that silently
leaves it as `""` on any error; when that happens no `"Draft ID:"` line is
emitted at all, `reply.py`'s `draft_id` extraction returns `None`, and the
`can_retry` guard's `bool(draft_id)` check fails, skipping the retry even
though the verifier's subject-scan fallback can still resolve the artifact.
This is architecturally consistent with what was observed but was not
confirmed with instrumentation in this session; flagging as a recommended
code-level follow-up rather than a proven root cause.

**Check 4 result: FAIL** as literally specified (accented name triggers a
real mismatch), **PASS** on the narrower punctuation-normalization claim it
was meant to isolate (confirmed via the 4b diagnostic).

---

## Check 5: FOCUS-STEAL MID-TYPING (run once, not retried)

Target: message_id `119584` (Google Calendar, "You have no events scheduled
today."). Body: 2500 chars. A background shell process slept ~6s then ran
`osascript -e 'tell application "Finder" to activate'`, started immediately
before the `reply_to_email` call.

`reply_to_email(mode="draft", output_format="json", timeout=240)`: **68.68s**
(long relative to the other checks; consistent with the 20-attempt,
1s-interval subject-scan fallback the verifier runs when the exact-draft-id
lookup path is not taken).

Result: **`REPLY_BODY_MISMATCH`**, not the anticipated
`REPLY_BODY_TYPING_INTERRUPTED` and not a clean success:

```json
{
  "error": true, "code": "REPLY_BODY_MISMATCH",
  "remediation": { "verification_status": "body_missing", "retyped": false, "draft_id": "119606" }
}
```

Inspected the actual saved draft (`verify_draft(draft_id="119606")`): the
body above the quote boundary (` wrote:`) was **2572** characters, longer
than the 2500-character source, and cut off mid-word: "...still be mid-flight
w" (truncated before completing "with"). This is neither a clean truncation
(shorter than source) nor a clean success; it reads as a corrupted/partially
duplicated typing pass consistent with the focus steal landing mid-chunk
without tripping the explicit `TYPING_INTERRUPTED` sentinel.

Checked for stray windows via a read-only System Events window enumeration
(not a Mail AppleScript call, so it does not violate the one-call-at-a-time
rule): only `"Drafts — Cayman - Agentic Assets – 986 drafts"` was present,
no open compose window with the reply subject. So the compose window was
closed either way.

**Assessment:** the critical safety invariant held: no email was sent, and
the tool did not report false success on a corrupted body. But the specific
contract this check targets (either a clean `REPLY_BODY_TYPING_INTERRUPTED`
with the partial window auto-discarded, or a clean verified success) was not
what happened; a corrupted draft was left in Drafts requiring the caller to
notice `REPLY_BODY_MISMATCH` and manually delete it, rather than an automatic
discard. Per instructions this check was run once and not retried.

**Check 5 result: PARTIAL.** Safety-critical outcome (no send, no false
success) holds. The narrower contract (clean interrupted-abort or clean
success) was not observed; recommend engineering follow-up on the boundary
between the mid-chunk focus-guard check and the final save/verify step, plus
whether the retype-once path should also trigger on this failure mode.

---

## Check 6: MANAGE_DRAFTS CONTRACT (`CREATE_CANNOT_THREAD`)

`manage_drafts(action="create", subject="Standalone check 3.11.1",
to="cayman@agenticassets.ai", body="Contract check.",
in_reply_to="<test-message-id@example.com>")`: 0.14s.

```json
{
  "error": true, "code": "CREATE_CANNOT_THREAD",
  "message": "manage_drafts(action=\"create\") builds a standalone new message and cannot set In-Reply-To/References headers ... No draft was created."
}
```

`manage_drafts(action="list", subject_contains="Standalone check 3.11.1")`:
2.89s. `Found 0 draft(s)`.

**PASS.** Structured error returned, no draft created.

---

## Check 7: VERIFY_DRAFT QUOTE SCOPING

Reused Check 1's draft (`119544`, reply on the Ollama message). Before
writing Check 1's reply, the target message's raw content was fetched via
`get_email_by_id` and a phrase unique to the original message was picked:
"Kitematic, which made Docker dead-simple to run" (confirmed present only in
the quoted section, never in the new reply text).

`verify_draft(draft_id="119544", expected_body_contains="Kitematic, which
made Docker dead-simple to run")`: 0.62s.

```json
{ "body_contains_expected": false, "body_needle_only_in_quote": true }
```

**PASS.** Exactly the documented contract: a needle that exists only in the
quoted original is correctly reported as not-present-above-quote, with the
`body_needle_only_in_quote` flag set.

---

## Check 8: CLEANUP

Re-ran `manage_drafts(action="list", subject_contains=<fragment>)`
immediately before each delete and matched by subject, per the Exchange
id-drift warning in the tool's own docstring.

| Draft | Subject fragment | Resolved id | Delete result |
|---|---|---|---|
| Check 1 | "All Aboard Open Models - Ollama Raises" | 119544 | deleted |
| Check 2 | "Institutional Real Estate Newsline Jul 10, 2026" | 119595 | deleted |
| Check 3a | "Real Assets Adviser Newsline Jul 10, 2026" | 119597 | deleted |
| Check 3b | "Your IO credit limit increased" | 119598 | deleted |
| Check 4 | "2 unread notifications on Agentic Assets" | 119599 | deleted |
| Check 4b | "39 tasks overdue and 20 tasks due today" | 119603 | deleted |
| Check 5 | "You have no events scheduled today" | 119606 | deleted |
| Check 6 | "Standalone check 3.11.1" | (none created) | n/a |

Every id resolved matched what each check had already reported, no drift
observed. Followed with a fresh `action="list"` per subject fragment: **zero
drafts remaining** for all eight subjects.

Final System Events window check on the Mail process: only the Drafts list
window (`"Drafts — Cayman - Agentic Assets – 979 drafts"`, down from 986 at
session start by exactly 7, matching the 7 deletes). No stray compose
windows left open.

**PASS.** All test artifacts removed, no email sent, no other user drafts or
messages touched.

---

## Summary

| Check | Result |
|---|---|
| 1. Long body (~1200 chars) | PASS |
| 2. Very long body (~5000 chars) | PASS (verify_draft's own 5000-char preview cap is a separate, pre-existing limitation, not a regression) |
| 3. Short body, twice | PASS, 2/2 |
| 4. Substitutions body | FAIL as literally specified: accented character "é" corrupted to "ae" during native typing (pre-existing typing-fidelity gap, not a punctuation-normalization false-positive); punctuation-normalization claim itself PASSED via the 4b diagnostic |
| 5. Focus-steal mid-typing | PARTIAL: no send / no false success (safety-critical property held), but neither the clean interrupted-abort nor the clean success path was observed; a corrupted draft was left in Drafts instead of being auto-discarded |
| 6. manage_drafts CREATE_CANNOT_THREAD | PASS |
| 7. verify_draft quote scoping | PASS |
| 8. Cleanup | PASS: all 7 created drafts deleted and confirmed gone, no stray compose windows |

**Blocking findings for the push:**

1. **Accented/composed Unicode characters are not safe in native reply
   bodies today.** "Renée" typed as a reply body was saved to Drafts as
   "Renae." The branch's own verifier correctly caught this as
   `REPLY_BODY_MISMATCH` (no silent corruption reached a sent email), but the
   underlying typing defect is real and will surface as spurious mismatches
   for any reply containing accented names or diacritics. Recommend a
   follow-up before this ships to users who reply in languages/names with
   diacritics.
2. **The automatic retype-once safety net did not trigger** for either
   Check 4's or Check 5's `REPLY_BODY_MISMATCH`, both of which had a concrete
   artifact id and a `body_missing` status, the documented trigger
   condition. Whether this is the same root cause in both cases (a
   `draft_id` extraction gap right after save) was not confirmed with
   instrumentation in this session, but it is reproducible enough (2/2
   mismatch cases in this run) to warrant a targeted look before merge,
   since the retype path is one of this branch's headline claims.
3. Check 5's exact contract (clean `REPLY_BODY_TYPING_INTERRUPTED` with
   auto-discard, or clean success) was not exercised; the actual behavior
   under a focus steal was a corrupted-but-caught draft, a third outcome not
   covered by the plan. Not necessarily a blocker (no send occurred, no
   false success occurred), but worth another timing-window attempt with
   instrumentation before treating the interrupted-abort path as verified
   end-to-end.

Nothing else blocks: checks 1, 2, 3, 6, 7, and cleanup all match the branch's
documented contracts exactly, with no ALL CAPS, no truncation, and no false
`REPLY_BODY_MISMATCH` from smart-punctuation substitutions.
