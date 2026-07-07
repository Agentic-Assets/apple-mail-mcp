# Active Pointer — apple-mail-mcp

**Tasks layout:** Agents MUST follow [`tasks/CLAUDE.md`](CLAUDE.md) § Agent requirements (`active/` · `reference/` · `archive/` only; CI enforces).

**Current branch:** `codex/verify-plugin-marketplace-parity` (release validation green; draft PR pending).

**Current workstream:** [`tasks/active/manifest-release-hardening/`](active/manifest-release-hardening/) captures the 2026-07-07 public version-surface hardening and release artifact parity pass.

**Next action:** review and merge the PR after the required Cayman approval phrase. After merge, refresh this pointer to the next active workstream.

**Previous branch:** `chore/module-line-budget-splits` (v3.9.1 pushed; commit `3d2c515`).

**Shipped (v3.9.1):** Module line-budget splits. Flat `cli.py`, `core.py`, and six tool modules became packages with facade `__init__.py` re-exports; tests reorganized into `tests/<area>/` subfolders; recursive `@mcp.tool` count gate (31 tools preserved); `tools/manifest_checks/` package behind `validate_manifests.py`; 1021 tests (`tools/expected_test_count.txt` SSOT); version 3.9.1 across all six version files; all three artifacts rebuilt and validated (`bash tools/gates/dev-check.sh release` green).

**Shipped (v3.8.0):** Native-format reply drafts. `reply_to_email` defaults to `native_format=True` (Mail native reply window + keystroke body; colored quote bar + account logo signature). Flatten path preserved as `native_format=False`. See CHANGELOG 3.8.0 and [`tasks/active/native-reply/`](active/native-reply/).

**Handoff (native-reply live TO-TEST):** [`tasks/active/native-reply/native-reply-handoff-2026-06-30.md`](active/native-reply/native-reply-handoff-2026-06-30.md). Findings + probes: [`tasks/active/native-reply/native-reply-probes-2026-06-30.md`](active/native-reply/native-reply-probes-2026-06-30.md).

**Next action (live, needs Cayman):** remaining native-reply TO-TEST items that cannot be mocked. Send a saved native draft to self and confirm the logo survives the actual SEND; live exercise attachments + native reply, `reply_to_all` native on a real multi-recipient thread, and `GUARD_ABORT` under real focus contention. See the handoff TO-TEST section.

**Deferred follow-up (brand-voice, not a blocker):** `plugin-validator` flagged pre-existing em dashes in ~10 shipped descriptions (top-level + 8 tool descriptions in `apple-mail-mcpb/manifest.json`, plus `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` descriptions). Sweep in a separate brand-voice pass, then rebuild artifacts.

**Caveats (carried, not blockers):**
- Native path needs Mail window focus + Accessibility permission (`native_format=False` avoids it; returns `REPLY_WINDOW_FOCUS_FAILED` when focus cannot be acquired).
- Logo not repainted in the reopened draft editor = native Mail behavior (not our bug); confirm it survives an actual SEND.

**Prior workstream** (cleanup-docs-and-simplify, 2026-06-08) superseded; notes in `tasks/archive/2026-06/shipped/cleanup-docs-and-simplify-2026-06-08/`.
