# V4 Performance Consolidation Learnings And Parking Lot

## Recon Learnings

- The checkout has root `AGENTS.md`, while nested guidance currently exists as `CLAUDE.md` files. Use the nested `CLAUDE.md` files for current local instructions.
- Current registered tools:
  - `analytics.py`: 5
  - `compose.py`: 5
  - `inbox.py`: 6
  - `manage.py`: 6
  - `search.py`: 3
  - `smart_inbox.py`: 3
- All real tool modules currently exceed the future 600 LOC target:
  - `compose.py`: 2159
  - `inbox.py`: 2128
  - `analytics.py`: 1815
  - `manage.py`: 1619
  - `search.py`: 1612
  - `smart_inbox.py`: 1281
- There is no persistent cache in the hot paths yet. Current speed comes from bounded newest-first slices, limited mailbox enumeration, partial timeout handling, and parallel account dispatch.
- Existing perf tooling is live/manual through `apple-mail perf-test` and mocked through `tests/test_cli_perf.py`; there is no recorded fixture comparator yet.
- `tools/dev-check.sh lint` is now a fatal package gate for `ruff check`, `ruff format --check`, and `mypy --strict` on `plugin/apple_mail_mcp/`.
- Production perf gate account is `Cayman - Agentic Assets` (`cayman@agenticassets.ai`).
- Heavy analysis perf requires explicit opt-in with `--include-analysis --allow-heavy-mail-scan`.

## Competitor Prior Art

- `imdinu/apple-mail-mcp`:
  - License: GPL-3.0-or-later.
  - Use as conceptual prior art only; do not copy code.
  - Relevant ideas: SQLite FTS5 external-content index, `.emlx` inventory reconciliation, status/rebuild/watch commands, parse failure queue.
- `dastrobu/mail-mcp`:
  - License: MIT.
  - Relevant ideas: JXA executor, selected messages from frontmost viewer, draft/reply replacement flow that preserves quote/signature via Mail UI and Accessibility.
- `patrickfreyer/apple-mail-mcp`:
  - License: MIT.
  - Upstream lineage for this repo.
  - Relevant ideas: read-only registry behavior and rich `.eml` draft workflow.

## Potential V4 Gaps Found During Recon

- `get_email_thread(mailbox="All")` may enumerate every mailbox for the target account without the same explicit mailbox-count cap used by search/list paths.
- `manage_drafts(action="open")` opens a compose window but may not enforce `MAX_OPEN_COMPOSE_WINDOWS=5`.
- Account-list AppleScript exists in both `inbox.py` and `search.py`; centralize around shared helpers before adding cache behavior.
- Repeated AppleScript helpers (`sanitize_field`, `pad2`, `iso_datetime`) and pipe-row parsers are candidates for a helpers submodule.

## Parking Lot

- Add a `tests/test_perf_budget.py` fixture format that can assert p50/p95 for all top-10 tools without touching Mail.app.
- Decide whether deprecation aliases count as registered tools during the one-minor compatibility window. The objective requires registered count `<=18`, so this affects manifest design.
- Keep strict `ruff`/`mypy` clean before behavior work; `tools/dev-check.sh lint` is now fatal for the package gate.
- Test-count docs were refreshed during the Codex plugin close-out; rerun `pytest tests/ --collect-only -q` before future release docs changes.
- Keep Gmail quirks front and center: uppercase `INBOX`, sequential-only Mail calls where needed, and re-push behavior on Cayman - Agentic Assets.
