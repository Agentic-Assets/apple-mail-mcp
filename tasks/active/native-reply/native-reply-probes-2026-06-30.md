# Native-reply probes and reusable test commands (2026-06-30)

Working test code captured while validating the native-reply approach on real
mail. Companion to [`native-reply-formatting-investigation-2026-06-30.md`](native-reply-formatting-investigation-2026-06-30.md).

**Before reusing:** update the two literals — account name (`TU - Cayman`) and
source message id (`86695`). Find a fresh id with the inbox listing below.
Everything here is draft-only and sends nothing.

## Environment facts learned this session (Darwin 25.5 / macOS Tahoe)

- `count of outgoing messages` is **0 even with compose windows open**, EXCEPT
  for windows created via the dictionary (`reply`, `make new outgoing message`).
  A window from `open -a Mail file.eml` is visible but **not** an outgoing
  message and **cannot be saved**.
- `window of <outgoing message>` raises "Can't get window of outgoing message
  id N". Target via Mail's `front window` name instead.
- System Events `name of front window` can be empty for a compose window (AX
  title quirk); cross-check Mail's dictionary `name of front window`.
- Mail's dictionary `name of front window` of an opened reply is the subject,
  e.g. `Re: Test`.
- Closing the compose window after `save` does NOT corrupt the saved draft.

## 0. Find a fresh source message id (read-only)

```bash
.venv/bin/apple-mail inbox --account "TU - Cayman" --limit 6 --json \
  | .venv/bin/python -c "import sys,json; d=json.load(sys.stdin); \
[print(e.get('message_id'),'|',e.get('sender'),'|',e.get('subject')) for e in d.get('emails',[])]"
```

## 1. Window / outgoing diagnostics (read-only)

```applescript
tell application "Mail"
    set report to ""
    set report to report & "outgoing_count=" & (count of outgoing messages) & linefeed
    set report to report & "window_count=" & (count of windows) & linefeed
    repeat with w in windows
        set report to report & "  win: " & (name of w) & linefeed
    end repeat
end tell
return report
```

System Events view (shows AX titles, which can be empty):

```bash
osascript -e 'tell application "System Events" to tell process "Mail"
  set out to "se_front_window=" & (name of front window) & linefeed
  repeat with w in windows
     set out to out & "  se_win=[" & (name of w) & "]" & linefeed
  end repeat
  return out
end tell'
```

## 2. Open a PURE native reply for visual inspection (no content set, no save)

This is "what Apple Mail natively builds" — the user-approved format.

```applescript
tell application "Mail"
    set acct to account "TU - Cayman"
    set mbx to missing value
    try
        set mbx to mailbox "INBOX" of acct
    end try
    if mbx is missing value then
        try
            set mbx to mailbox "Inbox" of acct
        end try
    end if
    set theMsg to missing value
    repeat with m in (messages 1 thru 20 of mbx)
        try
            if (id of m) is 86695 then
                set theMsg to m
                exit repeat
            end if
        end try
    end repeat
    if theMsg is missing value then return "ERROR: source not found in newest 20"
    set r to reply theMsg with opening window
    delay 0.9
    activate
    return "opened native reply; inspect quote bar + signature + empty top"
end tell
```

## 3. AXRaise targeting test (no keystroke, no save)

Confirms you can deterministically make the reply window the key window before
typing. Run this if keystroke targeting ever seems flaky.

```applescript
-- (resolve theMsg as in #2, then:)
set r to reply theMsg with opening window
delay 1.2
activate
delay 0.4
tell application "System Events"
    tell process "Mail"
        set frontmost to true
        delay 0.3
        try
            perform action "AXRaise" of (first window whose name is "Re: Test")
        end try
        delay 0.4
        return "se_front_after_raise=[" & (name of front window) & "]"
    end tell
end tell
-- Expect: se_front_after_raise=[Re: Test]
```

## 4. THE VALIDATED PATTERN — guarded typed keystroke + dictionary save

Native reply + body on top + save, with a guard that aborts (no keystroke)
unless both Mail and System Events agree the reply window is front. Typed
keystroke, never clipboard paste.

