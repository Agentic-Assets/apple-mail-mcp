# Active Pointer — apple-mail-mcp

**Branch:** `codex/pr38-guidance-verifier-followup`. Native-reply workstream SHIPPED in
**v3.8.0** (committed; see CHANGELOG 3.8.0).

**Active workstream:** Native-format reply drafts. `reply_to_email` defaults to
`native_format=True`: composes in Mail's native reply window and types the body in, so
drafts keep the colored quote bar + the account's default logo signature. Object-model
flatten path preserved as `native_format=False` (headless/bulk-safe).

**Handoff (READ THIS FIRST):** [`tasks/native-reply-handoff-2026-06-30.md`](native-reply-handoff-2026-06-30.md)
for full Done / To-test detail. Findings + reusable probes:
[`tasks/native-reply-probes-2026-06-30.md`](native-reply-probes-2026-06-30.md).

**Done this pass (2026-06-30):** Test suite re-aligned to the native default (8
flatten-path tests pinned to `native_format=False`, 4 default-path tests rewritten to
the native keystroke contract, 3 native regression tests added: signature-verify skip,
`GUARD_ABORT` to `REPLY_WINDOW_FOCUS_FAILED`, verifier `stripLineBreaks`). 963 tests
pass. code-simplifier pass (no changes needed). Docs/manifests updated for
`native_format` + Accessibility. Version bumped to 3.8.0 across all six version files;
all three artifacts rebuilt and validated (`dev-check.sh release` green).

**Next action (live, needs Cayman):** the remaining TO-TEST items that cannot be mocked.
Send a saved native draft to self and confirm the logo survives the actual SEND; live
exercise attachments + native reply, `reply_to_all` native on a real multi-recipient
thread, and `GUARD_ABORT` under real focus contention. See the handoff TO-TEST section.

**Caveats (carried, not blockers):**
- Native path needs the Mail window to take focus + Accessibility permission for the
  host process (`native_format=False` avoids it; returns `REPLY_WINDOW_FOCUS_FAILED`
  when focus cannot be acquired).
- Logo not repainted in the reopened draft EDITOR = **native Mail behavior** (Cayman
  reproduced manually), NOT our bug; logo IS stored in the draft. Open item: confirm it
  survives an actual SEND (send a test draft to self, check received).

**Prior workstream** (cleanup-docs-and-simplify, branch `codex/cleanup-docs-and-simplify`,
2026-06-08) is superseded by this; its notes are below for reference if needed.

---

_Superseded 2026-06-08 pointer:_ Cleanup/simplification after native-reply/Codex
launcher fixes; plan `tasks/cleanup-docs-and-simplify-2026-06-08/phase-plan.md`.
