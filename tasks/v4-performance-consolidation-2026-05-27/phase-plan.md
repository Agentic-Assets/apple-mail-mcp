# V4 Performance Consolidation Phase Plan

> **For agentic workers:** Use subagents for research, implementation, tests, docs, and live verification. Use `plugin-dev:plugin-validator`, `plugin-dev:skill-reviewer`, `mcp-builder`, `plugin-dev:mcp-integration`, `plugin-dev:plugin-structure`, and `code-simplifier:code-simplifier` at the ship checkpoints described below.

**Goal:** Ship a v4.x Apple Mail MCP release that is measurably faster, easier for agents to call, and easier to extend than the v3.4.0 baseline at `3f6d3f1`.

**Architecture:** Preserve every existing capability while collapsing narrow tools into mode-based public tools with one-minor-version deprecation aliases. Add a zero-state default path plus an explicit opt-in SQLite FTS5 body index for full-text search. Keep Mail.app calls bounded, sequential where Mail requires it, and covered by recorded perf fixtures before performance behavior changes.

**Tech Stack:** Python 3.10+, FastMCP, AppleScript through `core.run_applescript()`, SQLite FTS5, pytest, ruff, mypy strict, repo release scripts, Claude/Codex plugin manifests, MCPB artifacts.

---

## Current Baseline

- Branch: `feat/v4-performance-consolidation`
- Baseline commit: `3f6d3f1`
- Tool count: `28` via `rg '^@mcp\.tool' plugin/apple_mail_mcp/tools/*.py | wc -l`
- Test collection: `763` tests across `39` test files via `.venv/bin/pytest --collect-only -q tests/`
- Existing full suite status from read-only recon: `.venv/bin/pytest tests/ -q -p no:cacheprovider` passed with 4 deprecation warnings
- Manifest status from read-only recon: `bash tools/dev-check.sh manifest` passed with version `3.4.0`, tools `28`

## Strict Gate Status

The v4 objective requires `ruff + mypy --strict clean on plugin/apple_mail_mcp/`. The untouched `3f6d3f1` baseline failed strict gates:

- `.venv/bin/ruff check plugin/apple_mail_mcp/` -> `593` errors, `454` fixable
- `.venv/bin/ruff format --check plugin/apple_mail_mcp/` -> `12` files would be reformatted
- `.venv/bin/mypy --strict plugin/apple_mail_mcp/` -> `116` errors in `9` files

Current cleanup has resolved the package gate:

- `.venv/bin/ruff check plugin/apple_mail_mcp/` -> passes
- `.venv/bin/ruff format --check plugin/apple_mail_mcp/` -> passes
- `.venv/bin/mypy --strict plugin/apple_mail_mcp/` -> passes
- `bash tools/dev-check.sh lint` -> passes

`tools/dev-check.sh lint` and `tools/dev-check.sh release` are fatal gates for Ruff and strict mypy. Keep this gate green before starting MCP behavior changes.

## Phase 0 Non-Goals

- Do not rewrite production MCP tools before measurement is in place.
- Do not change manifests, versions, plugin marketplace metadata, or MCPB tool lists in the perf-harness slice.
- Do not mix MCP behavior changes into the strict lint/type cleanup slice.
- Do not run heavy live analysis without `--include-analysis --allow-heavy-mail-scan`.

## Decisions Needed Before Code Work

1. Tool-count strategy:
   - Keep deprecated aliases registered for one minor release, or hide them from the default registry while preserving compatibility through CLI/plugin docs.
   - The objective asks for registered count `<=18`, so alias handling must be designed before manifest edits.
2. FTS storage boundary:
   - Confirm the acceptable local index path and opt-in UX before indexing body text.
   - imdinu/apple-mail-mcp is GPL-3.0-or-later; use only as prior-art architecture and credit explicitly.

## Phase 0: Measurement Harness

**Purpose:** Make every later performance change measurable against `3f6d3f1`.

**Files:**
- Create: `tools/compare_perf_results.py`
- Create: `tests/test_compare_perf_results.py`
- Modify: `docs/AGENT_LIVE_TESTING.md`

**TDD steps:**

1. Write failing tests for a pure JSON comparison utility:
   - matching perf cases by `name`
   - reporting `delta_ms` and `delta_pct`
   - failing when current payload has `ok: false`
   - failing when baseline/current cases are missing
   - failing when regression exceeds `--max-regression-pct`
2. Run `.venv/bin/pytest tests/test_compare_perf_results.py -q` and confirm the module import fails.
3. Implement `tools/compare_perf_results.py` with `load_payload`, `compare_payloads`, and `main`.
4. Run `.venv/bin/pytest tests/test_compare_perf_results.py -q`.
5. Run `.venv/bin/pytest tests/test_cli_perf.py tests/test_compare_perf_results.py -q`.
6. Record baseline capture commands in `docs/AGENT_LIVE_TESTING.md`.

**Verification:**

```bash
.venv/bin/pytest tests/test_compare_perf_results.py -q
.venv/bin/pytest tests/test_cli_perf.py tests/test_compare_perf_results.py -q
bash tools/dev-check.sh manifest
```

## Phase 1: Strict Gate Strategy

**Purpose:** Unblock the objective's required strict lint/type release gate without hiding existing debt.

**Files:**
- Modify: `pyproject.toml`
- Modify: `tools/dev-check.sh`
- Create or modify focused tests under `tests/` only when the gate behavior changes

**Steps:**

