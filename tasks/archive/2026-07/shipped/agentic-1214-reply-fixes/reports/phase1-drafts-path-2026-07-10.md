# Phase 1 research: `manage_drafts` and draft identity (AGENTIC-1214, Bug 2 + id-stability observation)

Date: 2026-07-10
Scope: read-only research, no edits, no live Mail. Answers the six questions in the
dispatch prompt with file:line anchors and direct quotes.

## 1. `manage_drafts(action="create")` full flow: what happens to `in_reply_to`

`in_reply_to` is **silently ignored** on `action="create"`. It is not validated, not
read, not passed into the AppleScript, and not mentioned in the create output.

Evidence, `plugin/apple_mail_mcp/tools/compose/manage.py`:

- `in_reply_to` is a top-level parameter of `manage_drafts` (`manage.py:40`) and is
  documented in the docstring as scoped **only** to `action="find"`:
  `manage.py:66`: `"in_reply_to: For action=\"find\", source Internet Message-ID to match against Drafts In-Reply-To or References headers."`
- The only two other places `in_reply_to` appears in the whole file are the `find`
  branch's own required-arg check (`manage.py:127-128`) and its script-builder call
  (`manage.py:132`), plus a `TARGET_SELECTOR_DEPRECATED` discovery hint string
  (`manage.py:79`) that recommends `action='find', in_reply_to=...` as a *separate*
  lookup path.
- The entire `action == "create"` branch is `manage.py:136-226`. A `grep -n
  "in_reply_to" plugin/apple_mail_mcp/tools/compose/manage.py` confirms the token
  never occurs inside that branch. The AppleScript built at `manage.py:189-226`
  constructs the outgoing message purely from `subject`, `body`/`content`, `to`/
  `cc`/`bcc` recipients, and an optional sender override; there is no header-setting
  step of any kind.

So a caller can pass `manage_drafts(action="create", ..., in_reply_to="<msg-id>")`
and the call succeeds (assuming the reply-like guard, see §2, does not block it)
with `in_reply_to` accepted by the function signature and then dropped on the floor.
This exactly reproduces AGENTIC-1214 Bug 2: the draft body/recipients save fine, but
`verify_draft` later reports `threading: {in_reply_to: "", references: ""}` because
nothing ever set those headers (and, per §3, nothing *could* set them through this
code path even if the parameter were wired up).

## 2. The reply-like guard on create

**What it checks.** `manage.py:140-142` calls the shared guard with the create
params:

```python
thread_warning = _standalone_compose_thread_warning(subject, body, None, standalone_confirmed)
if thread_warning:
    return thread_warning
```

The guard itself lives in `plugin/apple_mail_mcp/tools/compose/payload.py:117-145`.
It looks only at `subject` and `body` text, never at `in_reply_to`:

- `payload.py:128-129`: a compiled regex (`_THREADED_SUBJECT_RE`,
  `payload.py:14`) flags a `Re:`/`Fw:`/`Fwd:` subject prefix.
- `payload.py:131-133`: a compiled regex (`_QUOTED_THREAD_MARKERS_RE`,
  `payload.py:15`) flags quoted-thread markers (`On ... wrote:`, `-----Original
  Message-----`, `From:` lines, `>`-quoted lines) in the combined body/HTML text.
- If neither signal fires, the guard returns `None` (allowed) regardless of
  `standalone_confirmed`.
- If either signal fires and `standalone_confirmed` is falsy, it returns a refusal
  string.

**Exact refusal code and message.** There is no structured error code (no `code:
...` JSON envelope) — it is a plain string, identical across all three callers
(`compose_email`, `manage_drafts(action="create")`, `create_rich_email_draft`)
because they all share this one function (`payload.py:138-145`):

```python
return (
    "Error: compose_email creates a standalone new message and will not "
    "include the original email thread. This draft looks like a reply or "
    f"forward ({', '.join(signals)}). Use reply_to_email(message_id=...) "
    "or forward_email(message_id=...) after locating the source message. "
    "If you intentionally want a brand-new standalone message, pass "
    "standalone_confirmed=True."
)
```

Two problems this raises for the field report:

