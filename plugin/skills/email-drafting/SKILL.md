---
name: email-drafting
description: This skill should be used when the user asks to "draft an email", "reply to this message", "forward this thread", "verify my draft", or needs compose workflows in Apple Mail with draft-safe defaults. Covers compose_email, reply_to_email, forward_email, create_rich_email_draft, manage_drafts, verify_draft, and verify_drafts with exact message ids for replies, standalone-draft guardrails, native reply defaults, signatures, and post-draft verification. Do NOT use for inbox triage (inbox-triage), Mail MCP setup (apple-mail-operator), folder taxonomy (mailbox-taxonomy), Mail rules (mail-rules-advisor), staged bulk moves (email-archive-cleanup), or attachment extraction (email-attachments).
---

# Email Drafting

Compose-first workflows against Apple Mail. Default plugin installs run **`--draft-safe`**: compose tools default to quiet `mode="draft"` (no leftover compose windows), and `mode="send"` returns a structured error until the server is reconfigured. When send is allowed, still confirm intent with the user before calling `mode="send"` or `manage_drafts(action="send")`.

## Recent-first drafting (required when batching replies)

See [`recent-first-triage.md`](references/recent-first-triage.md). When the user asks to draft responses to recent mail, work **newest inbound first** in batches of **3 to 5**. Draft and verify **one thread at a time** before moving to the next message. Do not batch-draft month-old threads discovered by wide `date_from` or `sender_domain` sweeps while newer inbox mail is still unreviewed.

## Native drafting only (binding rule)

**Always use the native drafting method for replies. Never use the windowless fallback.** `reply_to_email` defaults to `native_format=True`, and that is the only method to use. It composes in Mail's native reply window so drafts keep the colored quote bar and the account's default logo signature, which is the formatting the user requires; the body is typed in small focus-guarded chunks (never one keystroke of the whole body). The windowless `native_format=False` path is gated: it returns `WINDOWLESS_FALLBACK_DISABLED` unless `allow_windowless_fallback=True` is explicitly passed, and agents must never pass that flag. After saving a native draft/open reply, use a retryable Draft ID only when the plugin emits a complete persisted-Drafts identity: one bounded before/after snapshot gained exactly one new RFC `Message-ID`, and that message's `In-Reply-To` exactly links to the source. Draft mode resolves this before closing its window; open mode leaves the saved window available for review. The full-body verifier and any automatic delete-and-retype revalidate the same headers at that numeric ID. A cap limit, indexing delay, ambiguity, malformed headers, or ID drift disables automatic cleanup. A bounded newest-Drafts fallback is diagnostic only and can never trigger deletion or retyping. If the call returns `REPLY_BODY_MISMATCH`, inspect the named artifact with `verify_draft(draft_id=...)` before manual cleanup. If a native reply fails with `REPLY_WINDOW_FOCUS_FAILED` (window never took focus), `REPLY_SUBJECT_GUARD_MISMATCH` (a compose window opened but its title never matched), or `REPLY_BODY_TYPING_INTERRUPTED` (focus was lost partway through typing; the partial compose window was already discarded, nothing left behind), retry with Mail visible and not being clicked; do not switch off native formatting. **Scheduled intake batches:** if focus still cannot be acquired on one message, log `draft_deferred`, continue other courtesy drafts, and report the blocker. **Interactive sessions:** stop and report the blocker. Never use `native_format=false` in either case.

## Pre-draft verification (required before replying)

Canonical rules: [`pre-draft-verification.md`](references/pre-draft-verification.md). Summary:

1. Check the discovery row first: `was_replied_to` and `has_draft` are always present on every read/triage discovery row (`search_emails`, `list_inbox_emails`, `get_email_by_id`, `get_email_by_ids`, `get_email_thread`, `get_needs_response`, `inbox_dashboard`, and `get_inbox_overview`). **Abort the draft** when `has_draft=true` (a matching draft already exists) or when `was_replied_to=true` with no matching draft, unless the user explicitly said "redraft", "include already-replied", or "include drafted".
2. Fallback (row fields absent, or `has_draft=null`): fetch the conversation with `get_email_thread(account=..., message_id=...)`. If no id is known, run bounded `search_emails` or `list_inbox_emails` first, then fetch by returned `message_id`.
3. Cross-check senders in the thread against `list_account_addresses(account=...)`. If any message in the thread was sent by one of the user's own addresses, **abort the draft** and report which message already replied (date, subject snippet), unless the user explicitly said "redraft" or "include already-replied".
4. Proceed to `reply_to_email(message_id=...)` only after the row/thread check shows no user-sent message and no matching draft.

Never use standalone draft creators (`compose_email`, `create_rich_email_draft`, or `manage_drafts(action="create")`) to answer an existing message. They create standalone new messages, so the original chain is not included. If a standalone draft has a `Re:` / `Fwd:` subject or quoted-thread body, the tool returns an error unless `standalone_confirmed=True`; use that override only for a truly new message whose subject happens to look threaded.

## When To Use This Skill

| Request signal | Use this skill |
|----------------|----------------|
| "Reply / forward / write / draft" | Yes |
| "Make a nicer HTML newsletter-style draft" | Yes |
| Manage open drafts (`list/send/delete`) with guardrails | Yes |
| Bulk archive or reorganize folders | No → `email-archive-cleanup` |
| Decide folder strategy | No → `mailbox-taxonomy` |

## Preconditions

1. Know the **`account`** (defaults follow `DEFAULT_MAIL_ACCOUNT`) and signature intent. Compose/reply/forward default to **`include_signature=True`**, which applies **`DEFAULT_MAIL_SIGNATURE`** when that env var is set; when unset, the tool does not force a named signature and Mail may still apply the account's normal default signature. Pass `include_signature=False` to suppress plugin-applied signatures, or `signature_name` to override the default for one call. For replies, disabling signatures cannot skip `reply_body` insertion. On native replies (`native_format=True`) Mail supplies its own default reply signature with the logo preserved; `signature_name` and `include_signature=False` still override it, whereas the windowless `native_format=False` path yields a flattened text signature only.
2. Confirm the `mcp__apple-mail__*` tools are actually registered before any drafting call. If they are absent, fix MCP registration or use the documented MCP-only absolute-path fallback; do not draft with generic AppleScript, Mail UI scripting, shell `osascript`, or a standalone compose fallback.
3. For replies/forwards, use the Mail **`message_id`** returned by `search_emails`, `get_email_by_id`, or thread tools whenever available. **`list_inbox_emails` JSON always includes `message_id`**, so use it directly for `reply_to_email`. Do not pass `subject_keyword` to action tools just because the subject is visible. **Discovery-only:** pass `subject_keyword` to `search_emails`, then pass the returned `message_id` to `reply_to_email` / `forward_email`. Use `list_inbox_emails` for bounded recent listing when no subject filter is needed.
4. Reply drafting requires `reply_to_email(message_id=...)`. If no `message_id` is known, run `search_emails` or `list_inbox_emails` first; `subject_keyword` on `reply_to_email` returns `TARGET_SELECTOR_DEPRECATED`.
5. Load **`USER_EMAIL_PREFERENCES`** plus any capture from **`email-style-profile`** before writing content.

## Pre-call Checklist (every mutate call)

Restate these in chat **before** invoking `compose_email`, `reply_to_email`, `forward_email`, `create_rich_email_draft`, or `manage_drafts(action="send")`:

1. **Recipients**: explicit `to`, `cc`, `bcc`. Confirm spelling and that no replies stay private.
2. **Subject line**: exact text. For replies/forwards, confirm the inherited subject if Mail will prepend `Re:` / `Fwd:`.
3. **Mode**: `draft` (quiet save, default) vs `open` (saved + window stays open for review) vs `send` (blocked under `--draft-safe`). `mode="send"` requires explicit user confirmation and a non-draft-safe configuration. For `reply_to_email`, the full-body post-save verification (and its automatic retype-on-mismatch) only runs for `mode="draft"` / `mode="open"`; a sent reply leaves no Drafts artifact to verify afterward, so prefer `draft` first and confirm the saved body before switching to `send`.
4. **Signature intent**: `include_signature=True` applies `DEFAULT_MAIL_SIGNATURE` if set; when unset, Mail may still apply the account's normal default signature. Pass `signature_name` to override, `include_signature=False` to suppress plugin-applied signatures. For replies, disabling signatures cannot skip body insertion above the quoted original. `create_rich_email_draft` does not accept signature params; switch to a plain compose tool when a named signature is required.
5. **Source message id** (replies/forwards only): pass the `message_id` returned by search/list. If no id is known yet, run bounded `search_emails` or `list_inbox_emails` first; never pass `subject_keyword` to `reply_to_email` or `forward_email`.
6. **Standalone-confirmed override:** `compose_email`, `create_rich_email_draft`, and `manage_drafts(action="create")` refuse `Re:`/`Fwd:` subjects or bodies containing quoted-thread markers and return a structured error. If the user explicitly wants a fresh standalone message that happens to look threaded (e.g. a new "Re: weekly review" note unrelated to any prior thread), pass `standalone_confirmed=True` (CLI: `--standalone-confirmed`); never use this override to substitute for `reply_to_email` / `forward_email`.

**Large-inbox caveat:** the pre-draft `get_email_thread` verification can stall on long threads in a 24k mailbox. If no `message_id` is known, run bounded `search_emails` or `list_inbox_emails` first, then fetch by returned `message_id` (`get_email_thread(account=..., message_id=...)` or `get_email_by_id(account=..., message_id=...)`).

`mode="open"` saves first then leaves the compose window open, so closing it should not trigger Mail's Save/Don't Save prompt.

**Native reply focus:** the default native path (`native_format=True`) is the only supported reply path for normal agent use. It types `reply_body` into Mail's reply window in small focus-guarded chunks, so Mail must be able to take and keep focus and the host process needs Accessibility permission. If `reply_to_email` returns `REPLY_WINDOW_FOCUS_FAILED`, `REPLY_SUBJECT_GUARD_MISMATCH`, or `REPLY_BODY_TYPING_INTERRUPTED`, no draft with a partial body was saved and nothing was sent: retry with Mail visible and not being clicked. Do not switch to `native_format=False`. **Scheduled intake:** log `draft_deferred` and continue other drafts. **Interactive:** stop and report the blocker.

## Tool Selection Pattern