```applescript
tell application "Mail"
    try
        close (every window whose name is "Re: Test") saving no
    end try
    delay 0.4
    set acct to account "TU - Cayman"
    set mbx to missing value
    try
        set mbx to mailbox "INBOX" of acct
    end try
    if mbx is missing value then
        try
            set mbx to mailbox "Inbox" of acct
        end try
    end if
    set theMsg to missing value
    repeat with m in (messages 1 thru 20 of mbx)
        try
            if (id of m) is 86695 then
                set theMsg to m
                exit repeat
            end if
        end try
    end repeat
    if theMsg is missing value then return "ERROR: source not found"
    set r to reply theMsg with opening window
    delay 1.2
    activate
    delay 0.4
    set mailFront to name of front window
end tell

set seFront to "(none)"
set didType to false
tell application "System Events"
    tell process "Mail"
        set frontmost to true
        delay 0.3
        try
            perform action "AXRaise" of (first window whose name is "Re: Test")
        end try
        delay 0.4
        try
            set seFront to name of front window
        end try
        if (seFront is "Re: Test") then
            keystroke "Your reply body here." & return & return & "Second paragraph."
            set didType to true
        end if
    end tell
end tell

delay 0.4
tell application "Mail"
    if didType then
        save r
        delay 1.5
        -- draft mode also: close (every window whose name is "Re: Test") saving no
        return "TYPED+SAVED | mail_front=" & mailFront & " | se_front=" & seFront
    else
        return "ABORTED (no keystroke) | se_front=" & seFront
    end if
end tell
```

## 5. Verify the saved draft (read-only) + structural checks

```applescript
on writeText(t, p)
    set fh to open for access (POSIX file p) with write permission
    set eof fh to 0
    write t to fh as «class utf8»
    close access fh
end writeText
tell application "Mail"
    set acct to account "TU - Cayman"
    set dm to mailbox "Drafts" of acct
    set found to missing value
    repeat with m in (messages 1 thru 30 of dm)
        try
            if (subject of m) is "Re: Test" then
                set found to m
                exit repeat
            end if
        end try
    end repeat
    if found is missing value then return "no draft found"
    set theId to (id of found) as string
    set src to (source of found) as string
    my writeText(src, "/tmp/saved_reply_source.eml")
    return "draft_id=" & theId & " | source_len=" & (length of src)
end tell
```

```bash
SRC=/tmp/saved_reply_source.eml
grep -c 'YOUR BODY SENTINEL' "$SRC"          # body present
grep -ic 'blockquote' "$SRC"                  # colored quote bar survived
grep -ic 'multipart/related' "$SRC"           # signature MIME present
grep -ic 'Content-Type: image/' "$SRC"        # logo image present
# ordering: body line < signature cid line < blockquote line
grep -n 'YOUR BODY SENTINEL' "$SRC"; grep -n 'cid:' "$SRC" | head -1; grep -n 'blockquote' "$SRC" | head -1
```

## 6. Cleanup by EXACT id (never by subject for deletes elsewhere)

```applescript
tell application "Mail"
    set acct to account "TU - Cayman"
    set dm to mailbox "Drafts" of acct
    try
        delete (first message of dm whose id is 86704)
    end try
    try
        close (every window whose name is "Re: Test") saving no
    end try
    return "cleaned"
end tell
```

## NEVER do these (cost real incidents / wasted cycles)

- `set content of replyMessage to (... as rich text)` — flattens the native draft.
- `set the clipboard` + `keystroke "v" using command down` — clobbers clipboard, wrong-thread corruption.
- `close window 1` — positional close hit the wrong window.
- Saving a `.eml`-opened window as a draft on this macOS — it is not an outgoing message.
- Trusting System Events `name of front window` alone — can be empty for compose windows.
- `delete m` on a Drafts loop variable (Exchange) — silently no-ops; use the whose-id form below.

## VALIDATION RUN 2 — clean-path (2026-06-30, source id 86695, account "TU - Cayman")

