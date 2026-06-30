# Native reply formatting investigation and log

**Date:** 2026-06-30
**Tool:** `reply_to_email` (and by extension `create_rich_email_draft`)
**Status:** Diagnosis settled. Best fix still has one open empirical question (see Open questions).
**Authors:** Investigation run across two agents (this Claude Code session plus a parallel Codex session), both probing the same live "TU - Cayman" Exchange account.

---

## Problem statement

`reply_to_email` saves reply drafts that do not look like the draft Apple Mail builds when the user clicks Reply manually. The user wants the native result: the TU signature (with logo) placed above the quoted original, and the original wrapped as a rich blockquote with the colored quote bar. The user's explicit preference is to use Mail's native output rather than hand-rebuilding a quote, because the native version renders better and the logo signature is hard to reproduce by hand.

Two reference points from the user's own Mail:
- Screenshot 1 (current MCP output): flat, no colored bar, signature/logo displaced.
- Screenshot 2 (manual native reply): colored quote bar, signature with logo above the quote.

---

## Root cause (high confidence)

In `plugin/apple_mail_mcp/tools/compose.py`, `reply_to_email` calls Mail's native `reply` command and then overwrites the result:

```
# compose.py ~1588-1606
set sourceContent to content of foundMessage as string          -- reads original as PLAIN text
set replyMessage to reply foundMessage {reply_options}            -- Mail builds the NATIVE reply
...
set quotedOriginalText to quotedOriginalNeedle & return & sourceContent
set composedReplyContent to replyBodyText & return & return & quotedOriginalText
set content of replyMessage to (composedReplyContent as rich text) -- OVERWRITES the native reply
```

The single line `set content of replyMessage to (... as rich text)` is the defect. Mail's `content` over Apple Events is plain text only. Any write to it flattens the message: it drops the colored quote bar (which is HTML block structure, `<blockquote type="cite">`, not character styling), displaces the signature, and loses the logo image. The code then substitutes a hand-built quote assembled from `content of foundMessage as string`, which was already flattened to plain text on read.

So the native draft the user wants is in fact being built by `reply` and then discarded one step later.

---

## The hard constraint (confirmed by external sources and by live probe)

AppleScript's `content` property models text as character runs (font, size, color), not as HTML block structure. The colored quote bar requires `<blockquote type="cite">`. Therefore:

- Setting `content` to a string deletes the native quote and signature (confirmed on MacScripter and consistent with the live probes below).
- Concatenating (`set content to body & content`) still flattens, because the operation produces plain text.
- In-place element edits (`make new paragraph at beginning of content`, `set paragraph 1 of content to ...`) silently no-op against Mail's live document, especially once a compose window is open and Mail's WebKit editor owns the content.

Dictionary facts verified locally (`/System/Applications/Mail.app/Contents/Resources/Mail.sdef`):
- `reply <message>` returns an `outgoing message`; `opening window` and `reply to all` are optional booleans, both default false.
- `outgoing message.content` is writable rich text (line 282), but writing it flattens block structure as above.
- Saved `message.content` is read-only rich text (line 574).
- Preferences exist for `color quoted text` and `selected signature`.

---

## History (why an earlier native fix was reverted)

The reply body insertion mechanism went through several designs (from git history):

- `6bfe72b` (v3.6.0, 2026-06-05): removed the original GUI/clipboard compose path; rebuilt replies as plain-text `>`-quoted drafts. Lost native `In-Reply-To`/`References` threading.
- `80b0d82` (2026-06-08): switched to Mail's native `reply` command and inserted the body via clipboard plus `keystroke "v"`. This preserved the colored bar and signature.
- `67db4d2` (2026-06-08): added post-save bounded Drafts verification.
- `5590efd` (2026-06-19): pivotal revert. Removed the native `reply` plus clipboard/keystroke insertion and replaced it with today's `set content` reconstruction. Reason: the GUI paste was focus sensitive and silently dropped the body, saving drafts that contained only the signature and quoted original. See `tasks/reply-body-insertion-failure-2026-06-18.md` ("unsafe false progress").
- Repo doctrine after the revert (`tasks/reply-draft-verification-hardening-2026-06-19.md`): avoid reply-body insertion through UI scripting, clipboard paste, or focus-sensitive windows. Enforced by tests with `assertNotIn("System Events")`, `assertNotIn('keystroke "v"')`, `assertNotIn("NSPasteboard")`.

Key takeaway: the GUI-paste approach is not a new idea. It shipped and was deliberately removed because it failed silently. Any return to it has to address that failure mode, not just the rendering.

---