| Situation | Tool | Notes |
|-----------|------|-------|
| New outbound mail | `compose_email` | Standalone only; default `mode="draft"`; use `mode="open"` only for explicit saved-open review; `mode="send"` blocked under `--draft-safe` |
| Structured reply context | `reply_to_email` | Default quiet draft (`send=False` / `mode="draft"`); pass `message_id=...` from search/list. **Discovery-only:** use `subject_keyword` on `search_emails` only, never on `reply_to_email` (`TARGET_SELECTOR_DEPRECATED`). `native_format=True` is the only supported path (rich quote bar + logo signature, body typed in small focus-guarded chunks, needs Mail focus + Accessibility); `native_format=False` returns `WINDOWLESS_FALLBACK_DISABLED` unless `allow_windowless_fallback=True` is explicitly passed (deliberate headless/CI only, never set by agents). After save, native replies compare the FULL `reply_body` above the quoted original case-sensitively. Automatic delete-and-retype requires a persisted Drafts identity proven by a bounded before/after snapshot plus exact RFC `Message-ID` and `In-Reply-To` linkage, then revalidated before deletion. A bounded fallback can report a suspect Drafts artifact but never deletes it. |
| Share thread outward | `forward_email` | Default `mode="draft"`; pass `message_id=...` from search/list. **Discovery-only:** use `subject_keyword` on `search_emails` only, never on `forward_email` (`TARGET_SELECTOR_DEPRECATED`) |
| Marketing / HTML layout | `create_rich_email_draft` | Standalone only; produces multipart `.eml`, saves to Drafts by default; use `review_in_mail=True` for saved-open review; no Mail signature params. Use plain compose tools when a named signature is required |
| Low-level draft listing / CRUD | `manage_drafts` | Standalone `action="create"` only; respect cap defaults; never batch-delete without confirming folder scope. `action="list"` returns each draft's Id, To, and a body snippet (triage without re-fetching), reads **newest drafts first**, accepts `limit=...`, and accepts `subject_contains="..."` (case-insensitive "find the draft I just made"). `action="find"` locates reply drafts by bounded In-Reply-To / References header scan and is the durable handle for a reply draft (`in_reply_to` is honored only by `find`; passing it to `action="create"` returns `CREATE_CANNOT_THREAD` before any AppleScript runs, since create has no header property to set In-Reply-To/References). For `send`, `open`, or `delete`, prefer exact `draft_id` from the list output over `draft_subject`, and on Exchange/server accounts re-resolve that `draft_id` with a fresh `list`/`find` call immediately before acting, since it can drift across sync even between two `list` calls with no writes in between |
| Exact draft readiness check | `verify_draft` / `verify_drafts` | Read-only JSON snapshot for one or more Drafts ids: recipients, body sentinel, attachments, signature state, quoted original, and thread headers. Pass `resolve_source=True` to also resolve the draft's `In-Reply-To` header back to the source Inbox message's `message_id`, subject, and sender (bounded, see "Missed-replies queue" below); defaults to `False` (no behavior change) |
| Remove orphaned blank drafts | `manage_drafts(action="cleanup_empty")` | Deletes drafts with blank subject AND empty body; `dry_run=True` by default (preview first), capped by `max_deletes` (default 20). Confirm the preview count with the user before `dry_run=False` |

## Safety And Compliance

| Risk | Mitigation |
|------|-------------|
| Accidental dispatch | Maintain `--draft-safe`; disallow `mode="send"` silently |
| Over-broad lookups | Prefer `message_id` from search/list; when id is unknown, run bounded `search_emails` (narrow `recent_days`, `subject_keyword` on `search_emails` only) or `list_inbox_emails` for recent listing, then pass returned ids to action tools |
| Sensitive content | Warn before quoting full threads into new messages |
| Signature alignment | Prefer matching recent Sent-tone via `email-style-profile` routines |

### Draft-Safe And Read-Only Modes Reminder

- **`--draft-safe`** (default plugin install): `compose_email`, `reply_to_email`, and `forward_email` stay on quiet `mode="draft"` unless the user explicitly requests `mode="open"`; `mode="send"` and `manage_drafts(action="send")` return structured errors; treat send requests as drafting tasks until configuration changes.
- **`--read-only`**: Unregisters `compose_email`, `reply_to_email`, and `forward_email`; also enables draft-safe send blocking for `manage_drafts(action="send")`. `create_rich_email_draft` and `manage_drafts` remain for draft workflows where permitted.

## Rich Draft Guidance

Choose `create_rich_email_draft` when plain-text AppleScript insertion would show escaped HTML artifacts. With a nonblank subject and default `open_in_mail=True`, it writes the `.eml`, opens Mail only long enough to save the draft, then closes the fresh compose window. Blank subject → `.eml` only (Mail not opened). Use `open_in_mail=False` when the caller only needs the `.eml` artifact, and use `review_in_mail=True` only when the user explicitly wants Mail left open after the draft has been saved.

## Post-Draft Verification

Summarize artifacts for the operator:

1. Mailbox + identifiers (`message_id` if surfaced).
2. Draft location and whether Mail was left open for explicit review.
3. For replies, whether `reply_body` was verified above the quoted original.
4. Next actions (edit, attachments, approvals).

