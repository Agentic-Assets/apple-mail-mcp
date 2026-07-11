Verdict: Do now.

The helper extraction is low risk if the first pass keeps public tool registration in `compose.py` and moves only pure payload and expectation helpers into a private helper module. It should not move `verify_draft` itself yet.

## Evidence

- The recommendation already points at a private helper module, `plugin/apple_mail_mcp/tools/draft_verification.py`, and explicitly says to keep public MCP registration in `compose.py`: `tasks/draft-verification-simplification-recommendations-2026-06-26.md:24`, `tasks/draft-verification-simplification-recommendations-2026-06-26.md:26`, `tasks/draft-verification-simplification-recommendations-2026-06-26.md:32`.
- The exact helper cluster is pure Python and currently sits directly above `verify_draft`: `_parse_expected_attachments`, `_split_csv_addresses`, `_csv_contains_all`, `_normalize_attachment_rows`, and `_build_verify_draft_payload` are at `plugin/apple_mail_mcp/tools/compose.py:1461`, `plugin/apple_mail_mcp/tools/compose.py:1472`, `plugin/apple_mail_mcp/tools/compose.py:1479`, `plugin/apple_mail_mcp/tools/compose.py:1487`, and `plugin/apple_mail_mcp/tools/compose.py:1502`.
- Expectation-warning construction is contained inside `_build_verify_draft_payload`: body and subject warnings at `plugin/apple_mail_mcp/tools/compose.py:1528` and `plugin/apple_mail_mcp/tools/compose.py:1534`, recipient warnings at `plugin/apple_mail_mcp/tools/compose.py:1540`, attachment warnings at `plugin/apple_mail_mcp/tools/compose.py:1547`, signature warnings at `plugin/apple_mail_mcp/tools/compose.py:1554`, and quoted-original warnings at `plugin/apple_mail_mcp/tools/compose.py:1560`.
- `verify_draft` depends on only three moved symbols directly: `_parse_expected_attachments`, `_split_csv_addresses`, and `_build_verify_draft_payload` at `plugin/apple_mail_mcp/tools/compose.py:1623`, `plugin/apple_mail_mcp/tools/compose.py:1624`, `plugin/apple_mail_mcp/tools/compose.py:1625`, and `plugin/apple_mail_mcp/tools/compose.py:1801`.
- Public registration is attached to `verify_draft`, not the helpers: `@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)` and `@inject_preferences` remain at `plugin/apple_mail_mcp/tools/compose.py:1591` and `plugin/apple_mail_mcp/tools/compose.py:1592`.
- Package registration imports `compose` for side effects: `plugin/apple_mail_mcp/__init__.py:13` through `plugin/apple_mail_mcp/__init__.py:17`. A helper module imported only by `compose.py` does not need to be added there.
- Current tool guidance says all `@mcp.tool` handlers live in the six tool modules, and `compose.py` owns six public tools including `verify_draft`: `plugin/apple_mail_mcp/tools/CLAUDE.md:1`, `plugin/apple_mail_mcp/tools/CLAUDE.md:2`, and `plugin/apple_mail_mcp/tools/CLAUDE.md:10`.
- Read-only behavior is registry based. `--read-only` removes only `SEND_TOOLS` after importing the package at `plugin/apple_mail_mcp/__main__.py:58` through `plugin/apple_mail_mcp/__main__.py:64`, and `SEND_TOOLS` excludes `verify_draft` at `plugin/apple_mail_mcp/server.py:71`. The convention states that read-only mode removes send tools from the registry and does not branch inside implementations: `docs/CLAUDE-conventions.md:120` through `docs/CLAUDE-conventions.md:122`.
- The registry test treats `verify_draft` as read-only at `tests/test_read_only_registry.py:17` through `tests/test_read_only_registry.py:35`, and asserts read-only annotations at `tests/test_read_only_registry.py:86` through `tests/test_read_only_registry.py:89`.
- Manifest validation scans `plugin/apple_mail_mcp/tools/*.py` for `@mcp.tool` decorators at `tools/validate_manifests.py:98` through `tools/validate_manifests.py:110`, then compares code tool names and MCPB names at `tools/validate_manifests.py:936` through `tools/validate_manifests.py:975`. A new helper module with no decorator should not change count or manifest names.
- The MCPB manifest exposes only the public `verify_draft` tool, not helper names: `apple-mail-mcpb/manifest.json:105` through `apple-mail-mcpb/manifest.json:106`.
- Existing `verify_draft` tests call the public `compose_tools.verify_draft` path and assert payload behavior and script construction: `tests/test_compose_tools.py:1814` through `tests/test_compose_tools.py:1854`, `tests/test_compose_tools.py:1856` through `tests/test_compose_tools.py:1861`, and `tests/test_compose_tools.py:1863` through `tests/test_compose_tools.py:1879`. They do not currently import the helper names directly.
- Saved reply verification is a related but separate cluster. `_ReplyDraftVerification`, `_reply_verification_from_output`, `_format_reply_verification_lines`, and `_verify_saved_reply_draft` start at `plugin/apple_mail_mcp/tools/compose.py:325`, `plugin/apple_mail_mcp/tools/compose.py:335`, `plugin/apple_mail_mcp/tools/compose.py:365`, and `plugin/apple_mail_mcp/tools/compose.py:384`. Tests already call `_verify_saved_reply_draft` directly at `tests/test_compose_tools.py:1060` and `tests/test_compose_tools.py:1137`, so moving that cluster should be a separate decision.

