# Native reply correctness review

Reviewer: runtime correctness pass

Scope: `origin/main...HEAD`, concentrating on the native reply builder,
chunked typing, saved-draft verification, and retry flow.

## Finding

### HIGH | The native reply template never returns the Drafts ID required for retry

Location: `plugin/apple_mail_mcp/tools/compose/reply_scripts.py:528`
and `plugin/apple_mail_mcp/tools/compose/reply.py:404`.

The native success output includes the subject and quote needle, but never
reads `id of replyMessage` or emits `Draft ID:`. Consequently `draft_id` is
always `None` for native replies. The retry guard also requires that returned
ID to equal the verifier's artifact ID, so the advertised delete-and-retype
path cannot execute.

The branch's live verification report records this production behavior:
`body_missing` with `retyped: false`, despite an identified artifact.

Fix: after saving the native reply, capture `id of replyMessage` before the
window is closed and include `Draft ID:` in the output. Add a generated-script
assertion and a runtime mock that derives its response from the generated
output rather than injecting a Draft ID unconditionally.

## Scope note

`mode="send"` remains unable to verify a saved draft before sending. That
behavior was present on the comparison base, so it is not recorded as a
branch-introduced finding here. It remains an important hardening follow-up.