To verify a freshly-created draft, do **not** use `search_emails`; it runs a
date-filtered scan that is slow on large accounts and silently drops brand-new
drafts (an unsent `outgoing message` has a null received date). Instead use the
exact Drafts verification: `verify_draft(draft_id="...", expected_body_contains="...")` or `verify_drafts(draft_ids=[...])`
or bounded Drafts lookup: `manage_drafts(action="list", subject_contains="...")`
(newest-first) or `get_email_by_id(account=..., message_id=..., mailbox="Drafts")`. Use
the returned exact `draft_id` for `manage_drafts(action="open"|"delete"|"send")`. Confirm
`to`/`cc` are the intended recipients and the body is present. For replies,
`reply_body` must appear above the quoted original; mere presence below the
quote is not enough. `verify_draft(expected_body_contains="...")` treats an
Apple Mail `On <date>, <sender> wrote:` attribution, an Outlook header block,
or an Outlook original-message separator as a quote boundary. An authored
phrase such as `As Keynes wrote:` is not a boundary. If no reliable boundary is
present, the tool checks the complete available body preview instead of rejecting authored
text before a bare `wrote:` phrase.

**Reply threading note:** `reply_to_email` defaults to `native_format=True`: it
opens Mail's native reply window so the saved draft keeps Mail's colored quote bar
and the account's default reply signature (logo included), and types `reply_body`
above the quoted original via System Events keystrokes sent in small focus-guarded
chunks, never one keystroke of the whole body (a single-keystroke pass silently
dropped the tail of long bodies and could leak shift state into ALL CAPS output).
This path needs the Mail reply window to take and keep focus and Accessibility
permission for the host process; if focus cannot be acquired before typing starts
it returns `REPLY_WINDOW_FOCUS_FAILED` or `REPLY_SUBJECT_GUARD_MISMATCH` without
saving, and if focus is lost partway through typing it returns
`REPLY_BODY_TYPING_INTERRUPTED`, discarding the partially typed compose window so
no partial draft is ever left behind. `native_format=False` is gated: it returns
`WINDOWLESS_FALLBACK_DISABLED` unless the caller explicitly passes
`allow_windowless_fallback=True`, and that path is reserved for deliberate
headless/CI runs only (agents must never set it). It is not a normal fallback.
Native draft/open replies expose a retryable Draft ID only after a complete,
bounded Drafts snapshot proves exactly one new persisted RFC `Message-ID` whose
`In-Reply-To` contains the source RFC token. Draft mode resolves that identity
before closing its window; open mode leaves the saved window available for
review. The verifier and delete path revalidate the draft's RFC `Message-ID`
and `In-Reply-To` at the numeric ID. A cap limit, indexing delay, malformed
headers, ambiguity, or numeric-ID drift exposes no retryable ID and never
permits automatic deletion. A bounded newest-Drafts lookup remains diagnostic
only. The native path compares the FULL saved body above the quote against the
requested `reply_body` (whitespace-flattened, smart-punctuation-folded, then
case-sensitive so an ALL-CAPS draft still fails). Otherwise it returns the
structured `REPLY_BODY_MISMATCH` error naming the suspect artifact id; a
placement failure after the quote or a not-found/timeout case keeps the
pre-existing `REPLY_DRAFT_BODY_AFTER_QUOTE` /
`REPLY_DRAFT_VERIFICATION_TIMEOUT` / `REPLY_DRAFT_VERIFICATION_ERROR` codes.
For machine-readable reply draft metadata, call `reply_to_email(..., output_format="json")` only with `mode="draft"` or `mode="open"`. The JSON payload includes `mode`, `sent`, `subject`, `draft_id`, `captured_draft_id`, `draft_id_source`, `verified_draft_id`, `exact_id_verified`, `verification_status`, `body_present`, `body_verified`, `retyped`, `stale_artifact_id`, `attachment_status`, `attachment_count`, `attachments_applied`, `signature_status`, and `mailbox`. `draft_id_source="persisted_header_identity"` and `exact_id_verified=true` mean the verifier matched the same header-linked persisted Drafts artifact. `verification_fallback` is non-destructive and must be checked manually. `retyped=true` means the automatic delete-and-retype retry ran only after identity revalidation. `stale_artifact_id`, when set, names an earlier draft whose deletion before retyping could not be confirmed, so check Drafts for a stray truncated/miscased duplicate. If verification times out or errors after a persisted identity was available, the error preserves the known `draft_id` / Drafts artifact id for exact cleanup. `output_format="json"` with effective `mode="send"` is rejected before Mail mutation; send mode is not draft-verifiable.
`body_html` on `reply_to_email` is accepted for compatibility but ignored; use
`create_rich_email_draft` / `compose_email` only for rich HTML on a confirmed
standalone message.

