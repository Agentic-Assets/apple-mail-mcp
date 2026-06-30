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
