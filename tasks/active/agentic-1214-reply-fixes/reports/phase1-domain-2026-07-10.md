# AGENTIC-1214 domain research: System Events keystroke reliability for the reply body

**Scope:** desk research only (web search plus this repo's own git history and archived
task docs). No AppleScript, Mail, or CLI execution. No file edits outside this report.
**Branch:** `fix/agentic-1214-reply-body-truncation` (read-only).
**Goal:** give the designer the facts needed to pick the reply-body insertion mechanism
that fixes Bug 1 (truncation) and Bug 3 (ALL CAPS) without reopening Bug 2's clipboard
class of failure or the earlier content-flattening bug.

---

## 1. Long-string keystroke truncation

No authoritative Apple document states a hard character limit for `System Events
keystroke`, and no forum thread converges on one specific chunk-size number. What is
consistently reported across Stack Overflow, the Keyboard Maestro forum, and general
AppleScript automation write-ups is a mechanism, not a constant:

- **The failure mode is a timing race, not a fixed byte limit.** Practitioners on the
  Keyboard Maestro forum describe it as the target app "reading fields, doing
  processing or checking, and writing fields back," and typing fast enough that the
  field gets read or rewritten mid-keystroke-stream, jumbling or losing characters.
  The forum's own fix is `Set Action Delay` (an artificial slowdown of the whole
  typing action), not a specific chunk size.
