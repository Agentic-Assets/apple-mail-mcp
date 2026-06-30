# Native reply (logo + quote bar) — session handoff (2026-06-30)

**Branch:** `codex/pr38-guidance-verifier-followup` (uncommitted work in tree).
**Goal:** `reply_to_email` should produce Apple Mail's NATIVE reply format — reply
body on top, the account's default signature WITH logo, and the colored quote bar —
instead of a flattened plain-text "Re:" draft. User: "the native version looks a lot
better; don't reinvent the wheel."

Companion logs: [`native-reply-probes-2026-06-30.md`](native-reply-probes-2026-06-30.md)
(reusable test code + every live finding) and
[`native-reply-formatting-investigation-2026-06-30.md`](native-reply-formatting-investigation-2026-06-30.md).

## DONE (implemented + live-validated on the real "Test" email, id 86695, acct "TU - Cayman")

All in `plugin/apple_mail_mcp/tools/compose.py`:

- **`native_format: bool = True`** param on `reply_to_email` (last positional kw).
- **Two builders:**
  - `_build_reply_native_window_applescript` (NEW): `reply ... with opening window` →
    AXRaise-guarded **typed keystroke** of the body (never clipboard) → `save` →
    close window (draft) / leave open (open) / `send` (send). Body read from temp file
    via `cat`. Emits `Quote Needle: wrote:` for verification.
  - `_build_reply_objectmodel_applescript` (RENAMED from `_build_native_reply_applescript`):
    the old `set content` flatten path, now the `native_format=False` headless/bulk
    branch (no window, no Accessibility needed).
- **Focus guard:** Mail's dictionary `name of front window` is authoritative and must
  equal the reply subject; an EMPTY System Events title is tolerated (AX quirk for
  compose windows); a different non-empty SE title aborts. Retries 4×. On failure
  returns structured `REPLY_WINDOW_FOCUS_FAILED` (suggests retry or `native_format=False`).
- **Sender NOT pinned in native path** — only set on explicit `from_address`. Pinning
  the single alias made Mail re-insert a text-only signature and drop the logo from the
  saved source. Object-model path keeps the pinning.
- **Signature verification skipped** for native default (`include_signature` + no
  `signature_name`) — Mail's own logo signature can't be substring-matched, so it was
  falsely warning "missing". Explicit names + object-model path still verify.
- **Verifier is now line-break-insensitive** (`stripLineBreaks` in
  `_verify_saved_reply_draft`) — fixes a false `BODY_MISSING` when a long first line
  soft-wraps (Mail breaks it mid-word in `content as string`).

Live-confirmed at the SOURCE level: native draft = body-on-top + logo signature
(`multipart/related` + `image/` cid before the blockquote) + colored quote bar,
correctly ordered, exactly one draft (no shell dupes), window auto-closed in draft
mode / left open in open mode. `native_format=False` produces a valid flattened draft.

## NOT our bug (confirmed, documented — do NOT chase)

- **Logo not repainted in the reopened draft EDITOR.** Saved draft stores the logo
  (and the Drafts list preview shows it), but double-clicking the draft opens a
  compose editor that doesn't repaint the inline signature image. Cayman reproduced
  this with a fully MANUAL draft → it's native Mail behavior, no dictionary-level fix.
  A fresh `mode="open"` window (never saved+reopened) renders the logo fine.

## PENDING (next session)

1. **Tests (`tests/test_compose_tools.py`) — ~12 failing, all expected.** They assert
   the OLD `set content` default. Re-point:
   - Flatten-path assertions (`set content of replyMessage`, `reply foundMessage with
     reply to all` without window, single-alias sender) → call with `native_format=False`.
   - Default-path tests (`test_reply_defaults_to_draft_mode`,
     `test_reply_uses_native_mail_reply_and_preserves_native_quote_by_default`,
     `test_reply_to_all_uses_native_mail_reply_all`,
     `test_reply_signature_is_applied_before_body_insert`,
     `test_reply_signature_verification_runs_when_signature_requested_without_resolved_name`,
     `test_default_emits_single_alias_fallback_for_reply_message`,
     `test_default_signature_applies_to_reply_via_mail_signature_property`,
     `test_empty_reply_body_keeps_body_assignment_guarded`,
     `test_include_signature_false_*`, `test_reply_without_all_uses_native_plain_reply`,
     `test_reply_all_with_attachment_preserves_single_body_and_verifies_exact_draft`) →
     update to the native keystroke contract.
   - ADD native-path regression tests: `with opening window` present, `keystroke`
     present, NO `set content`, AXRaise guard + empty-SE-title tolerance, `wrote:`
     needle, sender NOT set when no `from_address`, signature-verify skipped for native
     default, `stripLineBreaks` in the verifier, `GUARD_ABORT` → `REPLY_WINDOW_FOCUS_FAILED`.
   - Verify full suite: `PYTEST_ADDOPTS='' .venv/bin/pytest tests` (was 958 collected).
2. **Ship gates (Task #5):** `code-simplifier:code-simplifier` on the new code;
   `plugin-dev:plugin-validator` + `plugin-dev:skill-reviewer`; `bash tools/dev-check.sh
   release` (rebuilds the stale `apple-mail-plugin.zip` / `.mcpb` — the PostToolUse hook
   flags them stale on every edit, resolved here); then `finalize-apple-mail-mcp`.
3. **Docs/manifests:** mention `native_format` + the Accessibility requirement in
   `plugin/apple_mail_mcp/tools/CLAUDE.md`, `docs/CLAUDE-conventions.md`, and the
   `reply_to_email` description in `apple-mail-mcpb/manifest.json` (currently says it
   "constructs and assigns reply_body above the quoted-original block" — that's the old
   set-content path).
4. **Commit** only when Cayman asks. Never push to main.

## TO TEST / VERIFY (open questions, need live runs)

- **Does the logo survive an actual SEND of a saved draft?** Source contains it, so it
  should. Send a test draft to self, check the RECEIVED mail. (Cannot auto-send.)
- **Attachments + native reply:** compiles, NOT live-tested. The attachment appends to
  `content` via the dictionary BEFORE the keystroke; confirm it doesn't disturb the
  rich quote/logo. (Rare combo.)
- **`reply_to_all` native:** compiles, not live-tested on a real multi-recipient email.
- **GUARD_ABORT under real focus contention** (only the happy path + empty-SE-title case
  were exercised).
- **Accessibility permission**: native path needs the host process to have Accessibility
  (System Events keystroke). Document prominently for other installs; `native_format=False`
  avoids it.

## SEPARATE, UNRELATED bug noticed (log for later)

`apple-mail drafts list --account "TU - Cayman"` reports `count=0` while AppleScript
sees 208 drafts — a `manage_drafts` scoping quirk. Not part of this workstream.

## How to resume / test quickly

- Editable source runs live: `.venv/bin/python -c "from apple_mail_mcp.tools.compose
  import reply_to_email; print(reply_to_email(account='TU - Cayman', message_id='86695',
  reply_body='...', mode='draft'))"` (there is no `apple-mail reply` CLI subcommand).
- Find a fresh source id + reusable probes: see `native-reply-probes-2026-06-30.md`.
- Identify test drafts by a unique sentinel in the body AND report the saved id; delete
  by `delete (item 1 of (every message of dm whose id is N))` with N coerced to integer,
  ~8s settle (Exchange deletion lag).
- Memory: [[compose-must-use-object-model]], [[tahoe-outgoing-messages-empty]].
