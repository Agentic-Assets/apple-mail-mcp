# Active Pointer — apple-mail-mcp

**Branch:** `feat/v4-performance-consolidation`

**Active workstream:** v4 performance, consolidation, strict-gate, and FTS planning: [`v4-performance-consolidation-2026-05-27/phase-plan.md`](v4-performance-consolidation-2026-05-27/phase-plan.md).

**Next action:** review the strict package-gate cleanup, then start the pure JSON perf comparison harness (`tools/compare_perf_results.py` + `tests/test_compare_perf_results.py`) before MCP behavior changes.

**Latest verification (2026-05-27):** `bash tools/dev-check.sh lint` passes with fatal `ruff check`, `ruff format --check`, and `mypy --strict` for `plugin/apple_mail_mcp/`; focused strict-cleanup pytest groups passed. Earlier recon reported `.venv/bin/pytest tests/ -q -p no:cacheprovider` OK with 4 deprecation warnings, `bash tools/dev-check.sh manifest` OK, and current collection is **763 tests**.

**Blockers / caveats:** strict cleanup is intentionally broad and should be reviewed before perf/tool behavior edits; `E501` is ignored only for embedded AppleScript/docstring literals while formatter-owned wrapping remains enforced.