- **The converged workaround is per-character (or small-chunk) typing with an
  explicit inter-keystroke delay**, looping over the string and issuing one
  `keystroke` (or `key code`) per character rather than one `keystroke` call for the
  whole string. This shows up independently in the Keyboard Maestro discussions and
  in general "type text slowly" AppleScript gists (e.g. `gist.github.com/sscotth/...`,
  `gist.github.com/ethack/...`) built specifically to work around apps that drop
  characters from a single large `keystroke` call.
  Source: [Insert text by typing > slow?](https://forum.keyboardmaestro.com/t/insert-text-by-typing-slow/6125), [How do I add a delay for "Insert text by typing"?](https://forum.keyboardmaestro.com/t/how-do-i-add-a-delay-for-insert-text-by-typing/20681), [Paste as keystrokes (macOS)](https://gist.github.com/sscotth/310db98e7c4ec74e21819806dc527e97)
- **No numeric consensus exists for "safe chunk size."** Nothing in the sources
  names 200, 250, or any other specific character count as a threshold; the practical
  guidance is "type slower / in smaller pieces," tuned per receiving app, not a
  portable constant. Treat any specific number this repo picks (e.g. a chunk of N
  characters) as an empirically tuned value for Mail's WebKit-backed compose body,
  not a citable industry standard.
- **A plausible mechanism for this repo's specific 320-480 char cutoff:** Mail's
  reply-body text area is WebKit/HTML backed (confirmed by this repo's own live
  probes, section 4 below), not a plain `NSTextField`. A single long `keystroke`
  call fires a burst of synthetic key-down/key-up events through
  `CGEventPost` into the WindowServer event queue; if WebKit's editor is still
  laying out/reflowing the DOM from the first burst of characters when later events
  in the same burst arrive, later keystrokes can be dropped rather than queued. That
  is consistent with a fixed-ish cutoff (a proportion of the burst lands before the
  editor falls behind) recurring at a similar offset across runs, rather than a
  purely random drop count.
- **Adjacent, more recent evidence that synthetic keystroke delivery itself is
  getting stricter on modern macOS**: on macOS Tahoe (26), `CGEventPost`-synthesized
  events are now filtered by `CGXSenderCanSynthesizeEvents()` before some
  hotkey-matching paths, based on the *sending process's* identity/signing. That
  specific report is about global hotkey listeners, not `System Events keystroke`
  into a focused app's text field, so it does not directly explain Bug 1, but it is
  a signal that this OS generation (Darwin 25.5, the host this repo already targets
  per `tasks/active/native-reply/`) is actively tightening synthetic-event handling,
  which raises the plausibility of throughput-sensitive drops inside a WebKit editor
  too.
  Source: [What replaced CGEventPost in my Stream Deck daemon](https://www.nick-liu.com/posts/tahoe-hotkey-dead-end/)

**Actionable takeaway:** chunk the `keystroke` call (character-level or small
fixed-size chunks) with a short delay between chunks, and re-verify window focus
between chunks (see section 5). There is no citable "correct" chunk size; it must be
tuned empirically against Mail's compose body and covered by a live-verification pass
with a body long enough to have previously truncated (the field report's ~1000-char
body, not the short probe bodies this repo's own prior validation used — see section 4
note on `VALIDATION RUN 2`).

---

## 2. The ALL CAPS failure mode

### Known causes

- **This is a long-documented, unfixed-in-general System Events bug class**, not
  specific to this repo. The clearest primary-source hit is [Apple Developer Forums
  thread 70284](https://developer.apple.com/forums/thread/70284): on macOS 10.12.2
  through 10.12.3 beta 4, `keystroke` of a string like `mmalibu@zetetic.net`
  intermittently rendered as `mmalibu@Zetetic.net`, `mmalibu@ZEtetic.net`, or
  `mmalibu@ZETETIc.net` — correct only "about 1 in 10" times. The same thread also
  reproduces the openradar-mirrored radar
  [#16313](https://github.com/lionheart/openradar-mirror/issues/16313) description:
  "lowercase letters followed after an uppercase letter or symbol that requires the
  press of the shift key modifier appear as uppercase or mixed case ... even though
  no modifier keys are pressed on the keyboard." Apple never posted a public root
  cause in either report; the openradar issue is stale/unresolved.
- **The reported mechanism (as far as external sources describe it) is a stuck or
  leaking shift-key state inside the synthetic keystroke pipeline**, not the user's
  physical Shift key. It is consistent with, though not proven identical to, a case
  where an internally-tracked "shift required for this character" modifier flag
  from one synthesized character bleeds into the processing of the next character(s).
  This matches the task's suspicion of "shift or caps state ... possibly left over
  from a prior interrupted typing pass": a truncated/aborted `keystroke` call (the
  repo's own Bug 1) is exactly the kind of interrupted stream that could leave such
  state dangling for the *next* keystroke call in the same process/session.
- **The one apple-forums-documented workaround is per-character typing with an
  explicit `key up {shift}` after every character**:
  ```applescript
  repeat with i from 1 to len
      set x to character i of outputString
      keystroke x
      key up {shift}
  end repeat
  ```
  The original poster confirmed this masked the symptom until Apple shipped a real
  fix in 10.12.4 beta 1. That the bug needed an actual OS patch (not just a scripting
  workaround) to fully resolve on 10.12, and that this repo is seeing the same
  symptom class on a completely different macOS generation (Darwin 25.5, ~9 years
  later), suggests this is a recurring/regressed defect in the synthetic-keystroke
  pipeline rather than something this repo's own code can fully guarantee against —
  only mitigate.
- **Caps Lock (key code 57) is a documented dead end for this bug family, in both
  directions.** Multiple independent sources agree key code 57 does not toggle Caps
  Lock via `System Events key code`/`keystroke` (it "doesn't look like key code 57
  does anything" via System Events). That rules out "the tool is accidentally
  toggling caps lock via key code 57" as the mechanism; the ALL CAPS symptom is a
  shift/modifier-state artifact of the keystroke pipeline itself, not a Caps Lock
  toggle.

### Reading Caps Lock state from System Events: verified false

**There is no documented way to read Caps Lock state via `System Events` /
AppleScript**, confirming the task's framing. The only Apple-sanctioned mechanism is
[Technical Q&A QA1519 "Detecting the Caps Lock Key"](https://developer.apple.com/library/archive/qa/qa1519/_index.html),
which is a **compiled Cocoa API only**: override `NSResponder`'s `flagsChanged:` and
inspect `[event modifierFlags] & NSAlphaShiftKeyMask`. This requires a native app (or
a small compiled helper) receiving real key events; it is not exposed through
AppleScript, `osascript`, or `System Events`. Community workarounds for reading Caps
Lock state from a *script* go through a small compiled C helper (Carbon
`GetCurrentKeyModifiers()`, bitwise-AND against `0x0400`) invoked via `do shell
script`, not anything System Events exposes natively.

**Practical implication:** the tool cannot check "is caps lock/shift stuck" before
typing and branch accordingly using pure AppleScript/System Events. The only lever
available from System Events is defensive: explicitly `key up` every modifier
(shift, command, option, control) immediately before the keystroke call, to clear any
state System Events itself may still be holding from this process's own prior
synthetic events, and treat the guard-retry loop already in `reply_scripts.py`
(`repeat with guardAttempt from 1 to 4`, lines ~428-477) as the natural place to reset
modifier state before every retry, not just window focus.

---

## 3. Does Mail apply autocorrect/substitutions to typed text in the compose body?

**Evidence points to yes, substitutions are live in Mail's compose windows by
default, which matters for any case-sensitive full-body readback compare.**

- Multiple independent how-to sources confirm Mail exposes the same **Edit >
  Substitutions** menu as TextEdit/other AppKit apps (smart quotes, smart dashes,
  text replacement, auto-capitalization depending on OS version), and that these
  substitution toggles are **remembered message-to-message** (a per-app/per-session
  preference, not a one-time opt-in) rather than defaulting fully off.
  Sources: [Using Automatic Text Substitutions On a Mac](https://macmost.com/using-automatic-text-substitutions-on-a-mac.html), [How to Turn off Smart Quotes - Apple Community](https://discussions.apple.com/thread/6436023)
- **Mechanism**: these substitutions are implemented at the `NSTextView`/text-input
  layer (`NSSpellChecker` "automatic text replacement" hooks), which is the same
  general layer System Events keystroke injection ultimately lands in for
  AppKit-native fields. Whether Mail's *specific* compose body (a WebKit-hosted
  HTML editor per this repo's own probes, not a plain `NSTextView` — see section 4)
  routes typed characters through the same NSSpellChecker substitution path as
  TextEdit is not independently confirmed in the sources found; WebKit editable
  regions can implement their own subset of substitution behavior distinct from
  native NSTextView. Either way, the *safe assumption for this repo* is that smart
  quotes and dashes are live by default (this is broadly true of macOS text fields
  system-wide and specifically called out for Mail by name in the sources above),
  which already matters for the reply body: straight quotes (`'`, `"`) or double
  hyphens the caller sends may be silently rewritten to curly quotes/em dashes by
  the time they land in `content`.
- **Consequence for verification design**: a byte-exact, case-sensitive substring
  compare of the *entire* typed body against the saved draft's `content` is not
  provably safe even before considering the ALL CAPS bug, because ordinary
  substitution (quotes/dashes) can make an exact match fail on a perfectly good
  draft. This is a second, independent reason (beyond the documented AppleScript
  string-comparison case-insensitivity in section on root cause 3) that a naive
  case-sensitive full-body `is equal to` check is the wrong verification primitive.
  A verification design should either (a) normalize known substitution classes
  (smart quotes, dashes) on both sides before compare, similar to the existing
  `stripLineBreaks` normalization already in `saved_draft_checks.py`, or (b) verify
  structural properties (full length within tolerance, no ALL-CAPS ratio anomaly,
  needle-at-multiple-offsets) rather than one whole-string equality.
- I found no primary source specifically documenting whether Mail's WebKit compose
  body auto-capitalizes the first letter of sentences (a distinct feature from smart
  quotes/dashes, and the one that would most directly explain "GEOFF, THANKS..."
  becoming all-caps rather than just first-letter-capitalized). Given the ALL CAPS
  pattern reported (entire short bodies, not just first letters) matches the shift-
  state bug in section 2 far better than a sentence-capitalization substitution
  (which would capitalize only first letters, not entire words), the balance of
  evidence still favors the System Events shift-state bug over an NSTextView/WebKit
  substitution as the primary cause of Bug 3. Substitutions remain a secondary risk
  specifically for the verifier's exact-match design, not the primary explanation
  for full-body ALL CAPS.

---

## 4. This repo's history: clipboard tried and reverted (with commit evidence)

Clipboard-based reply-body insertion was **attempted twice** in this repo's history
and **reverted both times**, for two different, well-documented failure modes. The
current typed-keystroke-only approach is the result of both reversions plus a third
transition away from AppleScript `content` reassignment. Full chain, oldest to
newest (`git log --oneline --all -S clipboard -- .` / `-S keystroke -- .`, confirmed
with `git show -p` on each commit):

| Date | Commit | What changed | Why |
|---|---|---|---|
| 2026-03-10 | `69fdfc3` | Introduced per-line `keystroke` typing for `mode="open"` replies (no clipboard yet). | First attempt to preserve Mail's native quoted original without flattening `content`. |
| 2026-03-11 | `a4af837` | Switched `mode="open"` from that "fragile per-char keystroke" to `set the clipboard to replyBodyText` + `keystroke "v" using command down`. Commit message calls the prior keystroke path "fragile." | Reduce keystroke fragility for long/special-character bodies. |
| 2026-03-27 (PR #32) | `3a0a522` / `59d5a05` | Expanded clipboard to **all** reply modes (send/draft/open) and to `forward_email`, using full `NSPasteboard` **HTML** clipboard injection (`AppleScriptObjC`, `NSPasteboardTypeHTML`), to preserve Mail's native quoted original across every mode. | `set content of` was destroying thread history in all non-open modes; clipboard paste was the fix at the time. |
| **2026-06-05** | **`6bfe72b`** (v3.6.0) | **First revert.** Removed clipboard entirely; rebuilt replies through Mail's object model (`make new outgoing message`, no window, no clipboard, no System Events). | Commit message, verbatim: *"Second live draft-QA session proved the 3.5.0 `saving no` change was insufficient: reply_to_email/forward_email were driving Mail's GUI (open window → clipboard keystroke "v" → positional close window 1), which on the 24K TU Exchange inbox produced duplicate drafts, an empty draft, **a CROSS-THREAD body leak (a reply's body landed in an unrelated thread)**, and dropped reply-all recipients."* Trade-off explicitly accepted: replies became plain-text `Re:`/`Fwd:` drafts with a `>`-quoted original, losing native `In-Reply-To`/`References` headers. |
| 2026-06-08 | `80b0d82` | Re-introduced native `reply ... with opening window` (to get the colored quote bar + logo signature back) **with clipboard** (`set the clipboard to replyBodyText` + `keystroke "v"`), this time with proper save/restore of the user's prior clipboard contents. | Wanted the native render back without repeating the exact `6bfe72b` failure; added clipboard save/restore as a partial mitigation. |
| 2026-06-08 | `67db4d2` | Added post-save bounded-Drafts verification on top of `80b0d82`. | Safety net for the reintroduced GUI path. |
| **2026-06-17 to 2026-06-19** | **`3d8c81a`** (v3.7.2) and **`5590efd`** | **Second revert.** Removed clipboard again, replaced with `set content of replyMessage to replyBodyText & return & return & nativeReplyContent` (reading Mail's own rendered `content` and re-prepending the reply body). | CHANGELOG (`3d8c81a`), verbatim: *"Reply drafts no longer rely on clipboard paste for body insertion, reducing body loss, attachment/body ordering races, and duplicate signature-only draft risk."* The archived incident doc [`tasks/archive/2026-06/issues/reply-body-insertion-failure-2026-06-18.md`](../../../archive/2026-06/issues/reply-body-insertion-failure-2026-06-18.md) documents the concrete symptom that forced this: draft saved and reported success, but the body was silently missing (only signature + quoted original present) — called "unsafe false progress" because the tool claimed success on a broken draft. Doctrine written down immediately after in [`reply-draft-verification-hardening-2026-06-19.md`](../../../archive/2026-06/issues/reply-draft-verification-hardening-2026-06-19.md): *"avoid reply-body insertion through UI scripting, clipboard paste, or focus-sensitive windows,"* enforced at the time by tests asserting `assertNotIn("System Events")`, `assertNotIn('keystroke "v"')`, `assertNotIn("NSPasteboard")` in the reply path. |
| 2026-06-30 | `e31ae0d` (v3.8.0) | **Third transition** (not a clipboard revert — a `content`-reassignment revert). Replaced the `3d8c81a`/`5590efd` `set content` approach with the **current** design: native `reply ... with opening window` + plain **typed** `keystroke` (no clipboard) above the quote, never touching `content`. | The `set content` approach from the second revert had its own bug: `content` over Apple Events is plain text, so any write to it (even prepend-and-reassign) flattens the native rendering, dropping the colored quote bar (`<blockquote type="cite">` block structure), displacing the signature, and losing the embedded logo image. Root-caused and validated live in [`tasks/active/native-reply/native-reply-formatting-investigation-2026-06-30.md`](../../native-reply/native-reply-formatting-investigation-2026-06-30.md) (RESOLUTION section, validated on real mail 2026-06-30). |

**Current docstring** (`plugin/apple_mail_mcp/tools/compose/reply_scripts.py:320-337`,
introduced verbatim in `e31ae0d`) captures the net lesson from both reverts in one
place: never reassign `content` (flattens quote bar/signature — the second revert's
bug) and never use the clipboard (clobbers the pasteboard, leaked bodies into the
wrong thread — the first revert's bug, `6bfe72b`'s "CROSS-THREAD body leak").

**Important nuance for the current bug (truncation/ALL CAPS):** the 2026-06-30
validation that produced the current typed-keystroke design (`native-reply-probes-
2026-06-30.md`, "VALIDATION RUN 2", point 4) tested `keystroke` with short
multi-paragraph probe bodies (`PROBE_SENTINEL_ALPHA`/`BETA`, not measured in the
hundreds of characters) and concluded "multi-line preserved... works," with no
truncation observed. That validation never exercised a ~1000-char body, so the
320-480 char truncation this field report found is not a regression from that design
decision — it is an untested body-length regime the original validation didn't cover.
This matters for scoping the fix: the "never clipboard, never content-reassignment"
doctrine from both reverts should hold, but the *typed-keystroke mechanism itself*
still needs the chunking/guard hardening described in sections 1 and 2, since it was
never validated at field-report scale.

**`compose/send.py:155`'s `keystroke "v" using command down`** is a
**separate, still-live, and doctrinally distinct** clipboard path: it belongs to
`_send_html_email()`, the HTML-body helper for **`compose_email`** (new standalone
messages with `body_html=...`), not to `reply_to_email`. It uses `NSPasteboard`
HTML injection with explicit clipboard save/restore (`oldClip` captured before
`clearContents()`, restored after paste) because `compose_email` has no analogous
"Mail already rendered rich content natively" problem to solve — there is no native
Mail HTML compose contract to preserve, so the tool has to inject HTML somehow, and
clipboard-paste-into-a-known-single-fresh-window is safe there in a way it was not
safe for replies driving into an *existing, ambiguous* thread context. This is a
**documented, deliberate exception**, not an oversight:
[`tasks/archive/2026-06/issues/reply-draft-verification-hardening-2026-06-19.md`](../../../archive/2026-06/issues/reply-draft-verification-hardening-2026-06-19.md)
item 6 states explicitly: *"Isolate remaining UI-scripting exceptions outside the
reply path and document why HTML compose still uses pasteboard/UI scripting ...
HTML compose remains a documented exception because Mail's dictionary exposes
deprecated/no-op HTML content."* Do not treat `send.py:155` as precedent for
reintroducing clipboard into the reply path; it is exempted for a different reason
(no rendering-preservation constraint, single always-fresh window) that does not
apply to replies.

---

## 5. Alternatives ranked for this repo

Constraints restated: native reply window is fixed (only source of the colored quote
bar + logo signature); `content of replyMessage` must never be reassigned (flattens
both); clipboard for the *reply body specifically* is doctrinally banned by two prior
incidents (cross-thread leak, then silent body loss under focus races), enforced by
existing tests (`assertNotIn`-style guards per `reply-draft-verification-hardening-
2026-06-19.md`).

### (a) Chunked keystroke with focus re-check between chunks — recommended

Split `replyBodyText` into fixed-size chunks (character count TBD empirically, well
under the observed ~320-char truncation floor — start conservative, e.g. 100-150
chars, and widen only after a live test proves reliability), and between chunks:
explicitly `key up {shift, command, option, control}` (clears any lingering modifier
state per section 2's only documented mitigation), re-run the existing Mail-dict +
System-Events front-window guard (the guard loop already exists at
`reply_scripts.py:428-477` for the pre-typing check; extend it to re-fire between
chunks, not just before the first chunk), and insert a short delay (tunable, start
at the scale of the existing `delay 0.3`/`0.5` calls already in this file) before
the next chunk.

- **Failure modes:** still fundamentally GUI/focus-driven (same class as the
  existing design, so it does not introduce a *new* risk category); a focus loss
  mid-chunk-sequence is now easier to detect (guard re-check between chunks) and
  abort-clean from, versus the current single-shot call where a mid-stream drop is
  invisible until verification. Slower (more delay calls) for long bodies, and the
  original ALL CAPS bug may still occur *within* a chunk even with `key up` between
  chunks (the Apple Forums fix released `key up` after *every character*, not just
  between chunks — worth prototyping both granularities and letting the live-check
  agent measure which one actually clears the symptom on Darwin 25.5, since the
  documented fix is nine OS generations old and unverified on this host).
- **Why recommended:** it is a strict superset of the current design (same
  mechanism, hardened), so it does not reopen either reverted failure class, needs
  no new AppleScript capability, and both known bugs (truncation via smaller
  bursts + refreshed focus, ALL CAPS via `key up` between/within chunks) have a
  concrete, sourced mitigation path through this one change. It also keeps the
  existing verification and guard infrastructure fully reusable.

### (b) Clipboard paste with save/restore and strict focus guard — not recommended

Even implemented "well" (as `80b0d82` already did: clipboard save/restore,
guarded focus check before paste), this is the exact mechanism that produced the
cross-thread body leak in `6bfe72b` on a live 24K-message Exchange inbox, and was
independently re-abandoned a second time (`3d8c81a`) for "body loss, attachment/body
ordering races, and duplicate signature-only draft risk" under focus races. Two
separate live-incident classes on two separate implementations of this same idea is
a strong prior against a third attempt, and it directly contradicts the standing
written doctrine (`reply-draft-verification-hardening-2026-06-19.md`) and its
enforcing tests. Revisiting this option would require deleting or rewriting those
doctrine tests, which is itself a strong signal this is the wrong path absent new
evidence the underlying Mail/Exchange race is now fixed.

- **Failure modes:** cross-thread leak (proven, `6bfe72b`), clobbered user clipboard
  during the paste window (mitigated but not eliminated by save/restore — a
  concurrent clipboard write from the user or another app between capture and
  restore still corrupts one or the other), and silent body loss under focus races
  (proven, `3d8c81a`/`5590efd`).

### (c) Setting AXValue of the compose body text area via accessibility — unproven, likely fragile for this specific view

No evidence was found, in this repo's own live probes or in external search, of
`AXValue` being successfully set on Mail's reply compose body specifically. What is
established:

- `AXValue` is a generically *settable* attribute on `AXTextField`/`AXTextArea`
  accessibility roles system-wide (best-effort; the element is focused as part of
  the set), per general macOS Accessibility API documentation.
- Mail's compose body is **not** a plain `NSTextView`/`AXTextArea` — it is rendered
  by an internal WebKit HTML editor. This repo's own investigation
  (`native-reply-formatting-investigation-2026-06-30.md`) independently discovered
  the same thing from a different angle: "the native quote and signature only
  materialize once a compose window renders them," "Mail's WebKit editor owns the
  content" once a window is open, and in-place element edits against that live
  document "silently no-op." WebKit-hosted editable regions expose accessibility
  through `AXWebArea` and nested roles that do not reliably support write-through
  `AXValue` the way a native `NSTextView`'s `AXTextArea` does; setting a value on a
  WebKit contentEditable region through the generic Accessibility API risks the
  same "no-op against the live document" failure mode already documented for direct
  element edits, or a value that is accepted by the AX layer but not reflected back
  into WebKit's internal DOM/undo state (leaving Mail's own "unsaved changes"
  tracking desynced from what the user sees).
- No public report was found (Stack Overflow, MacScripter, developer forums) of
  anyone inserting reply text into Mail.app specifically via `AXValue`; every
  community-documented mechanism for typing into Mail's compose body uses either
  `keystroke` or clipboard paste, which is itself circumstantial evidence that
  `AXValue` does not work reliably against this particular view, or no one has
  needed to try since keystroke/paste already "work" for short bodies.
- **Failure modes (projected, not observed):** silent no-op (AX call returns
  success, body unchanged, exactly the failure class this repo already hit once
  with direct `content` element edits); desync between WebKit's DOM and Mail's
  save/dirty state; no guarantee it respects the "insert above the quote, don't
  touch the quote" placement requirement the way `keystroke`-at-cursor does, since
  `AXValue` on a text area typically **replaces the whole value**, which risks
  becoming a third variant of the already-twice-reverted "reassign the whole body
  and flatten the native quote" bug class if the body area's `AXValue` actually
  encompasses the full rendered content rather than just an empty top region.
- **Recommendation:** worth a cheap, isolated, read-only accessibility-inspector
  probe (e.g., Apple's Accessibility Inspector or an `ax-cli`-style tool, run
  against an *already-open* native reply window) to confirm the actual role/
  hierarchy and whether `AXValue` is even present and settable on the body region,
  before spending implementation effort. Do not adopt without that confirmation; the
  prior probability of it working cleanly on a WebKit-hosted editable region is low
  based on available evidence.

### (d) Per-character key codes — not recommended as the primary mechanism, viable as the finest-grained fallback within (a)

This is really the extreme end of option (a)'s chunk-size spectrum (chunk size = 1),
using `key code` for literal keys and `keystroke` only for characters without a
direct scan code (accented/Unicode characters typically still need `keystroke`, not
`key code`, since `key code` addresses physical key positions, not arbitrary Unicode
scalars). The single Apple-forums-documented fix for the ALL CAPS bug specifically
is per-character `keystroke` + `key up {shift}` (section 2), which is this
granularity.

- **Failure modes:** slowest option by far for a ~1000-char body (one AppleScript
  loop iteration + `System Events` round-trip per character); `key code` cannot
  express the full Unicode range a reply body may contain (em dashes, curly quotes
  already normalized in by `escape_applescript`, non-ASCII names), so a pure
  `key code` implementation is not viable standalone — it would need to fall back to
  `keystroke` for anything without a direct code, which reintroduces exactly the bug
  this option is meant to dodge for those characters.
- **Recommendation:** do not build this as a separate mechanism from (a). Instead,
  make chunk size a tunable in the (a) implementation and let the live-verification
  agent's field test (long body + short/ALL-CAPS-prone body) determine the smallest
  chunk size that actually clears both bugs; if that empirically converges on
  chunk-size-1 for the ALL CAPS case specifically (matching the one documented fix),
  that is (d) implemented as (a)'s floor, not a separate code path.

### Overall recommendation

Ship (a) — chunked `keystroke` with modifier-clearing (`key up`) and a re-fired
focus guard between chunks — as the mechanism. Treat (b) as closed by two prior
live-incident reverts and standing doctrine; do not reopen without new evidence the
underlying Exchange/focus race is gone. Treat (c) as a cheap pre-implementation
accessibility-inspector probe worth running once, but do not plan on it as the
primary fix given the WebKit-editor evidence against it. Treat (d) as (a)'s
chunk-size floor, not a separate design.

---

## Sources

- [AppleScript / System Events keystroke ... (Apple Developer Forums thread 70284)](https://developer.apple.com/forums/thread/70284)
- [29182929: AppleScript and System Events "keystroke" function lead to incorrect casing (openradar-mirror #16313)](https://github.com/lionheart/openradar-mirror/issues/16313)
- [Technical Q&A QA1519: Detecting the Caps Lock Key](https://developer.apple.com/library/archive/qa/qa1519/_index.html)
- [Insert text by typing > slow? (Keyboard Maestro Discourse)](https://forum.keyboardmaestro.com/t/insert-text-by-typing-slow/6125)
- [How do I add a delay for "Insert text by typing"? (Keyboard Maestro Discourse)](https://forum.keyboardmaestro.com/t/how-do-i-add-a-delay-for-insert-text-by-typing/20681)
- [Paste as keystrokes (macOS) gist](https://gist.github.com/sscotth/310db98e7c4ec74e21819806dc527e97)
- [Scripts that simulate typing the clipboard contents gist](https://gist.github.com/ethack/110f7f46272447828352768e6cd1c4cb)
- [What replaced CGEventPost in my Stream Deck daemon (macOS Tahoe CGEventPost filtering)](https://www.nick-liu.com/posts/tahoe-hotkey-dead-end/)
- [Using Automatic Text Substitutions On a Mac](https://macmost.com/using-automatic-text-substitutions-on-a-mac.html)
- [How to Turn off Smart Quotes - Apple Community](https://discussions.apple.com/thread/6436023)
- [Accessing text value from any System wide Application via Accessibility API](https://macdevelopers.wordpress.com/2014/01/31/accessing-text-value-from-any-system-wide-application-via-accessibility-api/)
- [NSTextView Set and Get Value? (MacScripter)](https://www.macscripter.net/t/nstextview-set-and-get-value/61980)
- This repo, git history (`git log -S clipboard`, `-S keystroke`, `git show -p`) on commits `69fdfc3`, `a4af837`, `3a0a522`/`59d5a05`, `6bfe72b`, `80b0d82`, `67db4d2`, `3d8c81a`, `5590efd`, `e31ae0d`
- `tasks/archive/2026-06/issues/reply-body-insertion-failure-2026-06-18.md`
- `tasks/archive/2026-06/issues/reply-draft-verification-hardening-2026-06-19.md`
- `tasks/active/native-reply/native-reply-formatting-investigation-2026-06-30.md`
- `tasks/active/native-reply/native-reply-probes-2026-06-30.md`
- `plugin/apple_mail_mcp/tools/compose/reply_scripts.py`, `plugin/apple_mail_mcp/tools/compose/saved_draft_checks.py`, `plugin/apple_mail_mcp/tools/compose/send.py`, `plugin/apple_mail_mcp/applescript_snippets.py`