1. **Wrong tool name in the message.** When this fires from
   `manage_drafts(action="create")`, the returned text still says *"Error:
   compose_email creates a standalone new message..."* — it names the wrong tool.
   This is not cosmetic: an agent reading this refusal from a `manage_drafts` call
   has no textual signal that the refusal came from `manage_drafts`, only that
   `compose_email` (a tool it did not call) is somehow implicated.
2. **No mention of `in_reply_to` or of create's threading limitation.** The message
   never explains that `manage_drafts(action="create")` cannot thread a draft even
   when `in_reply_to` is supplied, and does not tell the caller that
   `standalone_confirmed=True` will make the parameter get silently dropped rather
   than honored. A caller who supplied `in_reply_to` specifically *because* they
   want a threaded draft gets a message whose only path forward
   (`standalone_confirmed=True`) still produces an unthreaded message — see §5 for
   where a warning could be added instead.

**How `standalone_confirmed` bypasses it.** `payload.py:124-125`: `if
standalone_confirmed: return None` — a single unconditional early return. It is a
pure bypass switch with no interaction with `in_reply_to`; passing both
`standalone_confirmed=True` and `in_reply_to=<id>` together silences the guard and
still drops the header (per §1).

**Test coverage gap.** `tests/compose/test_compose_tools.py`
`ManageDraftsCreateSenderOverrideTests` (`tests/compose/test_compose_tools.py:2673+`)
covers the guard for reply-like subjects with and without `standalone_confirmed`
(`test_compose_tools.py:2680-2719`), but no test in the repo calls
`manage_drafts(action="create", ..., in_reply_to=...)`. `grep -n "in_reply_to"
tests/compose/test_compose_tools.py` shows the only `in_reply_to` usages are around
`verify_draft(resolve_source=...)` (`test_compose_tools.py:2926`, `2972`, `3197`,
`3210`) and `manage_drafts(action="find", in_reply_to=...)`
(`test_compose_tools.py:3345-3359`). The silent-drop behavior on `create` is
unguarded by any regression test today.

## 3. Feasibility verdict: can Mail AppleScript set In-Reply-To/References on an outgoing message?

**Verdict: impossible via the documented Mail scripting dictionary; possible only
via Mail's own `reply`/`forward`/`redirect` commands, which populate those headers
internally and are not exposed as settable properties.**

Evidence from the local dictionary,
`/System/Applications/Mail.app/Contents/Resources/Mail.sdef`:

- The compose class is `outgoing message` (`bcke`, `ComposeBackEnd_Scripting`),
  defined at `Mail.sdef:269-303`. Its **complete** property/element list is:
  `sender`, `subject`, `content` (rich text), `visible`, `message signature`, `id`
  (read-only integer, "the unique identifier of the message"), plus two
  deprecated/no-op properties (`html content`, `vcard path`). Its only `element`
  declarations are `bcc recipient`, `cc recipient`, `recipient`, `to recipient`
  (`Mail.sdef:272-275`). **There is no `header` element and no In-Reply-To /
  References property anywhere on this class.** You cannot `make new header ... of
  newDraft` because `outgoing message` never declares that it has headers as
  children at all.
