Verdict: Do now.

Recommendation 4 is worth doing now, but only as small string-builder helpers. Do not introduce a broad AppleScript template system, and do not move full tool scripts out of their current functions.

## Evidence

- The original recommendation names the exact duplicated targets: `sanitize_field`, `textOffset`, Drafts bounded newest-window setup, header parsing, and recipient address collection in `tasks/draft-verification-simplification-recommendations-2026-06-26.md:129`.
- `verify_draft` embeds a stricter `sanitize_field` handler, including `|||` and `;;` cleanup, in `plugin/apple_mail_mcp/tools/compose.py:1630`.
- `search_emails` and `get_email_by_id` embed a lighter `sanitize_field` handler that cleans `|||` but not `;;` in `plugin/apple_mail_mcp/tools/search.py:572` and `plugin/apple_mail_mcp/tools/search.py:1296`.
- `_verify_saved_reply_draft` embeds `textOffset` at `plugin/apple_mail_mcp/tools/compose.py:407`, and `verify_draft` repeats the same handler at `plugin/apple_mail_mcp/tools/compose.py:1652`.
- `_verify_saved_reply_draft` does exact id lookup first, then falls back to a bounded Drafts head window at `plugin/apple_mail_mcp/tools/compose.py:491` and `plugin/apple_mail_mcp/tools/compose.py:504`.
- `_build_draft_lookup` already centralizes one safe Drafts subject lookup, including head and tail windows, at `plugin/apple_mail_mcp/tools/compose.py:188`.
- `manage_drafts(action="list")` repeats the head-window Drafts setup at `plugin/apple_mail_mcp/tools/compose.py:2653`.
- `manage_drafts(action="find")` repeats the same head-window setup and then parses thread headers at `plugin/apple_mail_mcp/tools/compose.py:2750` and `plugin/apple_mail_mcp/tools/compose.py:2770`.
- `verify_draft` parses `In-Reply-To` and `References` from `all headers of aDraft` at `plugin/apple_mail_mcp/tools/compose.py:1727`.
- `get_email_by_id` parses the same headers from `all headers of aMessage` at `plugin/apple_mail_mcp/tools/search.py:1419`.
- `verify_draft` collects to, cc, and bcc recipients from an exact Drafts message at `plugin/apple_mail_mcp/tools/compose.py:1688`, `plugin/apple_mail_mcp/tools/compose.py:1701`, and `plugin/apple_mail_mcp/tools/compose.py:1714`.
- `get_email_by_id` collects to, cc, and bcc recipients from a single exact message at `plugin/apple_mail_mcp/tools/search.py:1389`, `plugin/apple_mail_mcp/tools/search.py:1404`, and `plugin/apple_mail_mcp/tools/search.py:1441`.
- Bulk `search_emails` intentionally does not collect recipients because per-message recipient reads can hang large remote mailboxes, documented in code at `plugin/apple_mail_mcp/tools/search.py:679` and locked by `tests/test_mail_search_tools.py:1163`.
- Tests already guard bounded Drafts behavior in `tests/test_compose_tools.py:1881`, `tests/test_compose_tools.py:1960`, `tests/test_bounded_scan_contract.py:199`, and `tests/test_phase_2_scan_hardening.py:43`.
- Tests already guard header extraction in `tests/test_compose_tools.py:1853`, `tests/test_compose_tools.py:1984`, and `tests/test_mail_search_tools.py:1299`.
- The repo already treats `core.py` and `bounded_scan.py` as homes for shared script builders in `plugin/apple_mail_mcp/CLAUDE.md:18` and `plugin/apple_mail_mcp/CLAUDE.md:23`.

## Centralize Now

1. `textOffset`

Move this exact AppleScript handler into a helper builder. It is identical across reply draft verification and saved Drafts verification, and it has no tool-specific Mail dictionary assumptions.

2. `sanitize_field`

Centralize it, but preserve the two current modes. The common mode should collapse return, linefeed, tab, and `|||`. A stricter row mode should also collapse `;;` for attachment rows. Do not silently make every existing script use the stricter mode unless tests bless that output change.

3. Thread header parsing for exact or bounded single-message contexts

Build a helper that emits the `all headers of <message_var>` loop and assigns caller-provided variables for `In-Reply-To` and `References`. It should accept `message_var`, `in_reply_to_var`, `references_var`, and an optional sanitizer name. That covers `aDraft` and `aMessage` without coupling the rest of the scripts.

4. Recipient address collection for exact or bounded small-message contexts

Build a helper that emits one recipient-kind collection block for `to`, `cc`, or `bcc`. It should accept `message_var`, `recipient_kind`, `output_var`, and optional sanitizer. Use it in `verify_draft` and `get_email_by_id`. It can also serve bounded Drafts list output for To-only collection.

5. Drafts bounded head-window setup inside `compose.py`

Create a compose-local builder for the common head-window pattern:

```python
def _build_drafts_head_window(messages_var: str, limit_expr: str, mailbox_var: str = "draftsMailbox") -> str:
    ...
```

It should emit the readable sequence now repeated in `manage_drafts(action="list")`, `manage_drafts(action="find")`, and `_verify_saved_reply_draft`: count Drafts, cap `headEnd`, use `{}` when empty, otherwise `messages 1 thru headEnd of draftsMailbox`.

