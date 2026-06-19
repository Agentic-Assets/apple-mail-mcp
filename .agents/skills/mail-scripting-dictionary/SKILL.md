---
name: mail-scripting-dictionary
description: This development-only skill should be used when plugin maintainers change, review, or debug Apple Mail AppleScript in apple-mail-mcp or related macOS automation. It helps inspect Mail's local scripting dictionary, choose dictionary-backed commands and writable properties, avoid fragile UI scripting, validate reply/compose/draft behavior, and cite official Apple scripting sources. Do NOT expose this skill in the packaged plugin/skills bundle or use it for end-user email workflows.
---

# Mail Scripting Dictionary

This is a plugin-developer skill, not a user-facing Apple Mail workflow skill. Keep it in repo agent surfaces such as `.agents/skills` and `.claude/skills`; do not copy it into `plugin/skills` or list it in packaged plugin manifests.

Use Mail's scripting dictionary as the contract before changing Mail AppleScript. Treat prior examples, forum snippets, and remembered syntax as hypotheses until checked against the local `.sdef` and a compile or smoke test.

## Workflow

1. Start with the local dictionary:
   - Inspect `/System/Applications/Mail.app/Contents/Resources/Mail.sdef`.
   - Prefer `rg -n '<term>' /System/Applications/Mail.app/Contents/Resources/Mail.sdef -C 5`.
   - Use Script Editor's dictionary UI when a visual terminology view is helpful.
2. Verify the exact Mail class and command involved:
   - Confirm command parameters, result type, property type, and read/write access.
   - For `reply`, confirm it returns an `outgoing message` and that `opening window` and `reply to all` are optional booleans.
   - For draft body work, remember `outgoing message` has writable rich-text `content`, while saved/source `message` has read-only rich-text `content`.
3. Prefer app-dictionary automation over UI automation:
   - Use Mail commands and object properties first.
   - Avoid `System Events`, keystrokes, clipboard paste, and window focus unless the Mail dictionary cannot express the operation.
   - If UI scripting is unavoidable, isolate it, justify it, and add regression coverage that proves it is not used in safer paths.
4. Validate syntax and behavior separately:
   - Use `osacompile` for parse-level AppleScript checks when available.
   - Use small live smokes for Mail lifecycle behavior, especially Drafts persistence.
   - Clean smoke artifacts by exact Drafts message id, not subject.
5. Make failures structured and actionable:
   - If a draft is created but verification fails, return the artifact id and expected sentinel.
   - Verify the exact Drafts id surfaced after `save` before scanning newest Drafts.
   - For replies, require the constructed `reply_body` above the quoted-original block; body text below the quote is a failure.

## Mail Reply Notes

- `reply <message>` creates an `outgoing message`; draft mode can omit `opening window` because the dictionary default is false.
- `with opening window` is the affirmative AppleScript form. Do not assume `opening window false` compiles.
- `with reply to all` is the affirmative form for all-recipient replies.
- `message signature` on an `outgoing message` can be a `signature` or `missing value`; use `missing value` to disable signatures without skipping body insertion.
- Avoid reading `content of replyMessage` as a string before save in fragile reply flows. Construct the intended outgoing `content` from known source-message fields and assign it once.
- After saving a reply draft, capture `id of replyMessage` when Mail exposes it and verify that exact Drafts artifact first. Keep bounded newest-Drafts verification as fallback only.

## Source Map

Read [references/official-sources.md](references/official-sources.md) when you need official URLs, local dictionary landmarks, or a quick checklist for citing the Apple sources.
