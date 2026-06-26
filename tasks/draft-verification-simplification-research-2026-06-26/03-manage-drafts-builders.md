Verdict: Do now, but extract only `list` and `find` first.

## Decision

Start with the read-only script-generation branches:

- Extract `action == "list"` and `action == "find"` into private builders now.
- Leave `create`, `send`, `open`, `delete`, and `cleanup_empty` inline until the first extraction has snapshot tests and static scan coverage in place.

Reason: `list` and `find` are the branches called out by the recommendation as easiest to snapshot-test, and they share the same Drafts head-window safety shape. The mutating branches include draft-safe blocking, sender validation, exact id preference, subject fallback, and deletion caps. Pulling every action at once would raise review risk without adding much immediate safety.

## Evidence

- Recommendation 3 asks to keep the public signature unchanged and move action-specific AppleScript into private helpers: `tasks/draft-verification-simplification-recommendations-2026-06-26.md:103`.
- It specifically names `list`, `find`, `create`, `send`, `open`, `delete`, and `cleanup_empty` as covered behaviors: `tasks/draft-verification-simplification-recommendations-2026-06-26.md:93`.
- It requires preserving text output, draft-safe send blocking, exact `draft_id` preference, the `limit` cap, and no unbounded `every message` behavior: `tasks/draft-verification-simplification-recommendations-2026-06-26.md:121`.
- Repo guidance says `manage_drafts` is a sync single-account tool: `docs/CLAUDE-conventions.md:100`.
- Repo guidance says read-only mode removes send tools from the registry, but `manage_drafts` remains registered and blocks `send` internally: `docs/CLAUDE-conventions.md:122`.
- Repo guidance says lifecycle operations should prefer `draft_id` over `draft_subject`: `docs/CLAUDE-conventions.md:142`.
- Forbidden AppleScript patterns ban raw `every message of MB`, non-id `whose`, and `whose` over slice-bound variables: `docs/CLAUDE-conventions.md:74`.
- `manage_drafts` currently computes `list_limit` once from `DRAFT_LIST_CAP`, clamping caller `limit` to `1..75`: `plugin/apple_mail_mcp/tools/compose.py:2602`.
- The local nested `_draft_action_lookup()` already encapsulates exact id lookup and subject fallback for `send`, `open`, and `delete`: `plugin/apple_mail_mcp/tools/compose.py:2604`.
- Exact `draft_id` lookup uses id-only `whose`, which is allowed and must stay preferred: `plugin/apple_mail_mcp/tools/compose.py:2613`.
- Subject fallback delegates to `_build_draft_lookup()`, which uses bounded head and tail slices with in-loop subject filtering: `plugin/apple_mail_mcp/tools/compose.py:188`.
- The `list` branch builds a bounded newest-first Drafts window and never raw-enumerates Drafts: `plugin/apple_mail_mcp/tools/compose.py:2653`.
- The `list` branch applies `subject_contains` with `ignoring case` inside the loop and deliberately avoids date filters so new drafts with null dates are not dropped: `plugin/apple_mail_mcp/tools/compose.py:2631`.
- The `find` branch requires `in_reply_to`, strips angle brackets and spaces, applies optional subject filtering in-loop, and scans bounded Drafts headers: `plugin/apple_mail_mcp/tools/compose.py:2731`.
- `send` blocks both read-only and draft-safe mode before building or running AppleScript: `plugin/apple_mail_mcp/tools/compose.py:2896`.
- `cleanup_empty` enforces `max_deletes >= 1`, defaults to dry run, scans only `messages 1 thru 75`, then caps acted deletes separately: `plugin/apple_mail_mcp/tools/compose.py:3013`.
- All action scripts are executed through `run_applescript`, with a common timeout error wrapper: `plugin/apple_mail_mcp/tools/compose.py:3084`.

## Existing Test Locks