- Contrast with the separate `message` class (`Mail.sdef:558+`, the saved/received/
  sent class, `MCMessage`), which *does* declare `element type="header"`
  (`Mail.sdef:560-561`, `cocoa key="appleScriptHeaders"`) and a read-only `all
  headers` property (`Mail.sdef:569`, `access="r"`) and a read-only `message id`
  property (`Mail.sdef:593`, `cocoa key="scriptedMessageIDHeader"`, "The unique
  message ID string"). This is exactly the class the plugin already reads from —
  `thread_headers_block()` in `plugin/apple_mail_mcp/applescript_snippets.py:61-103`
  parses `all headers of <message>` text for `In-Reply-To:`/`References:` lines —
  but that path is read-only and only works on messages that already have those
  headers (received mail, or a message Mail itself constructed via `reply`), never
  on a bare `make new outgoing message`.
- Repo-wide `grep -rn "In-Reply-To\|References:" plugin/apple_mail_mcp/` (see
  `applescript_snippets.py:95-98`) shows the string literals `"In-Reply-To:"` and
  `"References:"` are used exclusively as **read-side** prefix matches inside
  `thread_headers_block`, never on a write/`set` line anywhere in the codebase.
- `git log --oneline --all -S "in_reply_to"` and `-S "References"` against
  `plugin/apple_mail_mcp` (repo history) show every commit that touched these
  strings is a feature that *reads* headers (`get_email_by_id`'s header parsing,
  `manage_drafts(action="find")`'s bounded header scan, `verify_draft`'s
  `threading` block, `get_awaiting_reply`'s Message-ID cross-reference). None is a
  write attempt; there is no reverted "set In-Reply-To" commit to find, because no
  such commit was ever written — the dictionary never offered the property to try.
- `tasks/archive/2026-06/issues/issue-find-draft-by-in-reply-to-2026-06-24.md:26-33`
  documents the same conclusion from the other direction: it explicitly answers
  "does Mail.app expose a Message-ID header accessor in AppleScript?" with **"It
  does, and the plugin already uses it"** — for *reading* (`get_email_by_id` parses
  `in_reply_to`/`references` from `all headers`). The issue proposes only a
  *lookup* feature (`manage_drafts(action="find", in_reply_to=...)`, which shipped),
  never a *write* feature, because the dictionary does not support one.

**The only way Mail produces a correctly-threaded outgoing message is the `reply`
(and `forward`/`redirect`) command** (`Mail.sdef:247-256`, `result type="outgoing
message"`), which Mail's own internals populate with In-Reply-To/References before
handing the object back to the script. That is precisely the mechanism
`reply_to_email`'s native path already uses (`reply ... with opening window`) and
that `plugin/skills/email-drafting/SKILL.md:26` and
`docs/CLAUDE-conventions.md:149` both codify as the rule: *"Never use standalone
draft creators (`compose_email`, `create_rich_email_draft`, or
`manage_drafts(action="create")`) to answer an existing message."* There is no
"possible-only-via-X" workaround inside `manage_drafts(action="create")` itself —
X here is "call `reply` instead of `make new outgoing message`", which is a
different code path entirely (already implemented as `reply_to_email`), not a
parameter you can add to `create`.

**Conclusion for the fix:** `in_reply_to` on `manage_drafts(action="create")` cannot
be honored by setting a header property; the only correct fixes are (a) make the
tool refuse the combination with a clear, accurate error (structured code +
message naming `manage_drafts`, telling the caller to use `reply_to_email` instead),
or (b) accept it as a *label-only* hint that never claims real threading and is
surfaced back to the caller as an explicit warning (see §5). Silently accepting and
dropping it, as today, is the one option that should not survive.

## 4. Draft id lifecycle

**Where the numeric id comes from.** For `action="create"`,
`manage.py:210-213`:

```python
set draftId to ""
try
    set draftId to id of newDraft as string
end try
```

captures `id of newDraft` (the `outgoing message`'s own `id` property,
`Mail.sdef:290-293`, read-only "unique identifier of the message") **after**
`save newDraft` (`manage.py:208`) and a `delay 0.5` (`manage.py:209`). For
`action="list"`/`"find"`, the id instead comes from re-enumerating the Drafts
mailbox as `message` objects and reading each one's `id` property
(`drafts_scripts.py:73`: `set draftId to (id of aDraft) as string`,
`drafts_scripts.py:175`: `set draftId to id of aDraft as string`) — a different
Cocoa key (`libraryID` on `message`, `Mail.sdef` line for the `message` class `id`
property, vs `uniqueID` on `outgoing message`, `Mail.sdef:290-293`) that is only
meaningful once the object has become a real, stored Drafts-mailbox message.

**Why Exchange re-sync reassigns it.** This is a store-assigned id, not a
client-side handle, and Exchange accounts resync/renumber the Drafts folder
independently of the plugin. Two pieces of direct evidence in this repo:

1. `tasks/active/native-reply/native-reply-probes-2026-06-30.md:266-273` (live
   validation on account "TU - Cayman", source id 86695): *"`id of replyMessage` is
   the wrong id, and on Exchange the saved id is unstable. `id of r` returned the
   small session-scoped outgoing id (`32`), not the saved Drafts id. The enumerated
   Drafts id right after save was `86714`, but after Exchange sync (~seconds) it
   was reassigned to `86715`. So do not rely on an exact draft id captured at save
   time on the window path."*
2. The AGENTIC-1214 field report's own observation (repeated `manage_drafts
   action="list"` calls returning `103 -> 91058 -> 91061` with zero writes between
   calls) shows the instability is not limited to save-time capture — the id can
   drift on **pure re-list**, i.e. Exchange resyncs the Drafts folder's numbering
   independently of any client action. This is a stronger and more general failure
   mode than the one already documented in native-reply-probes.md, and is not yet
   captured anywhere else in the repo's task history (`grep -rln "unstable"
   tasks/ docs/` finds only the native-reply-probes reference above and two
   unrelated `2026-05` whose-elimination notes).

**Stable keys that exist today.**

- `manage_drafts(action="find", in_reply_to=<source internet Message-ID>)`
  (`drafts_scripts.py:132-190`) is the one deterministic, non-numeric lookup the
  plugin ships. It scans a bounded newest-first Drafts window
  (`drafts_scripts.py:155-162`), reads each candidate's `In-Reply-To`/`References`
  via `thread_headers_block` (`drafts_scripts.py:142-147`,
  `applescript_snippets.py:61-103`), and does a `contains` match against the
  caller-supplied `in_reply_to` (`drafts_scripts.py:174`). This only works for
  drafts that already have real threading headers, i.e. drafts created by
  `reply_to_email`'s native `reply` path — never for `manage_drafts(action="create")`
  drafts, which (per §1/§3) have no headers to match against.
- **Subject + recipient** is available but not exact: `manage_drafts(action="list")`
  returns subject, id, To, and a body snippet per draft
  (`drafts_scripts.py:111-116`), and `verify_draft`/`verify_drafts` accept
  `expected_subject`/`expected_to`/`expected_cc` as match filters
  (`verify_tools.py:85-86`, consumed in `draft_verification.py:109-120`). Subjects
  are not unique (the exact hazard the id-first refactor was designed against; see
  `tasks/archive/2026-06/issues/issue-find-draft-by-in-reply-to-2026-06-24.md:41-43`).
- **Internet Message-ID of the draft itself** (as opposed to the source message's
  Message-ID that `in_reply_to`/`references` point at) is *not* currently surfaced
  anywhere. The Mail dictionary does expose it once a draft is saved and becomes a
  `message` instance (`Mail.sdef:593`, `message id` / `scriptedMessageIDHeader`,
  read-only), but no snippet builder or tool in this repo reads it —
  `applescript_snippets.py` has no `message id` extraction, and neither
  `verify_draft`'s payload (`draft_verification.py:148-168`) nor
  `manage_drafts(action="list"/"find")`'s output includes it. This is a real gap: a
  draft's own Message-ID would be a stable, store-independent key (unlike the
  numeric `id`), and it is one AppleScript read away from being wired up the same
  way `in_reply_to`/`references` already are.

**What `verify_draft` and `manage_drafts list` return today that could serve as a
key.**

- `verify_draft` payload (`draft_verification.py:148-172`): `draft_id` (the
  unstable numeric id, echoed back from the input, not re-derived),
  `recipients.{to,cc,bcc}`, `subject`, `body_preview`, `threading.{in_reply_to,
  references}` (source-message linkage, not the draft's own id),
  `quoted_original.detected`, `signature.detected_above_quote`,
  `attachments.found`, `checks`, `warnings`. None of these is a store-stable
  identifier for the *draft itself* except by re-deriving a `(subject, to,
  in_reply_to)` composite key from the fields already present — there is no single
  field meant for that purpose.
- `manage_drafts(action="list")` output (`drafts_scripts.py:46-129`, plain text,
  not JSON): per-draft `✉ <subject>`, `Id: <numeric>`, `To: <recipients>`,
  `Created: <date received>`, and a 140-char body snippet. Same limitation: `Id` is
  the unstable numeric handle; everything else is a soft match, not a key.
- `manage_drafts(action="find")` output (`drafts_scripts.py:148-189`): per-match
  `✉ <subject>`, `Id: <numeric>`, `In-Reply-To: <value>`, `References: <value>`.
  This is the closest thing to a stable key today (the In-Reply-To value itself,
  not the numeric Id), but it is a re-derivable lookup, not a field returned as an
  identity token you can round-trip.

## 5. What `manage_drafts(action="create")` returns today, and where a warnings field would fit

**Exact output today.** `manage.py:189-226` builds a single plain-text (not JSON)
AppleScript-composed string, `manage.py:415-425` returns it (or `"Error: ..."` on
timeout) with no structured envelope at all. On success the format is fixed lines:

```
CREATING DRAFT

✓ Draft created successfully!

Subject: <escaped_subject>
To: <to>
Draft ID: <draftId>
```

(built at `manage.py:191, 215-218`; `Draft ID:` line is conditional on
`draftId is not ""`, `manage.py:218`). On AppleScript-level failure it is `"Error:
" & errMsg` (`manage.py:220-222`, wrapped in Mail's own `try`/`on error`), and on a
Python-level AppleScript timeout it is the f-string at `manage.py:420-424`. There
is no `warnings` field, no `threading` block, no JSON at all — contrast with
`verify_draft`'s structured JSON (`draft_verification.py:148-172`) and with
`reply_to_email(output_format="json")`'s documented contract in
`plugin/apple_mail_mcp/tools/CLAUDE.md` ("JSON `output_format`" section), which
`manage_drafts` never adopted.

**Where a warnings field / structured refusal would fit.** Two natural insertion
points, both requiring a Python-level check before the AppleScript path (i.e. no
new AppleScript, pure guard logic in `manage.py`'s existing `create` branch):

1. **Immediately after the existing `standalone_confirmed` guard**
   (`manage.py:140-142`), before the sender-override validation
   (`manage.py:144-152`): if `in_reply_to` is supplied at all on `action="create"`,
   this is the point to either (a) refuse outright with a structured
   `code: "CREATE_CANNOT_THREAD"` error naming `manage_drafts` (not
   `compose_email`) and pointing at `reply_to_email(message_id=...)`, mirroring the
   accurate, tool-correct phrasing that `_standalone_compose_thread_warning`
   currently gets wrong (§2), or (b) accept it but immediately build a `warnings:
   ["in_reply_to_ignored_on_create"]`-style note that gets threaded into the
   success text.
2. **In the success-text assembly** (`manage.py:215-218`), right where `Draft ID:`
   is conditionally appended: this is the existing pattern for appending optional
   informational lines to the plain-text output, so a warning line (or, if the
   tool is upgraded to return JSON, a `warnings` array key alongside `draft_id`)
   would slot in next to the existing `Draft ID:` conditional rather than requiring
   a new response shape. Doing this well would also mean deciding whether
   `manage_drafts(action="create")` should gain the same JSON `output_format`
   contract `reply_to_email` already has (per `tools/CLAUDE.md`'s "JSON
   `output_format`" table), since a `warnings` array is far more useful as a real
   JSON field than as another line of free text a caller has to string-match.

## 6. Skills and docs referencing `manage_drafts(action="create")` as a reply workaround

None of the shipped guidance recommends it as a workaround — every reference found
explicitly **forbids** it for thread replies (consistent with §3's feasibility
verdict). Full grep results:

**`docs/`** (`grep -rn "manage_drafts" docs/`):

- `docs/CLAUDE-conventions.md:149`: *"Do not use standalone draft creators
  (`compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")`)
  to answer existing mail: they create standalone messages with no quoted original
  thread."*
- `docs/findings-allow-full-scan-audit-2026-06-09.md`,
  `docs/live-testing-reports/LIVE_FIELD_REPORT_2026-06-04.md`: incidental mentions
  in scan-hardening / live-report context, not reply guidance.

**`plugin/skills/`** (`grep -rn "manage_drafts" plugin/skills/`, deduped by
distinct guidance text — the same reference files are symlinked/copied under
`email-drafting/`, `inbox-triage/`, `apple-mail-operator/`,
`email-archive-cleanup/`, `email-management/`, and the shared `references/`
folder):

- `plugin/skills/*/references/pre-draft-verification.md:32` (all five copies):
  *"Never use `compose_email`, `create_rich_email_draft`, or
  `manage_drafts(action="create")` for thread replies."*
- `plugin/skills/email-drafting/SKILL.md:26`: *"Never use standalone draft creators
  (`compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")`)
  to answer an existing message. They create standalone new messages, so the
  original chain is not included."*
- `plugin/skills/email-drafting/SKILL.md:55`: documents that `create` (along with
  `compose_email`/`create_rich_email_draft`) refuses `Re:`/`Fwd:` subjects or
  quoted-thread bodies and that `standalone_confirmed=True` exists only "for a
  truly new message whose subject happens to look threaded" — *"never use this
  override to substitute for `reply_to_email` / `forward_email`."*
- `plugin/skills/email-drafting/SKILL.md:71`: describes `manage_drafts`'s
  `action="create"` as *"Standalone `action="create"` only"* in the low-level tool
  table.
- `plugin/skills/apple-mail-operator/SKILL.md:79,99`: repeats the same prohibition
  in the "Reply to a known message" and "never substitute" guidance rows.
- `plugin/skills/email-management/SKILL.md:140`: same prohibition inside a triage
  workflow step.

**No skill or doc anywhere frames `manage_drafts(action="create")` +
`in_reply_to` as a supported or recommended way to produce a threaded reply.** The
AGENTIC-1214 reporter's attempt to use it that way was going against every piece of
shipped guidance; the bug is that the tool *accepted* the parameter and *looked*
like it might have worked (guard didn't explain why, output didn't warn, and
`verify_draft` only revealed the failure after the fact via an empty `threading`
block) rather than refusing clearly up front.

## Summary table

| Question | Verdict |
|---|---|
| Q1: `in_reply_to` on create | Silently accepted by the signature, never read inside the `create` branch (`manage.py:136-226`); no validation, no AppleScript use, no output mention. |
| Q2: reply-like guard | Checks only `subject`/body text for `Re:`/`Fwd:`/quote markers (`payload.py:117-145`); `standalone_confirmed` is a blanket bypass; refusal text hardcodes "compose_email" even when triggered from `manage_drafts` and never mentions `in_reply_to` or create's threading limitation. |
| Q3: feasibility of setting In-Reply-To/References | **Impossible** through the `outgoing message` class (`Mail.sdef:269-303`; no header element, no such property) — **possible only via Mail's own `reply`/`forward` commands**, which the plugin already uses for `reply_to_email` and which is not something `manage_drafts(action="create")`'s `make new outgoing message` path can reach. |
| Q4: draft id lifecycle | Numeric id is a store-assigned handle (`uniqueID` on `outgoing message`, `libraryID` on `message`) captured post-save; Exchange resync reassigns it, confirmed both by a live probe (`native-reply-probes-2026-06-30.md:266-273`) and by the field report's list-only drift. The only shipped stable key is `manage_drafts(action="find", in_reply_to=...)`, which only works for drafts that already carry real threading headers (i.e. never for `create`-made drafts). The draft's own Message-ID is readable in principle (`Mail.sdef:593`) but not currently surfaced by any tool. |
| Q5: current create output / warnings fit | Plain-text success/error string only (`manage.py:189-226`, no JSON, no `warnings` key); natural insertion points are right after the standalone guard (`manage.py:140-142`) for a structured refusal, and in the success-text assembly (`manage.py:215-218`) for an informational warning line or JSON field. |
| Q6: docs/skills recommending create-as-reply-workaround | None found; every reference explicitly forbids it (`docs/CLAUDE-conventions.md:149`, `plugin/skills/email-drafting/SKILL.md:26,55,71`, `plugin/skills/*/references/pre-draft-verification.md:32`, `plugin/skills/apple-mail-operator/SKILL.md:79,99`, `plugin/skills/email-management/SKILL.md:140`). |