1. Capture full strict baselines to files under the workstream folder:
   - `.venv/bin/ruff check plugin/apple_mail_mcp/ > tasks/v4-performance-consolidation-2026-05-27/ruff-baseline.txt`
   - `.venv/bin/mypy --strict plugin/apple_mail_mcp/ > tasks/v4-performance-consolidation-2026-05-27/mypy-strict-baseline.txt`
2. Split issues into mechanical formatting/pyupgrade/import fixes versus semantic typing fixes.
3. Land the mechanical ruff cleanup only if its diff is reviewable and tests stay green.
4. Land strict mypy cleanup module-by-module, starting with helper modules before tool modules.
5. Keep `tools/dev-check.sh lint` fatal for the package gate; if it fails, fix the underlying Ruff or strict mypy issue before release work proceeds.

**Verification:**

```bash
.venv/bin/ruff check plugin/apple_mail_mcp/
.venv/bin/ruff format --check plugin/apple_mail_mcp/
.venv/bin/mypy --strict plugin/apple_mail_mcp/
.venv/bin/pytest tests/ -q
```

## Phase 2: Tool Consolidation

**Purpose:** Reduce registered public tools from `28` to `<=18` while preserving every capability for one minor release.

**Initial consolidations:**

- `list_account_addresses` -> `list_accounts(mode="addresses")`
- `get_mailbox_unread_counts` -> `get_inbox_overview(include_unread_counts=True, mode="unread_counts")`
- `get_top_senders` -> `get_statistics(scope="top_senders")`
- narrow draft list/open/delete modes stay under `manage_drafts`

**Rules:**

- One consolidation per PR.
- Write failing compatibility tests before changing registry behavior.
- Deprecated aliases must return the same shape as v3.4.0 during the compatibility window.
- Update MCPB `tools[]`, tool-count claims, plugin skills, README, and CHANGELOG in the same PR.

**Verification:**

```bash
rg '^@mcp\.tool' plugin/apple_mail_mcp/tools/*.py | wc -l
.venv/bin/pytest tests/test_validate_manifests.py tests/test_wrapper_surface.py -q
bash tools/dev-check.sh release
```

## Phase 3: Hot Tool Performance

**Purpose:** Bring the top-10 hot tools under `<500 ms p50 / <1500 ms p95` with a 24K-message warm cache and `<3 s p95` cold IMAP.

**Top-10 tools:**

`list_inbox_emails`, `search_emails`, `get_email_by_id`, `get_inbox_overview`, `get_mailbox_unread_counts`, `list_accounts`, `list_mailboxes`, `get_email_thread`, `manage_drafts`, `get_statistics`

**Steps:**

1. Add `tests/test_perf_budget.py` with recorded fixtures for p50/p95 budgets.
2. Capture v3.4.0 live JSON from a separate worktree at `3f6d3f1`.
3. Add a warm-cache layer only where measured output proves repeated Mail.app reads are the bottleneck.
4. Keep Mail calls sequential where Gmail/Mail.app requires it.
5. Reject any change whose p95 regresses versus the recorded baseline.

**Verification:**

```bash
.venv/bin/pytest tests/test_perf_budget.py -q
.venv/bin/python tools/compare_perf_results.py /tmp/apple-mail-v340-perf.json /tmp/apple-mail-v4-perf.json --max-regression-pct 0
```

Production perf account:

```bash
DEFAULT_MAIL_ACCOUNT="cayman@agenticassets.ai" \
  .venv/bin/apple-mail perf-test \
  --profile production \
  --account "cayman@agenticassets.ai" \
  --json
```

Heavy analysis perf requires explicit opt-in:

```bash
DEFAULT_MAIL_ACCOUNT="cayman@agenticassets.ai" \
  .venv/bin/apple-mail perf-test \
  --profile production \
  --account "cayman@agenticassets.ai" \
  --include-analysis \
  --allow-heavy-mail-scan \
  --json
```

## Phase 4: SQLite FTS5 Body Search

**Purpose:** Offer full-text body search through an explicitly enabled local SQLite FTS5 index while default install remains zero-state.

**Rules:**

- One-shot opt-in required before any body index is created.
- Do not copy imdinu/apple-mail-mcp GPL code.
- Credit imdinu/apple-mail-mcp, dastrobu/mail-mcp, and patrickfreyer/apple-mail-mcp in comments and CHANGELOG where their prior art informed design.
- Stop if the index requires storing email bodies outside the approved local index boundary.

**Suggested credit comment:**

```python
# FTS/index design independently implemented after reviewing imdinu/apple-mail-mcp
# (GPL-3.0-or-later); no GPL code copied.
```

## Phase 5: Agent-Friendly Tool Docs

**Purpose:** Make every remaining tool self-explanatory for agents.

**Rules:**

- Every remaining tool docstring starts with a one-sentence "when to use" line.
- Every remaining tool docstring includes a copy-paste call example.
- Skills under `plugin/skills/` must route agents to consolidated tools without trigger overlap.

**Verification:**

```bash
.venv/bin/pytest tests/test_contracts_inbox_tools.py tests/test_contracts_search_tools.py tests/test_contracts_smart_inbox.py -q
```

## Ship Gate

Before any v4 ship candidate:

```bash
.venv/bin/pytest tests/ -q
.venv/bin/pytest tests/test_perf_budget.py -q
.venv/bin/ruff check plugin/apple_mail_mcp/
.venv/bin/ruff format --check plugin/apple_mail_mcp/
.venv/bin/mypy --strict plugin/apple_mail_mcp/
bash tools/dev-check.sh release
```

Then run live CLI parity against:

- `Cayman - Agentic Assets`
- `iCloud`
- `TU - Cayman`
- `cayman@caiyman.ai`

Each consolidated tool must match v3.4.0 output shape for the compatibility window.
