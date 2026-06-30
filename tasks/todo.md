# Active Pointer — apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; CI enforces).

**Branch:** `codex/pr38-guidance-verifier-followup`. Native-reply workstream SHIPPED in
**v3.8.0** (committed; see CHANGELOG 3.8.0).

**Active workstream:** Native-format reply drafts. `reply_to_email` defaults to
`native_format=True`: composes in Mail's native reply window and types the body in, so
drafts keep the colored quote bar + the account's default logo signature. Object-model
flatten path preserved as `native_format=False` (headless/bulk-safe).

**Handoff (READ THIS FIRST):** [`tasks/active/native-reply/native-reply-handoff-2026-06-30.md`](active/native-reply/native-reply-handoff-2026-06-30.md)
for full Done / To-test detail. Findings + reusable probes:
[`tasks/active/native-reply/native-reply-probes-2026-06-30.md`](active/native-reply/native-reply-probes-2026-06-30.md).

**Done this pass (2026-06-30):** Test suite re-aligned to the native default (8
flatten-path tests pinned to `native_format=False`, 4 default-path tests rewritten to
the native keystroke contract, 3 native regression tests added: signature-verify skip,
`GUARD_ABORT` to `REPLY_WINDOW_FOCUS_FAILED`, verifier `stripLineBreaks`). 966 tests
pass. code-simplifier pass (no changes needed). Docs/manifests updated for
`native_format` + Accessibility. Version bumped to 3.8.0 across all six version files;
all three artifacts rebuilt and validated (`dev-check.sh release` green).
Post-ship `plugin-dev:plugin-validator` (PASS, no drift) + `plugin-dev:skill-reviewer`
pass run; the reviewer caught that the bundled `email-drafting` skill still described
replies in the old object-model/plain-text framing, so it was synced to the native
default (mechanism, Accessibility/focus, `REPLY_WINDOW_FOCUS_FAILED` recovery,
signature behavior) and artifacts rebuilt again. Then reviewed a sibling agent's
attachment-verification changes (multiset duplicate-name matching in `verify_draft`
and reply verification): kept the correct Counter/`suppress(OSError)` work, fixed a
runtime bug where the reply verifier used `delete item N of <list>` (a Mail command
that throws on a list, degrading every matched attachment to `"unsupported"`) by
switching to `set item N ... to missing value` (dictionary- and live-verified). Also
centralized the collected-test count to `tools/expected_test_count.txt` (single source
of truth) and added a `dev-check.sh` gate that fails on drift, so the number no longer
lives hardcoded across CLAUDE.md/AGENTS.md/README/tests+tasks CLAUDE.md. **Module line
budget** gate added (`tools/check_module_line_budget.py`, `tests/test_module_line_budget.py`):
600 LOC warn on `plugin/apple_mail_mcp/` + `tools/`, baseline regression in CI/dev-check/validate_manifests;
documented across `docs/CLAUDE-conventions.md` § Module line budget and agent hubs.

**Deferred follow-up (brand-voice, not a blocker):** `plugin-validator` flagged
pre-existing em dashes in ~10 shipped descriptions (top-level + 8 tool descriptions in
`apple-mail-mcpb/manifest.json`, plus the `plugin/.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` descriptions). These violate the no-em-dash rule but
predate this workstream; sweep them (and align the Codex `plugin.json` copy) in a
separate brand-voice pass, then rebuild artifacts.

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
launcher fixes; plan `tasks/archive/2026-06/shipped/cleanup-docs-and-simplify-2026-06-08/phase-plan.md`.