Second validation run on the real "Test" email, focused on the open questions the
first run left. Results corrected my earlier notes:

1. **Dedupe is NOT needed in the clean path.** A single `reply with opening window`
   → guarded keystroke → `save r` → `close ... saving no` leaves **exactly one**
   "Re: Test" draft (verified `reTestDrafts=1`). `save r` reuses the auto-created
   shell; it does not add a second copy. The earlier "4 shells + 1 filled" was an
   artifact of running the *inspection* probes (open-without-save) several times;
   each inspect-open left a shell. An **aborted** run (guard fails → no keystroke →
   `close ... saving no`) also leaves **zero** shells. So: no per-call dedupe pass;
   just close the window `saving no` on both success and abort.

2. **`id of replyMessage` is the wrong id, and on Exchange the saved id is unstable.**
   `id of r` returned the small session-scoped *outgoing* id (`32`), not the saved
   Drafts id. The enumerated Drafts id right after save was `86714`, but after
   Exchange sync (~seconds) it was reassigned to `86715`. So **do not rely on an
   exact draft id** captured at save time on the window path — the verifier's bounded
   newest-Drafts fallback (subject + first-body-line needle, newest-first) is the
   dependable check. The native script should still emit subject + quote-needle, but
   `exact_id_verified` will typically be false on Exchange (expected, not a bug).

3. **Front-window guard must tolerate an empty System Events title.** First run aborted
   because the guard required `se_front = "Re: Test"`, but SE returned **empty** (the
   AX-title quirk) while Mail's dictionary `name of front window` correctly returned
   `Re: Test`. Working guard: **Mail dict front-window name is authoritative**
   (`must equal replySubject`); SE front-window name must be **empty OR equal**
   replySubject (a *different* non-empty SE title aborts). With that relaxation the
   keystroke fired and saved correctly.

4. **`keystroke` of the body `cat`'d from the temp file works** — multi-line preserved
   (both `PROBE_SENTINEL_ALPHA`/`BETA` paragraphs landed, blank line between). No need
   for literal `& return &` concatenation; read the file into a var and `keystroke` it.

5. **Native quote attribution is `On <Mon DD, YYYY>, at <H:MM AM>, <Name> <addr> wrote:`** —
   it does NOT match the `date received as string` / full-sender needle the old
   `set content` path constructed. For the native path, set the verifier quote-needle to
   the universal **`wrote:`** suffix so body-above-quote ordering is genuinely checked.

6. **Source proof of the native format** (saved draft, `source` MIME): body sentinels
   present and multi-line; **2 `blockquote`** (colored quote bar survived); **1
   `multipart/related` + 1 `Content-Type: image/` + 2 `cid:`** (logo signature intact);
   ordering body(38) < signature-cid(53) < blockquote(69) < `wrote:`(71). Note the
   existing verifier checks body-above-quote + signature but **not** blockquote survival,
   so keep the keystroke as the **last** content mutation so nothing re-flattens it.

### Reliable cleanup-by-exact-id on Exchange (use this, not `delete m`)

```applescript
tell application "Mail"
    set dm to mailbox "Drafts" of account "TU - Cayman"
    set msgs to (every message of dm whose id is 86715)
    if (count of msgs) > 0 then delete (item 1 of msgs)
    delay 3
    return (count of (every message of dm whose id is 86715)) -- expect 0
end tell
```

(Aside: `apple-mail drafts list --account "TU - Cayman"` reported `count=0` while
AppleScript saw 208 drafts — a `manage_drafts` scoping quirk, unrelated to replies;
logged for a separate look.)

## VALIDATION RUN 3 — full tool integration + the logo regression (2026-06-30)

Tested through `reply_to_email` itself (editable source), all three paths:

