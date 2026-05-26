# Active Pointer — apple-mail-mcp

**Branch:** `main`

**Active workstream:** v3.2.1 release-artifact integrity follow-up on top of the shipped Phase A/senior-review hardening workstream: [`whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md`](whose-elimination-2026-05-22/00-FINAL-SYNTHESIS.md). Phase B (Envelope Index SQLite, v4.0.0) remains deferred.

**Next action:** review, commit, and push the validator/package/docs hardening changes on `main`.

**Latest verification (2026-05-23):** `bash tools/dev-check.sh release` OK; `pytest tests/ -q` **367 passed + 30 subtests**; wrapper surface OK; rebuilt `apple-mail-plugin.zip` + `apple-mail-mcp-v3.2.1.mcpb`; `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS=1 validate_manifests.sh` OK; `mcpb unpack` + `mcpb validate` OK; `claude plugin validate --strict` OK; wheel smoke confirmed `ui` package and `mcp-ui-server` metadata.

**Blockers / caveats:** `apple-mail-mcp-v3.2.1.textClipping` is an untracked Finder text clipping and not a release artifact. `apple-mail-mcp-v3.2.1.mcpb` is rebuilt locally and ignored by git via `*.mcpb`; keep it beside the branch for Claude Desktop handoff.