- Exact `draft_id` is part of the public signature and defaults to `None`: `tests/test_compose_tools.py:1628`.
- `create` rejects reply-like standalone drafts unless explicitly confirmed: `tests/test_compose_tools.py:1634`.
- `create` must save the draft and emit `Draft ID`: `tests/test_compose_tools.py:1648`.
- Sender override validation for `create` must run before the main script and avoid main-script execution on invalid sender: `tests/test_compose_tools.py:1702`.
- `send` must prefer exact `draft_id`, include id-only `whose`, emit `Draft ID`, and not include subject matching when an id is present: `tests/test_compose_tools.py:1752`.
- `open` and `delete` must also target exact `draft_id` and avoid `contains` subject fallback when an id is present: `tests/test_compose_tools.py:1776`.
- Invalid `draft_id` must be rejected before AppleScript runs: `tests/test_compose_tools.py:1801`.
- `list` must use `messages 1 thru headEnd`, cap `headEnd`, stop at `shownCount`, and avoid `every message of draftsMailbox`: `tests/test_compose_tools.py:1881`.
- Caller `limit` must cap both the head window and result count: `tests/test_compose_tools.py:1905`.
- `subject_contains` adds only an in-loop case-insensitive filter and no date filter: `tests/test_compose_tools.py:1922`.
- `find` must use the same bounded head window, avoid raw Drafts enumeration, read headers, and match `In-Reply-To` or `References`: `tests/test_compose_tools.py:1960`.
- Phase 2 hardening also asserts the Drafts list cap and no raw enumeration: `tests/test_phase_2_scan_hardening.py:31`.
- Static lint rejects dangerous non-id `whose`, raw enumeration, slice-var `whose`, and `allow_full_scan`: `tests/test_no_unbounded_whose.py:1`.
- None-handling tests guard against injecting Python `None` into `manage_drafts` list and create scripts: `tests/test_compose_none_handling.py:258` and `tests/test_compose_none_handling.py:459`.
- Timeout handling for `manage_drafts` list is locked: `tests/test_modernization_3_1_5.py:476`.

## Proposed Helpers

Add these first, near `_build_draft_lookup()` or directly above `manage_drafts`:

```python
def _build_manage_drafts_subject_filter_script(subject_contains: str | None, *, indent: int) -> str:
    ...

def _build_manage_drafts_list_script(
    *,
    safe_account: str,
    list_limit: int,
    hide_empty: bool,
    subject_contains: str | None,
) -> str:
    ...

def _build_manage_drafts_find_script(
    *,
    safe_account: str,
    list_limit: int,
    in_reply_to: str,
    subject_contains: str | None,
) -> str:
    ...
```

Then, in a later pass only if the first pass is green:

```python
def _build_manage_drafts_create_script(
    *,
    safe_account: str,
    escaped_subject: str,
    escaped_body: str,
    safe_to: str,
    sender_script: str,
    to_script: str,
    cc_script: str,
    bcc_script: str,
) -> str:
    ...

def _build_manage_drafts_action_lookup(
    *,
    draft_id: str | None,
    draft_subject: str | None,
) -> tuple[str | None, str, str | None]:
    ...

def _build_manage_drafts_send_open_delete_script(
    *,
    safe_account: str,
    action: str,
    lookup_script: str,
    not_found_text: str,
) -> str:
    ...

def _build_manage_drafts_cleanup_empty_script(
    *,
    safe_account: str,
    dry_run: bool,
    max_deletes: int,
) -> str:
    ...
```

The first helper should return only the small `ignoring case` block. Keep `escape_applescript()` inside that helper so both list and find share identical escaping and indentation. Use `indent` only to preserve existing script formatting for snapshot stability.

## Sequencing

1. Add `_build_manage_drafts_subject_filter_script()`.
2. Add `_build_manage_drafts_list_script()` with byte-for-byte equivalent AppleScript where practical.
3. Replace only the `action == "list"` branch body with `script = _build_manage_drafts_list_script(...)`.
4. Add direct unit tests for the list builder output, including cap, no date filter, and no raw Drafts enumeration.
5. Add `_build_manage_drafts_find_script()` and replace only the `find` branch body.
6. Add direct unit tests for the find builder output, including header parsing, optional subject filter, cap, and no raw Drafts enumeration.
7. Run the targeted verification below.
8. Only after that, consider extracting `create`.
9. Extract `send`, `open`, and `delete` together only after moving `_draft_action_lookup()` to module scope, because exact id preference is shared.
10. Extract `cleanup_empty` last, since it is destructive when `dry_run=False` and should keep its validation and dry-run defaults visually close during review.