7. **`set sender of replyMessage` on the native window DROPS the embedded logo
   signature.** Symptom (user-reported): the saved draft shows the TULSA logo in the
   Drafts LIST preview, but opening it in the compose window shows a TEXT-ONLY
   signature (no logo). Root cause: the tool pinned the account's single alias via
   `set sender of replyMessage to item 1 of (email addresses ...)`. Changing the
   From on an open reply window makes Mail RE-INSERT the signature for that identity,
   and the programmatic re-insert is text-only — it drops the inline image. Source
   proof: with sender pinned, saved `source` was 5155 bytes, `multipart/related:0`,
   `image:0`; without it, 37065 bytes, `multipart/related:1`, `image:1`, with the
   reply-signature `cid` BEFORE the blockquote (i.e. on the reply itself).
   **Fix:** native path only sets the sender when the caller passes an explicit
   `from_address`; never pin the single alias. Mail's native reply already selects
   the correct From and inserts the full logo signature ("don't reinvent the wheel").
   The object-model (`native_format=False`) path keeps the single-alias pinning since
   it has no window to inherit identity from.
8. **Native default signature is not strictly verifiable.** Mail's own reply
   signature (rich text + logo) cannot be reliably substring-matched against
   `content of sig`, so the verifier was warning "signature missing" on a draft that
   visibly had the signature. Fix: for `native_format=True` with `include_signature`
   and no explicit `signature_name`, pass `signature_requested=None` to the verifier
   (skip the check). Explicit named signatures and the object-model path still verify.
9. **All three paths validated live (source-verified where rich):** native draft
   (logo + quote bar + body-on-top, one draft, window auto-closed), native open
   (same, window left open), and `native_format=False` (headless flattened draft,
   honest "signature missing" since the flattened path has none). GUARD retry/abort
   and `wrote:` quote-needle verification working.
10. **Exchange draft deletion is slow to propagate.** A `delete` may still show the
    message on a re-enumeration a few seconds later; an ~8s settle clears it. Use the
    `delete (item 1 of (every message of dm whose id is N))` form with `N` coerced to
    integer; deleting via a loop variable used directly in the `whose` clause is
    unreliable.
11. **Long first lines: the verifier was giving a false `BODY_MISSING`.** Mail's
    compose window soft-wraps long lines, and `content as string` renders the wrap as
    a line break — sometimes MID-WORD (`...the prev`⏎`iew).`). The verifier searched
    for the full first line (155 chars) as one contiguous substring, which the wrap
    defeated, so a perfectly good draft was reported `BODY_MISSING`. Fix: the verifier
    now strips CR/LF from the draft content (and needle) before the offset search
    (`stripLineBreaks` in `_verify_saved_reply_draft`), which rejoins wrapped text
    (incl. mid-word breaks) so the body is found regardless of wrap. The draft itself
    was always correct; this was a verifier false-negative only.

## VALIDATION RUN 4 — the logo-on-reopen symptom is NATIVE MAIL BEHAVIOR (user-confirmed)

User report: when a native reply DRAFT is double-clicked to OPEN in the compose
editor, the TULSA logo in the reply signature is gone, even though the Drafts LIST
preview shows it. We confirmed at the source level that the saved draft DOES embed
the logo (reply-signature `cid` + one `image/` part, before the blockquote). So the
logo is stored, not deleted.

**User reproduced this with a FULLY MANUAL draft** (Reply in Mail by hand → type →
Save to Drafts → reopen): the logo is also gone from the reopened editor. So this is
**Mail's own behavior** — its draft *editor* does not repaint an inline signature
image when reopening a saved draft. It is NOT caused by this tool, and there is no
known dictionary-level fix (we use Mail's real native signature; we never touch its
image encoding). Distinct from the sender-pinning bug (run 3, finding 7), which made
the logo genuinely absent from the saved source and IS fixed.

Practical consequences:
- A FRESH/live reply window (`mode="open"`, never saved+reopened) renders the logo —
  review-and-send in one sitting always shows it. This is the user-approved look.
- A saved draft stores the logo; the reopen-editor just doesn't preview it.
- STILL UNCONFIRMED (needs a real send): does the logo survive SEND of a saved draft?
  Source contains it, so it should; confirm by sending a test draft to self and
  checking the RECEIVED mail. (Cannot auto-send: hard rule.)
