# Public contract and integration review

Reviewer: MCP API and packaging pass

Scope: `origin/main...HEAD`, concentrating on public tool contracts, error
semantics, output shape, manifests, and documentation.

## Findings

### HIGH | Native reply verification cannot be exact and automatic retry is unreachable

Location: `plugin/apple_mail_mcp/tools/compose/reply_scripts.py:528-541`
and `plugin/apple_mail_mcp/tools/compose/reply.py:404-458`.

The native builder omits `Draft ID:`, even though the revised caller extracts
that field and requires it for exact-ID verification and retry. Native replies
therefore fall back to a same-subject Drafts scan, which can match an older
same-subject draft rather than the new artifact.

Fix: capture and emit the native reply ID after save, then prove the exact-ID
path with a generator-level regression test.

### MEDIUM | Quote scoping treats authored text containing `wrote:` as quoted text

Location: `plugin/apple_mail_mcp/tools/draft_verification.py:49-67`.

`verify_draft(expected_body_contains=...)` chooses the first bare `wrote:` as
the quote boundary. For `As Keynes wrote: prices will adjust. On Tue, Ann
wrote: original`, checking for `prices will adjust` returns
`expected_body_only_in_quote`, even though the text is in the authored reply.

Fix: recognize a conservative attribution boundary before newline flattening,
or retain whole-body behavior when a reliable boundary is unavailable. Add the
example above as a regression test.

## Verified non-findings

The 41-tool registration, v3.11.1 version surfaces, manifest validation, and
distribution artifact parity are consistent.