## Missed-replies queue (bounded, no Inbox-wide search)

Discovery rows now carry `has_draft` automatically, so most callers never need this workflow: check the row first, and only fall back here when `has_draft` is `null` (the draft scan was skipped or errored; check `draft_scan.status`) or absent from an older result already in hand. Use this workflow to find drafts that answer a message but were never sent, without an unbounded Inbox scan. It is a three-call, bounded path:

1. `manage_drafts(action="list", limit=25)`: a bounded, Drafts-only slice (newest first). Read each returned draft's `Id` (its `draft_id`) and body snippet to shortlist candidates; do not scan beyond `limit`.
2. `verify_draft(draft_id=..., resolve_source=True)`: use an exact-id snapshot for one shortlisted draft. It returns the usual recipients, body preview, attachments, signature state, and `threading.in_reply_to`, plus a new `source` object when `resolve_source=True`. `source` resolves the draft's `In-Reply-To` header back to the SOURCE Inbox message via one bounded `search_emails(internet_message_id=...)` call and returns the original message's numeric `message_id`, `subject`, and `sender`, avoiding a second manual lookup. `resolve_source` defaults to `False`, so existing callers see no behavior change unless they opt in.
3. If `source.resolved` is `false`, do not assume the source does not exist. Check `source.reason`: `no_in_reply_to_header` means this draft is not a reply (nothing to resolve); `not_found_in_window` means the source is likely older than the default 30-day lookback. Either widen the window a little (`resolve_recent_days=90`, still bounded, still a single search call, never unbounded) or fall back to `manage_drafts(action="find", in_reply_to=<threading.in_reply_to>)`, which scans the bounded Drafts window by header instead of the Inbox.

**Do not cache a `draft_id` across turns.** On Exchange and other server accounts, numeric Drafts ids are reassigned on sync, including between two `action="list"` calls with no writes in between. Re-resolve the id with a fresh `action="list"` or `action="find"` call immediately before `verify_draft`, `manage_drafts(action="open"|"send"|"delete")`, or `cleanup_empty`; `action="find"` with `in_reply_to` is the durable handle for a reply draft since it matches by header, not by store-assigned id.

**Never call `search_emails` directly to "find the original message" for a draft.** `resolve_source=True` already does that lookup the bounded way (`account` set, `mailbox="INBOX"`, capped `recent_days`, `max_results=1`); a manual `search_emails` call without `account` plus a bounded `recent_days` risks an unbounded, slow scan on large mailboxes and duplicates work `verify_draft` already did. Call Mail tools one at a time in this sequence; the server serializes Mail access, so do not fan out `manage_drafts` / `verify_draft` calls concurrently.

## Related Skills

- **`email-style-profile`**: learn voice from Sent mail samples.
- **`email-attachments`**: after drafting, optionally attach binaries with validated filesystem paths (`compose_*` attachments parameters).
- **`apple-mail-operator`**: if tools error on account scope or timeouts, fix infra before rewriting prose.