## Impact

Tool registration should be unchanged if `verify_draft` stays in `compose.py`. The package still imports `compose.py`, the decorator still registers `verify_draft`, and the new helper module remains private implementation code.

Read-only registry behavior should be unchanged. `verify_draft` is read-only and not in `SEND_TOOLS`; the helper module should not import `mcp`, `server`, or annotations.

Plugin validation should be unchanged. The validator counts decorators, not private helper functions. A new `tools/draft_verification.py` file with no `@mcp.tool` decorator does not require manifest edits, version bumps, skill edits, or MCPB `tools[]` changes.

Tests get better targets. Existing public behavior tests can stay where they are, while new pure helper tests can cover exact warning construction without invoking AppleScript or patching `run_applescript`.

## Implementation Plan

1. Add `plugin/apple_mail_mcp/tools/draft_verification.py`.
2. Move these symbols unchanged into the new module:
   - `_parse_expected_attachments`
   - `_split_csv_addresses`
   - `_csv_contains_all`
   - `_normalize_attachment_rows`
   - `_build_verify_draft_payload`
3. Keep `_csv_contains_all` and `_normalize_attachment_rows` private to the helper module. Import only the symbols `compose.py` calls:

```python
from apple_mail_mcp.tools.draft_verification import (
    _build_verify_draft_payload,
    _parse_expected_attachments,
    _split_csv_addresses,
)
```

4. Import direction must be one way: `compose.py` imports `draft_verification.py`; `draft_verification.py` imports only standard-library dependencies such as `Path` and `Any`. It must not import `compose.py`, `server.py`, `mcp`, `run_applescript`, or annotations.
5. Leave `verify_draft`, its AppleScript, and its `@mcp.tool` decorator in `compose.py`.
6. Leave `plugin/apple_mail_mcp/__init__.py`, manifests, marketplace files, zips, MCPB artifacts, and skills untouched.
7. Add focused pure tests in a new file such as `tests/test_draft_verification_helpers.py` for:
   - attachment path basename normalization
   - comma-separated recipient parsing and exact address matching
   - body, subject, to, cc, attachment, signature, and quoted-original warnings
   - malformed attachment size rows returning `size: None`
8. Keep the existing public `verify_draft` tests in `tests/test_compose_tools.py` unless Recommendation 2 is implemented in the same branch.

## Risks And Mitigations

- Risk: circular imports or accidental registration side effects. Mitigation: helper module imports only standard library code and has no `@mcp.tool`.
- Risk: response shape drift in the JSON payload. Mitigation: move code unchanged first, then add direct tests that assert exact keys and warning values.
- Risk: hidden imports of old private names. Mitigation: `rg` currently finds only `compose.py` call sites for these helper names; rerun the search before editing.
- Risk: helper module under `tools/` is scanned by validators. Mitigation: validator scans decorators, so keep the file decorator-free.
- Risk: broader draft-verification cleanup pulls in reply-specific verifier behavior. Mitigation: do not move `_verify_saved_reply_draft` or `_reply_draft_verification_error` in this pass.
- Risk: artifact churn. Mitigation: do not run `bash tools/dev-check.sh release` for this helper-only refactor unless a release is explicitly requested, because that tier rebuilds distribution artifacts.

## Verification Commands

Run these after implementation:

```bash
rg -n "_build_verify_draft_payload|_normalize_attachment_rows|_parse_expected_attachments|_split_csv_addresses|_csv_contains_all" plugin/apple_mail_mcp tests
.venv/bin/pytest tests/test_draft_verification_helpers.py -q
.venv/bin/pytest tests/test_compose_tools.py::ManageDraftsListTests -q
.venv/bin/pytest tests/test_read_only_registry.py tests/test_validate_manifests.py -q
bash tools/dev-check.sh lint
bash tools/dev-check.sh
```

Use `bash tools/dev-check.sh release` only if the branch is being prepared as a release and artifact rebuilds are intended.

## Still Needs Research

No blocker remains for this narrow helper extraction.

Separate research is still needed before moving the public `verify_draft` tool to a new public tool module, because that would touch side-effect imports, docs, manifest validation, tool-count claims, and possibly skills. Separate research is also needed before moving the saved reply verifier cluster, because tests currently reach its private functions directly and its behavior is reply-specific.