## Safety Implications

Draft-safe send blocking:

- Keep `_server.READ_ONLY` and `_server.DRAFT_SAFE` checks in the public `manage_drafts()` dispatcher, before any send script builder call. Current code blocks at `plugin/apple_mail_mcp/tools/compose.py:2896`.
- Do not hide this gate inside `_build_manage_drafts_send_open_delete_script()`. Builders should build scripts, not decide whether sending is allowed.

Exact `draft_id` behavior:

- Keep `normalize_message_ids([draft_id])` validation before script execution. Current code rejects invalid ids before AppleScript at `plugin/apple_mail_mcp/tools/compose.py:2605`.
- Preserve id-first behavior over `draft_subject`. Existing tests assert no `contains "Duplicate Subject"` appears when `draft_id` is present: `tests/test_compose_tools.py:1752`.
- Id-only `every message of draftsMailbox whose id is N` is allowed by the static lint contract: `tests/test_no_unbounded_whose.py:46`.

Limits:

- Keep `list_limit = DRAFT_LIST_CAP if limit is None else max(1, min(int(limit), DRAFT_LIST_CAP))` in the dispatcher or a pure helper that is tested separately. Current line: `plugin/apple_mail_mcp/tools/compose.py:2602`.
- Do not let builder helpers accept raw `limit`. Accept the already-clamped `list_limit` so every script path uses the same cap.
- Keep `cleanup_empty` using `max_deletes` as an action cap, not as a scan cap. It scans the fixed Drafts cap and limits actual acted deletes separately: `plugin/apple_mail_mcp/tools/compose.py:3029`.

Unbounded scans:

- `list` and `find` builders must continue to emit `messages 1 thru headEnd of draftsMailbox` and must not emit raw `every message of draftsMailbox`.
- Subject filtering must remain an in-loop `ignoring case` block, not a `whose subject contains` clause.
- Header matching for `find` must remain in-loop over the bounded Drafts slice.
- After extraction, `tests/test_no_unbounded_whose.py` is mandatory because moving strings can create static lint changes even when runtime behavior looks unchanged.

## Risks And Mitigations

- Risk: changing output text breaks agent workflows that parse `Found`, `Id`, `To`, `Draft ID`, or cleanup summaries. Mitigation: keep first-pass builders output-equivalent and add focused script-output snapshot assertions.
- Risk: moving `subject_contains` escaping into a helper changes indentation or omits `ignoring case`. Mitigation: direct builder tests for list and find subject filters.
- Risk: extracting send/open/delete too early weakens exact-id preference. Mitigation: defer these until `_build_manage_drafts_action_lookup()` is module-scoped and directly tested.
- Risk: moving draft-safe blocking into script builders makes it easier to bypass. Mitigation: leave read-only and draft-safe gates in `manage_drafts()` dispatcher.
- Risk: `cleanup_empty` currently uses `messages 1 thru 75` without checking zero Drafts. If it is extracted later, a safe follow-up may need the same `totalDrafts/headEnd` empty-folder guard as list/find. Do not mix that behavior change into the first builder extraction.

## Verification Commands

```bash
.venv/bin/pytest tests/test_compose_tools.py::ManageDraftsCreateSenderOverrideTests tests/test_compose_tools.py::ManageDraftsListTests -q
.venv/bin/pytest tests/test_phase_2_scan_hardening.py::ComposeScanCapTests tests/test_no_unbounded_whose.py -q
.venv/bin/pytest tests/test_compose_none_handling.py tests/test_modernization_3_1_5.py -q
.venv/bin/pytest tests/test_read_only_registry.py tests/test_validate_manifests.py -q
```

If later extracting mutating branches, add:

```bash
.venv/bin/pytest tests/test_compose_tools.py -q
.venv/bin/pytest tests/ -q
```