## Live probes run on 2026-06-30

### Probe 1 (this session, MCP plus one osascript diagnostic)

Account "TU - Cayman", target message id 86659 ("One more thing"). Created two throwaway reply drafts, then deleted them.

Findings:
- `colorQuotedText = true` (the colored bar is enabled in Mail settings).
- `selectedSignature = None` (Mail's scriptable global signature is None, so a reply does not auto-insert the TU signature by name; the logo signature is a per-account assignment applied in the compose window).
- Native `reply` in draft mode (no window) returns an outgoing message whose `content` length is 0. The native quote and signature only materialize once a compose window renders them. This is why the current code had to build its own quote in headless draft mode.
- The outgoing message id returned by `reply` (small integers like 20, 21) does not equal the saved Drafts id (86667, 86668). Cleanup by the outgoing id failed; the two drafts were then located and deleted by their real Drafts ids via the MCP.

### Probe 2 (parallel Codex session)

Account "TU - Cayman", junk target ("Welcome to Dr.J's Substack").

Findings reported:
- Approach A (native `reply with opening window` plus GUI paste, no `set content`) produced a real editable draft that rendered natively: text/html part present, `<blockquote type="cite">` present, TU logo `<img>` present, body above quote. Verified on draft 86666 / 86672.
- Approach B (build a threaded `.eml` with native-style blockquote plus the real signature logo as a `cid:` related part, then `open -a Mail file.eml` plus save) appeared to open read-only viewer windows rather than editable compose windows. The agent also observed `create_rich_email_draft` reporting "Saved in Drafts: yes" while the uniquely-subjected draft did not appear.
- Signatures live in `~/Library/Mail/V*/MailData/Signatures/*.mailsignature` as `multipart/related` MIME with the logo embedded as a `cid:` image.

---

## Reframing the "B is broken" finding (important)

The Codex conclusion that the `.eml` route and `create_rich_email_draft` are broken on this macOS is very likely conflated with a separate, real bug found by reading the source, independent of any OS behavior:

`_save_front_compose_window_as_draft` (compose.py ~983-1026) does, at line ~1005:

```
set targetMessage to item 1 of outgoing messages
save targetMessage
```

It saves whichever compose window is `item 1`, not the one just opened from the `.eml`. The user currently has several compose windows open (NSF CAREER, EQUIRE, RJER, plus the A test drafts). So a save call can re-save a different lingering window and still return "Saved: yes". The agent watched an id roll from 86661 to 86672 during exactly this, which fits the wrong-window save.

So two issues are tangled:
1. Confirmed bug: blind `item 1 of outgoing messages` save targets the wrong window when more than one compose window is open, and reports false success. This breaks B and standalone rich drafts in a multi-window environment regardless of OS.
2. Unproven: whether `X-Unsent: 1` `.eml` opens editable on Darwin 25.5.0. The read-only observations were made through issue 1 and a cluttered window state, so B is not proven dead on this OS yet.

---

## Evidence-based comparison of the three approaches

| | A: native reply + GUI insert | B: `.eml` import | C: current `set content` |
|---|---|---|---|
| Native render (bar + logo) | Yes (verified) | Yes if blockquote + cid logo replicated | No (flattens) |
| Robustness | Fragile: focus, timing, clipboard, Accessibility | High when the save targets the right window | High |
| Permissions | Automation + Accessibility | Automation only | Automation only |
| Headless / bulk safe | No (attended only) | Yes | Yes |
| Works on current Mail (Darwin 25.5.0) | Yes (verified) | Open question (see issue 2 above) | Yes |
| Threading (In-Reply-To/References) | Native | Must set headers by hand | Lost in some paths |
| Status in repo | Shipped then reverted (`5590efd`) | Matches `create_rich_email_draft` design | Current code (the bug) |

---

## Can A be made non-focus-sensitive?

Partly, not fully.

Hardening that is worth doing:
- Target the exact reply window by reference (`window of replyMessage`, then `AXRaise` and verify `AXMain` is true) instead of pasting into the front window. The reverted version typed into whatever had focus. This is the single biggest improvement.
- Drop the clipboard. Use `keystroke` of the literal body instead of `set the clipboard` plus Cmd-V, so the user's clipboard is not clobbered and there is no clipboard race.
- Make post-save verification mandatory: confirm the body sits above the quote in the saved draft by exact id, and if not, delete that draft and retry once, else return a structured error. This is the missing safety net that caused the original revert (it failed silently).

The ceiling: `keystroke` goes to the OS keyboard focus. If a window steals focus in the moment it fires, it lands in the wrong place. The risk window can be shrunk and the damage caught afterward, but GUI keystroke cannot be made focus immune. True focus independence requires not using the GUI, which means B or C. A, even hardened, is an attended/interactive tool and should never be the headless or bulk path.

---

## Open questions

1. Does `X-Unsent: 1` `.eml` open as an editable compose (not a read-only viewer) on Darwin 25.5.0, once the save targets the correct window? This is the deciding question for B. Needs a clean test: one window, detect the result by unique subject via the MCP Drafts lookup, exact-id confirm, self-cleanup.
2. After fixing the blind save, does `create_rich_email_draft` reliably land the message it built (no false "Saved: yes")? Suspected yes.
3. Where exactly is the TU per-account signature assigned, and which `.mailsignature` file is it, so B can embed the right logo deterministically? (`selectedSignature = None` globally, so it is per-account.)

---

## Recommended path forward

1. Fix the blind-save bug first (compose.py ~983-1026). Save by exact reference to the message opened from the `.eml` (or match by a unique marker), and verify the saved Drafts id has the expected subject and body before returning success. This is a real false-success bug worth its own issue regardless of which approach wins.
2. Re-test B cleanly (exact-subject detection, one window). If `.eml` opens editable once the save targets the right window, B is the durable answer: native render, no GUI, no Accessibility, no clipboard, no focus dependency, aligned with the existing `create_rich_email_draft` design. The blockquote and cid-logo work already prototyped carries over.
3. Only if B truly fails on current macOS, ship hardened A (exact-window `AXRaise` + verify `AXMain` + clipboard-free keystroke + mandatory verify-or-delete-and-retry) as an attended reply mode, keep today's `set content` path as the headless default, and file the `.eml` regression against Apple's newest Mail.

Net: A can be made safe-failing but not focus immune, so it is the interactive fallback. B is probably not actually broken here; it was measured through a fixable save bug in a cluttered window state. If B holds up after the save fix, it is the better durable fix for a shipped plugin that runs on multiple hosts, some headless.

---

## Coordination and hygiene notes

- Two agents plus the user are all writing to the same live "TU - Cayman" Drafts. This caused at least one inventory mismatch (Probe 1 created two "One more thing" drafts that the parallel agent's inventory missed; both have since been deleted).
- GUI paste steals focus. Do not run two GUI-driving agents on live Mail at once, and do not run a GUI paste while another agent has raised a different window. Prefer one agent driving live Mail at a time.
- Test artifacts to track and clean when done: parallel-session prototype draft 86672 ("Re: Welcome to Dr.J's Substack"), plus any read-only viewer windows it opened ("ZZ Rich Draft Probe 9931", min_test, reply_B). The real Knill reply draft (86662) and the user's open compose windows (NSF CAREER, EQUIRE, RJER) must not be touched.

---

## References

- `plugin/apple_mail_mcp/tools/compose.py` (`reply_to_email`, `_build_native_reply_applescript`, `_save_front_compose_window_as_draft`, `_verify_saved_reply_draft`)
- `tasks/reply-body-insertion-failure-2026-06-18.md` (the original silent body-drop report)
- `tasks/reply-draft-verification-hardening-2026-06-19.md` (the no-UI-scripting doctrine)
- `tasks/mail-scripting-dictionary-audit-2026-06-19.md` (dictionary constraints, `html content` deprecated)
- Git commits: `6bfe72b`, `80b0d82`, `67db4d2`, `5590efd`
- `/System/Applications/Mail.app/Contents/Resources/Mail.sdef`
- MacScripter: "Reply to a Mail message: insert text above signature and quoted text" (https://www.macscripter.net/t/reply-to-a-mail-message-insert-text-above-signature-and-quoted-text/72391)

---

## RESOLUTION — validated 2026-06-30 (live, on real mail)

Verdict: **Approach A (native `reply` + window + typed keystroke) wins. Approach B (.eml import) is dead on this macOS.** Proven end-to-end on a real self-sent test email (source message 86695, "Test", `TU - Cayman`).

### The decisive new discovery (bigger than the blind-save bug)

On this macOS (Darwin 25.5), **`count of outgoing messages` returns 0 even while compose windows are open** — but only for windows Mail did **not** create through the dictionary:

- A window opened by `open -a Mail file.eml` (the `create_rich_email_draft` path) is **NOT** in `outgoing messages`. It is visible (`count of windows` shows it by name) but unreachable as an outgoing message, so it cannot be `save`d. This is why `create_rich_email_draft` honestly reported "Saved in Drafts: no" after the blind-save fix, and why the earlier "B is broken" reading was correct in conclusion (wrong in mechanism).
- A window created by the dictionary `reply` command **IS** counted (`outgoing_count=1`) and `save replyMessage` works. The reply path was never affected by the empty-collection issue because it holds the object reference directly.
- `window of <outgoing message>` is **not** accessible ("Can't get window of outgoing message id N"). Target the reply window via Mail's `front window` name instead.

### What works (validated)

1. `reply foundMessage with opening window` builds the exact native draft: empty body area on top, the account's signature **with the real logo** (`<img src="cid:…">`), then the quoted original wrapped in `<blockquote type="cite">` (the colored bar). Confirmed visually by the user and in saved source.
2. Insert the agent body with a **typed** System Events keystroke (NOT clipboard paste), guarded:
   - `activate` Mail; confirm Mail dict `name of front window` is the reply subject.
   - System Events: `set frontmost to true`; `perform action "AXRaise" of (first window whose name is "Re: …")`; re-read `name of front window` and only `keystroke` when it equals the reply subject. Otherwise abort with no keystroke (no wrong-window corruption).
3. `save replyMessage` (dictionary reference) persists it; the saved Drafts source has body → signature(logo) → blockquote in correct order (lines 38 / 52 / 68 of the captured source).
4. **Closing the window after save does not corrupt the saved draft** (re-read was byte-identical, 36994 bytes). So draft mode can open → type → save → close.
5. The existing `_verify_saved_reply_draft` body-above-quote check is the safety net the reverted `80b0d82` lacked.

### What does NOT work — never try again

- **`set content of replyMessage`** (current code, compose.py ~1604): flattens the native draft (drops the colored bar, the logo, displaces the signature). The native quote/signature live only in the rendered window, never in dictionary `content`, so any `content` read/write loses them.
- **Clipboard paste insertion** (`set the clipboard` + `keystroke "v" using command down`, the reverted `80b0d82`): clobbers the user clipboard and was the wrong-thread corruption vector. Use typed `keystroke` instead.
- **`close window 1` / positional window targeting**: closed the wrong window historically. Close by window name or by the saved reference.
- **`.eml` import for replies** (`create_rich_email_draft`-style): the opened window is not a saveable `outgoing message` here, and it would force hand-rebuilding the signature MIME the user explicitly does not want to reinvent.
- **Guarding the keystroke via System Events `name of front window` alone**: it can read empty for compose windows (AX title quirk). Cross-check Mail's dict front-window name and use AXRaise.

### Reusable probe scripts

Saved to [`tasks/native-reply-probes-2026-06-30.md`](native-reply-probes-2026-06-30.md): open-native-reply, AXRaise targeting test, guarded keystroke+save, saved-source verifier, and window/outgoing diagnostics.

### CONFIRMED: `reply with opening window` leaves empty shell drafts

Live check 2026-06-30: after the test runs, TU Drafts held **5** "Re: Test"
drafts — one filled (id 86704, contentLen 567, with the typed body) and **four
empty shells** (contentLen 502, signature+quote only). Each `reply … with
opening window` auto-saves a shell on open that **persists even after closing the
window with `saving no`**. This is the v3.5.0 duplicate-shell behavior, still live
on this macOS. (All five were test artifacts and were deleted by exact id.)

Implementation MUST dedupe: after the explicit `save`, delete the empty
"Re: <subject>" shell(s) and keep only the filled draft. Distinguish by content
length / presence of the body needle, and act by exact id. Do not delete by
subject blindly (would hit a user's real same-subject draft).

### Chosen implementation (pending final design confirm)

Rewrite `_build_native_reply_applescript`: drop `set content`; always `reply … with opening window`; when `reply_body` is non-empty, insert via the AXRaise-guarded typed keystroke; `save` via reference; **dedupe the auto-saved empty shell**; verify body-above-quote; in draft mode close the window after save, in open mode leave it. Default `native_format=True` with a `native_format=False` quiet/no-window fallback for headless/bulk callers. Focus-dependent by nature (acceptable per user's Mac mini usage); guard fails safe (abort, no corruption) and the verifier fails loud.

### Separately fixed this session (the blind-save bug)

`_save_front_compose_window_as_draft` → renamed `_save_new_compose_window_as_draft`: was `item 1 of outgoing messages` (saved the wrong window, false "Saved: yes"); now snapshots outgoing-message ids before the open and saves only the new one (id diff). Unit tests updated and green (959 passed). Note: on this macOS the diff cannot help `create_rich_email_draft` because file-opened windows never enter `outgoing messages` at all — that path needs a separate rethink or deprecation for replies.
