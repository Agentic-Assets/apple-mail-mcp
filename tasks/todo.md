# Active Pointer — apple-mail-mcp

**Branch:** `codex/cleanup-docs-and-simplify`.

**Active workstream:** Cleanup and simplification after the native-reply/Codex launcher fixes. Goal: remove duplicate legacy command exposure, tighten docs around Mail-native reply drafts/signatures, and keep validator coverage aligned with the simplified plugin surface.

**Plan:** [`tasks/cleanup-docs-and-simplify-2026-06-08/phase-plan.md`](cleanup-docs-and-simplify-2026-06-08/phase-plan.md)

**Next action:** review final diff, then commit/push the cleanup branch.

**Latest verification (2026-06-08):** Cleanup branch passed `.venv/bin/python -m pytest tests/test_validate_manifests.py tests/test_compose_tools.py::ReplyToEmailSenderOverrideTests tests/test_compose_tools.py::DefaultMailSignatureSupportTests -q`, `bash tools/dev-check.sh release`, `bash tools/validate-codex-plugin.sh`, `git diff --check`, and an artifact listing check confirming `apple-mail-plugin.zip` no longer contains retired command files.

**Blockers / caveats:** No known code blocker. `mcpb` CLI is not installed locally, so release validation skipped optional `mcpb unpack + validate`; structural MCPB checks still passed through `tools/validate_manifests.py`.