Keep `_build_draft_lookup` as the subject-lookup helper, but let it reuse the same head-window builder before adding its tail fallback.

## Keep Local

- The polling and exact-id-first control flow in `_verify_saved_reply_draft` should stay local. Its status strings, attachment checks, signature checks, and body-before-quote logic are tool semantics, not reusable script snippets.
- `manage_drafts` subject filtering should stay local because the list and find actions produce different output and warnings.
- Bulk `search_emails` recipient behavior should stay local and continue emitting empty recipient placeholders. A shared recipient helper must not appear in the bulk scan path, because the tests document that recipient reads can hang Exchange or Gmail scans.
- Exact `every message of draftsMailbox whose id is ...` lookups should stay local. The no-unbounded-scan lint allows id predicates, and the tests expect exact-id Drafts lookup for `verify_draft` and reply verification.

## Placement

Use a hybrid placement:

- Add `plugin/apple_mail_mcp/applescript_snippets.py` for generic AppleScript fragments used by more than one tool module:
  - `sanitize_field_handler(include_attachment_row_delimiter: bool = False, name: str = "sanitize_field") -> str`
  - `text_offset_handler(name: str = "textOffset") -> str`
  - `thread_headers_block(message_var: str, in_reply_to_var: str, references_var: str, sanitize_fn: str | None = "sanitize_field") -> str`
  - `recipient_addresses_block(message_var: str, recipient_kind: Literal["to", "cc", "bcc"], output_var: str, sanitize_fn: str | None = "sanitize_field") -> str`
- Keep Drafts-specific builders in `plugin/apple_mail_mcp/tools/compose.py`, because Drafts head windows are compose-domain behavior and currently only used there.
- Do not put this in `bounded_scan.py`. That module owns ScanWindow tokens and mailbox scan safety. Drafts exact-id and Drafts head-window snippets are not ScanWindow issuance.
- Do not add Jinja, a DSL, or a class-based template layer. Plain functions returning indented multi-line strings are enough and keep generated scripts reviewable.

## Testing Approach

Add unit tests around helper output, then keep existing generated-script tests.

- New pure helper tests should assert readable AppleScript substrings, not byte-for-byte full scripts. Check handler names, variable names, delimiter cleanup, `all headers of aDraft`, `starts with "In-Reply-To:"`, `starts with "References:"`, and recipient-kind output.
- Existing script-capture tests should continue to assert the generated scripts contain readable blocks, such as `messages 1 thru headEnd of draftsMailbox`, `all headers of aDraft`, and recipient loops.
- Add or update a compose test that confirms every Drafts head-window caller avoids `every message of draftsMailbox` except exact id lookup. For `manage_drafts(action="list")` and `manage_drafts(action="find")`, assert no `every message of draftsMailbox`.
- Keep `tests/test_no_unbounded_whose.py` as the global regression gate. The helper refactor should not require changing its allowlist.
- Add one negative assertion that bulk `search_emails` still does not include `to recipients of aMessage`, `cc recipients of aMessage`, or `address of aRecip`.
- If helper output indentation changes, prefer tests that look for semantic lines rather than exact indentation.

## Risks And Mitigations

- Risk: Shared recipient helper gets used in bulk search and reintroduces Exchange or Gmail hangs.
  Mitigation: Name the helper for single-message use, document the restriction, and add a negative bulk-search assertion.
- Risk: Normalizing `sanitize_field` changes output for double semicolons in search results.
  Mitigation: Keep an explicit mode for the `;;` delimiter and use the stricter mode only where attachment rows need it.
- Risk: Drafts head-window helper accidentally drops the empty-Drafts guard or changes limit semantics.
  Mitigation: Pure helper tests plus existing `manage_drafts` capture tests should assert `{}` for empty Drafts, `if headEnd > <limit>`, and `messages 1 thru headEnd`.
- Risk: Central header parsing becomes less readable inside large f-strings.
  Mitigation: Helpers should return small complete AppleScript blocks with caller-provided variable names. Do not hide surrounding tool logic.
- Risk: Exact id `whose id is` gets mistaken for an unbounded scan.
  Mitigation: Keep exact id lookups local and retain tests that assert the exact id branch before bounded fallback.

## Verification Commands

```bash
.venv/bin/pytest tests/test_compose_tools.py::ManageDraftsListTests -q
.venv/bin/pytest tests/test_compose_tools.py::ReplyToEmailSenderOverrideTests::test_reply_draft_success_runs_bounded_saved_draft_verifier -q
.venv/bin/pytest tests/test_mail_search_tools.py::NewFieldsTests -q
.venv/bin/pytest tests/test_bounded_scan_contract.py::BoundedInboxScanTests::test_draft_lookup_uses_safe_pattern -q
.venv/bin/pytest tests/test_phase_2_scan_hardening.py::ComposeScanCapTests -q
.venv/bin/pytest tests/test_no_unbounded_whose.py -q
```

Before shipping a real implementation, also run:

```bash
.venv/bin/pytest tests/test_compose_tools.py tests/test_mail_search_tools.py tests/test_bounded_scan_contract.py tests/test_phase_2_scan_hardening.py tests/test_no_unbounded_whose.py -q
```
