# Reply Draft Verification Hardening

Date: 2026-06-19

## Goal

Prevent `reply_to_email` from reporting success unless Mail saved a threaded reply draft whose constructed `reply_body` is assigned above the quoted-original block. Keep the implementation aligned with Mail's local scripting dictionary and avoid reply-body insertion through UI scripting, clipboard paste, or focus-sensitive windows.

## Priority Order

1. Verify saved reply drafts by exact artifact id when Mail exposes one after `save replyMessage`; keep bounded newest-Drafts as fallback only.
   - Status: implemented in `_verify_saved_reply_draft(draft_id=...)`.
2. Require body ordering, not just body presence: the body sentinel must appear before the quoted original marker/content.
   - Status: implemented through `replyBodyIsBeforeQuote(...)` and structured body-after-quote errors.
3. Extract and simplify the native reply AppleScript builder so reply options, body assignment, save behavior, and output fields are testable in isolation.
   - Status: implemented through `_reply_mode_plan`, `_reply_command_options`, `_reply_signature_script`, `_build_native_reply_applescript`, and `_reply_draft_verification_error`.
4. Make verification status structured for all verifier outcomes: found, body missing, not found, and AppleScript error.
   - Status: implemented in `_ReplyDraftVerification.status`.
5. Clean up tests so names match current behavior and regression cases cover exact-id/body-order/signature interactions.
   - Status: implemented in `tests/test_compose_tools.py`.
6. Isolate remaining UI-scripting exceptions outside the reply path and document why HTML compose still uses pasteboard/UI scripting.
   - Status: reply path has regression coverage against clipboard/System Events; HTML compose remains a documented exception because Mail's dictionary exposes deprecated/no-op HTML content.
7. Add a packaging guard proving repo-local developer skills are not exposed through packaged plugin manifests.
   - Status: implemented in `tools/validate_manifests.py` with regression coverage.

## Verification Plan

- Focused compose tests for reply draft generation and verifier outcomes.
- AppleScript builder compile tests for draft, open, reply-all, and signature variants.
- Manifest/skill packaging tests for developer-only skill isolation.
- `ruff check` and `ruff format --check` on changed Python files.
- Smallest safe live draft smoke only if Apple Mail MCP live tooling is available and cleanup can be done by exact Drafts id.
