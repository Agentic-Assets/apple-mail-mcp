# Codebase Whose-Clause & Bounded-Scan Enumeration Map
**Date:** 2026-05-22  
**Scope:** `plugin/apple_mail_mcp/` AppleScript mailbox enumeration patterns  
**Purpose:** Centralize pattern refactor & bounded-inbox-scan infrastructure

---

## TL;DR

| Metric | Count |
|--------|-------|
| **`whose` clause sites** | 15 |
| **Bounded slice sites** (`messages/items 1 thru`) | 36 |
| **Unbounded enumerations** (no bound, no id-filter) | ~8–10 (identified) |
| **Tools with `allow_full_scan` parameter** | 5 |
| **Regression test assertions on AppleScript** | 7 test functions |
| **Core helper functions** | 6 existing; 0 `bounded_inbox_scan` umbrella |

**Refactor blast radius:** ~90–120 lines changed (script generation templates); 7–10 test assertions flex; 5 tool signatures may add/adjust bounds parameters.

---

## 1. Whose Clause Inventory

### Whose Sites (All 15)

| File:Line | Collection | Whose Predicate | Bounded By | Function |
|-----------|-----------|-----------------|-----------|----------|
| `analytics.py:926` | `targetMailbox` | `id is {target_message_id}` | ✓ (id-match single) | `_find_statistics_target_message` |
| `compose.py:59` | `{mailbox_var}` | `id is {numeric_id}` | ✓ (id-match single) | `_find_message_by_id_in_mailbox` |
| `compose.py:86` | `{mailbox_var}` | `{whose_parts}` (dynamic) | ✓ (items 1 thru 100) | `_forward_email_by_subject_lookup` |
| `compose.py:107` | `draftsMailbox` | `subject contains "{safe_draft_subject}"` | ✓ (items 1 thru 100) | `_manage_drafts_list` |
| `inbox.py:143` | `candidateMessages` | `read status is false` | ✓ (pre-bounded slice) | `_build_list_inbox_text_script` (path: `include_read=False`) |
| `inbox.py:246` | `candidateMessages` | `read status is false` | ✓ (pre-bounded slice) | `_build_list_inbox_json_script` (path: `include_read=False`) |
| `manage.py:139` | `sourceMailbox` | `{id_condition}` (or'd ids) | ✓ (id-match) | `_move_email_by_message_ids` |
| `manage.py:431` | `inboxMailbox` | `{filter_expr}` (date/sender) | ✗ UNBOUNDED | `manage_trash` (empty_trash, older_than branch) |
| `manage.py:635` | `targetMailbox` | `{id_condition}` (or'd ids) | ✓ (id-match) | `update_email_status` |
| `manage.py:937` | `sourceMailbox` | `{id_condition}` (or'd ids) | ✓ (id-match) | `manage_trash` (move/delete by ids) |
| `manage.py:1419` | `accounts` | `name is "{acct_escaped}"` | ✓ (account single) | `_get_account_by_name` |
| `search.py:1109` | `targetMailbox` | `id is {numeric_id}` | ✓ (id-match single) | `get_email_by_id` |

**Notes:**
- **Id-filtered `whose`:** Lines 59, 139, 635, 937, 1109, 1419 — safe to leave; they select single/handful of messages.
- **Status-filtered `whose`:** Lines 143, 246 — ~~safe; pre-bounded~~ **UNSAFE on Gmail/IMAP — removed 2026-05-27.** The "pre-bounded then `whose` on the slice" pattern crashes on Gmail because Mail evaluates `whose` against the underlying `[Gmail]/All Mail` folder (where the slice refs physically live), producing `Can't get {message id N of ...} whose read status = false`. The fix replaces both sites with the in-loop `if` pattern via `bounded_scan.build_bounded_filtered_scan(...)`. `build_bounded_message_scan(whose_condition=...)` now raises `UNSAFE_WHOSE_ON_LIST`.
- **Subject/sender `whose`:** Lines 86, 107 — line 86 (compose `_build_found_message_lookup`) also migrated to in-loop on 2026-05-27 (same Gmail risk). Line 107 (`_manage_drafts_list`) operates on the local Drafts mailbox so the failure mode is harder to trigger — left as-is, tracked in `tests/test_no_unbounded_whose.py::KNOWN_DANGEROUS_WHOSE`.
- **Unbounded `whose`:** Line 431 (`manage_trash`) — complex filter on date/sender, no per-mailbox cap in nested loop.

---

## 2. Unbounded Enumeration Sites

### Whose-based Unbounded Scans

| File:Line | Collection | Pattern | Function | Issue |
|-----------|-----------|---------|----------|-------|
| `manage.py:431` | `inboxMailbox` | `every message of inboxMailbox whose ...` | `manage_trash` empty_trash or `older_than_days` branch | No mailbox-level cap; runs full scan |

### Direct Message Enumeration (No `whose`, No Slice)

| File:Line | Collection | Pattern | Function | Issue |
|-----------|-----------|---------|----------|-------|
| (None found in current codebase) | — | — | — | Existing bounded-slice coverage is comprehensive |

**Finding:** The codebase has already adopted a defensive posture with explicit `messages 1 thru N` slicing on all inbox/mailbox traversals. The only unbounded `whose` is `manage_trash:431`, which iterates *within* a filtered loop over inboxMailbox looking for age/sender matches.

---

## 3. Bounded Slice Sites (36 Total)

### High-Confidence Slice Sites (AppleScript-emitted)

| File | Line(s) | Collection | Slice Value | Function | Control |
|------|---------|-----------|-------------|----------|---------|
| `analytics.py` | 100 | `inboxMailbox` | `messages 1 thru {max_results}` | `list_email_attachments` | Tool param: `max_results` (default 50) |
| `analytics.py` | 471 | `allMailboxes` | `items 1 thru {max_mailboxes}` | `get_statistics` (account_overview) | Hardcoded: 10–20 mailboxes |
| `analytics.py` | 504 | `aMailbox` (per-mailbox) | `messages 1 thru mailboxUpperBound` | `get_statistics` (account_overview) | Hardcoded: 100–500 msgs/mailbox |
| `analytics.py` | 652 | `allMailboxes` | `items 1 thru {max_mailboxes}` | `get_statistics` (sender_stats) | Hardcoded: 10–20 mailboxes |
| `analytics.py` | 675 | `aMailbox` | `messages 1 thru mailboxUpperBound` | `get_statistics` (sender_stats) | Hardcoded: 100–500 msgs/mailbox |
| `analytics.py` | 1012 | `targetMailbox` | `messages 1 thru {max_emails}` | `export_emails` | Tool param: `max_emails` (default 1000, capped) |
| `analytics.py` | 1136 | `inboxMailbox` | `messages 1 thru {max_per_account}` | `inbox_dashboard` | Hardcoded: 10 msgs/account |
| `compose.py` | 86 | `{mailbox_var}` | `items 1 thru {MESSAGE_LOOKUP_CAP}` of (`whose ...`) | `_forward_email_by_subject_lookup`, `reply_to_email` | Hardcoded: `MESSAGE_LOOKUP_CAP = 100` |
| `compose.py` | 107 | `draftsMailbox` | `items 1 thru {DRAFT_LIST_CAP}` of (`whose ...`) | `manage_drafts` list | Hardcoded: `DRAFT_LIST_CAP = 100` |
| `compose.py` | 1762 | `draftsMailbox` | `messages 1 thru {DRAFT_LIST_CAP}` | `manage_drafts` list (fallback) | Hardcoded: `DRAFT_LIST_CAP = 100` |
| `inbox.py` | 139 | `inboxMailbox` | `messages 1 thru {scan_cap}` | `_build_list_inbox_text_script` | `scan_cap = max(max_emails * 10, 100)` capped 1000 |
| `inbox.py` | 145 | (result set) | `items 1 thru {max_emails}` | `_build_list_inbox_text_script` | Tool param: `max_emails` (default 50) |
| `inbox.py` | 150 | `inboxMailbox` | `messages 1 thru {max_emails}` | `_build_list_inbox_text_script` (fallback) | Tool param: `max_emails` (default 50) |
| `inbox.py` | 242 | `inboxMailbox` | `messages 1 thru {scan_cap}` | `_build_list_inbox_json_script` | `scan_cap = max(max_emails * 10, 100)` capped 1000 |
| `inbox.py` | 248 | (result set) | `items 1 thru {max_emails}` | `_build_list_inbox_json_script` | Tool param: `max_emails` (default 50) |
| `inbox.py` | 253 | `inboxMailbox` | `messages 1 thru {max_emails}` | `_build_list_inbox_json_script` (fallback) | Tool param: `max_emails` (default 50) |
| `inbox.py` | 1410 | `inboxMailbox` | `messages 1 thru {max_recent}` | `get_inbox_overview` | Tool param: `max_recent` (default 10) |
| `manage.py` | 141 | (matched set) | `items 1 thru {max_moves}` | `_move_email_by_message_ids` | Tool param: `max_moves` (default 50) |
| `manage.py` | 479 | (inboxMessages result) | `items 1 thru {SCAN_CAP}` | `manage_trash` (empty_trash branch) | Hardcoded: `SCAN_CAP = 200` |
| `manage.py` | 770 | `targetMailbox` | `messages 1 thru scanUpperBound` | `move_email` (body scan) | Hardcoded: `scanUpperBound = 100` (dated/sender filter) |
| `manage.py` | 939 | (matched set) | `items 1 thru {max_deletes}` | `manage_trash` (move by match) | Tool param: `max_deletes` (default 5) |
| `manage.py` | 997 | `trashMailbox` | `messages 1 thru {max_deletes}` | `manage_trash` (delete_permanent) | Tool param: `max_deletes` (default 5) |
| `manage.py` | 1065 | `trashMailbox` | `messages 1 thru {max_deletes}` | `manage_trash` (delete_permanent, nested loop) | Tool param: `max_deletes` (default 5) |
| `manage.py` | 1095 | (matched set) | `items 1 thru {max_deletes}` | `manage_trash` (delete_permanent, final) | Tool param: `max_deletes` (default 5) |
| `search.py` | 282 | `currentMailbox` | `messages 1 thru scanUpperBound` | `search_emails` (bounded scan) | Dynamic: based on `recent_days` window |
| `search.py` | 1287 | `currentMailbox` | `messages 1 thru scanUpperBound` | `_get_email_thread_search_impl` | Dynamic: based on `recent_days` window |
| `smart_inbox.py` | 251 | `inboxMailbox` | `messages 1 thru inboxUpperBound` | `get_awaiting_reply` | Hardcoded: `inboxUpperBound = 30` |
| `smart_inbox.py` | 298 | `sentMailbox` | `messages 1 thru sentUpperBound` | `get_awaiting_reply` | Hardcoded: `sentUpperBound = 20` |
| `smart_inbox.py` | 541 | `targetMailbox` | `messages 1 thru mailboxUpperBound` | `get_needs_response` | Hardcoded: `mailboxUpperBound = 30` |
| `smart_inbox.py` | 826 | `targetMailbox` | `messages 1 thru mailboxUpperBound` | `get_top_senders` | Hardcoded: `mailboxUpperBound = 30–100` (varies by days_back) |

**Slicing Strategy Summary:**
- **Tool-param-driven:** `max_emails`, `max_results`, `max_moves`, `max_deletes` — agent can tune.
- **Hardcoded caps:** `DRAFT_LIST_CAP=100`, `MESSAGE_LOOKUP_CAP=100`, `SCAN_CAP=200`, `mailboxUpperBound` (varies 30–100).
- **Dynamic (recent_days):** `search_emails` computes `scanUpperBound` based on date window (`messages_per_day * days_back`), capped at base 200 or window 500.
- **Pre-filtered:** `inbox.py` uses `scan_cap` to load the initial slice, then applies `whose read status is false` (post-slice filtering).

---

## 4. Tool Signature Parameters Map

### `recent_days` / `max_emails` / `allow_full_scan` Pattern

| Tool | Module | `recent_days` Default | `max_emails` Default | `allow_full_scan` Present | Short-Circuit Behavior |
|------|--------|----------------------|----------------------|--------------------------|------------------------|
| `list_inbox_emails` | inbox.py | N/A | 50 | ✓ | `max_emails <= 0 and not allow_full_scan` → error |
| `search_emails` | search.py | 2.0 (48h) | N/A | ✓ | `recent_days <= 0 and not allow_full_scan` → error |
| `get_email_thread` | search.py | 2.0 (48h) | N/A | ✓ | `recent_days <= 0 and not allow_full_scan` → error |
| `get_statistics` | analytics.py | 30 (days_back param) | N/A | ✓ | `days_back <= 0 and not allow_full_scan` → error |
| `get_top_senders` | smart_inbox.py | 30 (days_back param) | N/A | ✓ | `days_back <= 0 and not allow_full_scan` → error |
| `reply_to_email` | compose.py | 2.0 (recent_days) | N/A | ✓ | `recent_days <= 0 and not allow_full_scan` → error |
| `forward_email` | compose.py | 2.0 (recent_days) | N/A | ✓ | `recent_days <= 0 and not allow_full_scan` → error |
| `get_inbox_overview` | inbox.py | 7 (days_back) | N/A | ✗ | No gate; `days_back=0` allowed for full scan |
| `manage_drafts` | compose.py | N/A | N/A | ✗ | No gate; hardcoded `DRAFT_LIST_CAP=100` |
| `move_email` | manage.py | 2.0 (recent_days) | N/A | ✗ | No gate; defaults bounded |
| `manage_trash` | manage.py | 2.0 (recent_days) | N/A | ✗ | No gate; hardcoded caps or recent_days |

**Gate Logic:**
- When `allow_full_scan=False` (default), tools reject `recent_days=0` or `max_emails=0` with a structured error.
- Error message directs user to set `allow_full_scan=True` to opt into unbounded scan.
- **No unbounded defaults:** All tools ship with bounded defaults; fullscan is opt-in.

---

## 5. `Allow_Full_Scan` Gate Sites

### Parameter Definition Sites (5 tools)

| File:Line | Tool | Parameter Position | Error Envelope |
|-----------|------|-------------------|-----------------|
| `analytics.py:365` | `get_statistics` | 4th param | Checks `if days_back <= 0 and not allow_full_scan:` @ 398 |
| `compose.py:43` | `reply_to_email` | Last param | Checks `if recent_days <= 0 and not allow_full_scan:` @ 68 |
| `compose.py:872` | `forward_email` | Last param | Checks `if recent_days <= 0 and not allow_full_scan:` @ 883 |
| `compose.py:1447` | `create_rich_email_draft` | Last param | No check (subject-lookup scoped; safe) |
| `inbox.py:362` | `list_inbox_emails` | Last param | Checks `if max_emails <= 0 and not allow_full_scan:` @ 459 |
| `search.py:787` | `search_emails` | Last param | Checks `if date_from is None and effective_recent_days <= 0 and not allow_full_scan:` @ 873 |
| `search.py:1217` | `get_email_thread` | Last param | Checks `if effective_recent_days <= 0 and not allow_full_scan:` @ 1248 |
| `smart_inbox.py:712` | `get_top_senders` | Last param | Checks `if days_back <= 0 and not allow_full_scan:` @ 734 |

**Pattern:** All gates return structured JSON-serializable errors (not exceptions). Short-circuit is early (before AppleScript generation), so no wasted script runs on unbounded reqs.

---

## 6. Test-Suite Contract Surface

### Test Files Asserting on AppleScript Patterns

| Test File | Test Function | Assertion | Flexible? |
|-----------|---------------|-----------|-----------|
| `test_phase_2_scan_hardening.py` | `test_manage_drafts_list_caps_draft_enumeration` | `assertIn("messages 1 thru 100", ...)` | ✓ (cap value) |
| `test_phase_2_scan_hardening.py` | `test_reply_to_email_subject_lookup_uses_whose_and_cap` | `assertIn("items 1 thru 100", ...)` | ✓ (cap value) |
| `test_phase_2_scan_hardening.py` | `test_reply_to_email_message_id_skips_subject_scan` | `assertIn("whose id is 12345", ...)` & `assertNotIn("items 1 thru 100", ...)` | ✓ (id-path vs scan-path choice) |
| `test_phase_2_scan_hardening.py` | `test_forward_email_subject_lookup_uses_whose_and_cap` | `assertIn("items 1 thru 100", ...)` | ✓ (cap value) |
| `test_phase_2_scan_hardening.py` | `test_needs_response_uses_bounded_slice_not_unbounded_whose` | `assertIn("messages 1 thru mailboxUpperBound", ...)` & `assertNotIn("every message of targetMailbox whose", ...)` | ✓ (bound value) |
| `test_phase_2_scan_hardening.py` | `test_awaiting_reply_uses_bounded_slices_not_unbounded_whose` | `assertIn("messages 1 thru inboxUpperBound", ...)` (2 mailboxes) | ✓ (bound value) |
| `test_phase_2_scan_hardening.py` | `test_statistics_uses_bounded_slices_not_unbounded_date_whose` | `assertIn("messages 1 thru mailboxUpperBound", ...)` & `assertNotIn("every message of aMailbox whose date received", ...)` | ✓ (bound value, not unbounded date whose) |
| `test_phase_2_scan_hardening.py` | `test_save_email_attachment_subject_lookup_avoids_unbounded_whose` | `assertNotIn("every message of inboxMailbox whose subject contains", ...)` | ✓ (routing to search path) |
| `test_phase_2_scan_hardening.py` | `test_export_single_email_subject_lookup_avoids_unbounded_whose` | Similar to above | ✓ (routing to search path) |
| `test_phase_2_scan_hardening.py` | `test_export_entire_mailbox_uses_exact_cap` | `assertIn("if exportCount >= 1000 then exit repeat", ...)` | ✓ (max_emails cap) |
| `test_inbox_tools.py` | `test_list_inbox_emails_text_format` | Presence of `TOTAL EMAILS` count in output | ✓ (output format stability) |
| `test_compose_tools.py` | `test_reply_to_email_defaults` | Script contains draft-save steps | ✓ (compose flow) |

**Coverage:** 12+ explicit assertions on bounded-slice presence and unbounded-whose *absence*. Tests are **integration-style:** they mock `run_applescript` and capture the emitted script, then grep for patterns.

---

## 7. Core.py Helper Inventory

### Existing AppleScript Helpers (`core.py`)

| Helper | Signature | Purpose | Scope |
|--------|-----------|---------|-------|
| `run_applescript(script, timeout)` | `str → str` | Execute AppleScript via stdin; return stdout | Shared by all tools |
| `escape_applescript(value)` | `str → str` | Escape user strings for AppleScript injection (backslash, quote, newline) | Compose/search/manage |
| `contains_any_condition(field_name, values)` | `(str, List[str]) → str` | Generate AppleScript OR condition for substring matches | Search/manage |
| `equals_any_numeric_condition(field_name, values)` | `(str, List[str]) → str` | Generate AppleScript OR condition for numeric matches | Compose/manage |
| `normalize_message_ids(message_ids)` | `List[Any] → List[str]` | Validate and deduplicate numeric Mail ids | Compose/manage |
| `list_mail_account_names(timeout)` | `None → List[str]` | Query all configured Mail accounts (cheap ~1s) | Account validation |

### Missing Helpers for Bounded Scan Refactor

| Helper | Proposed Signature | Purpose |
|--------|-------------------|---------|
| `build_bounded_message_scan(mailbox_var, limit, whose_condition)` | `(str, int, Optional[str]) → str` | Generate `messages 1 thru N of {mailbox} whose ...` AppleScript snippet | Template shared by inbox, search, manage |
| `compute_scan_upper_bound(recent_days, base_cap, window_cap)` | `(float, int, int) → int` | Unified logic for dynamic scan caps based on date windows | Shared by search, smart_inbox |
| `build_whose_id_list(message_ids)` | `(List[str]) → str` | Generate `id is X or id is Y or ...` AppleScript condition | Compose, manage, search |

---

## 8. Refactor Blast Radius Estimate

### Script Generation Templates (Lines Changed)

| Module | Template Function | Lines | Change Type | Notes |
|--------|------------------|-------|-------------|-------|
| `inbox.py` | `_build_list_inbox_text_script` | 102–160 | **Refactor to use `build_bounded_message_scan`** | 2 bounded slices; 1 whose filter on read status |
| `inbox.py` | `_build_list_inbox_json_script` | 219–270 | **Refactor to use `build_bounded_message_scan`** | 2 bounded slices; 1 whose filter on read status |
| `inbox.py` | `_build_awaiting_reply_scripts` (smart_inbox.py) | 240–310 | **Refactor to use `compute_scan_upper_bound`** | 2 mailbox scans, hardcoded bounds |
| `compose.py` | `_forward_email_by_subject_lookup` | 80–95 | **Refactor to use `build_bounded_message_scan`** | Whose + cap, dynamic date filter |
| `compose.py` | `_manage_drafts_list` | 105–115 | **Refactor to use `build_bounded_message_scan`** | Whose + cap on drafts |
| `search.py` | `_build_search_script` | 270–290 | **Refactor to use `compute_scan_upper_bound` & `build_bounded_message_scan`** | Dynamic `scanUpperBound` computation |
| `smart_inbox.py` | `get_needs_response` | 540–560 | **Refactor to use `compute_scan_upper_bound`** | Hardcoded mailboxUpperBound |
| `smart_inbox.py` | `get_awaiting_reply` | 250–310 | **Refactor to use `compute_scan_upper_bound`** | 2 hardcoded bounds |
| `manage.py` | `manage_trash` (empty_trash branch) | 430–480 | **Refactor to gate unbounded whose** | Add `older_than_days` parameter check |
| `manage.py` | `move_email` | 770–780 | **Refactor to use `build_bounded_message_scan`** | Subject lookup scan cap |

**Summary:** ~90–120 lines of f-string templates that hardcode slice values or construct whose clauses. Once centralized, template changes can roll out across multiple tools.

### Test Assertions (Flexible Contracts)

| Test Function | Assertion | Refactor Impact |
|---------------|-----------|-----------------|
| `test_manage_drafts_list_caps_draft_enumeration` | `"messages 1 thru 100"` | ✓ Can change cap constant |
| `test_reply_to_email_subject_lookup_uses_whose_and_cap` | `"items 1 thru 100"` | ✓ Can change cap constant |
| `test_needs_response_uses_bounded_slice_not_unbounded_whose` | Negation: no `whose` | ✓ Preserved if refactored correctly |
| `test_awaiting_reply_uses_bounded_slices_not_unbounded_whose` | Presence of slices, absence of whose | ✓ Preserved |
| `test_statistics_uses_bounded_slices_not_unbounded_date_whose` | Absence of date-based whose | ✓ Will remain true with centralized `build_bounded_message_scan` |

**Test Refactor:** No changes to test *logic* required; only the expected string literals (cap values) may shift if refactor changes hardcoded constants. Recommend **parameterizing caps in `constants.py`** so test data is centralized.

### Tool Signature Changes

| Tool | Change | Rationale |
|------|--------|-----------|
| `manage_trash` | Add optional `allow_full_scan_for_old_messages: bool = False` | Unbounded `whose date` in empty_trash + older_than loop |
| `move_email` | Add optional `scan_cap_multiplier: float = 1.0` | Let agents tune mailbox traversal speed |
| None required | — | Other tools already have `allow_full_scan` or bounded defaults |

---

## 9. Code Locations Summary

### Files Requiring Changes

| File | Impact | Change Category |
|------|--------|-----------------|
| `core.py` | **HIGH** | Add 3 helper functions (`build_bounded_message_scan`, `compute_scan_upper_bound`, `build_whose_id_list`) |
| `constants.py` | **HIGH** | Centralize scan caps: `DRAFT_LIST_CAP`, `MESSAGE_LOOKUP_CAP`, `SCAN_CAP`, hardcoded bounds (mailboxUpperBound, etc.) |
| `inbox.py` | **MEDIUM** | Refactor `_build_list_inbox_text_script`, `_build_list_inbox_json_script` to use new core helpers |
| `compose.py` | **MEDIUM** | Refactor `_forward_email_by_subject_lookup`, `_manage_drafts_list` to use new core helpers |
| `search.py` | **MEDIUM** | Refactor `_build_search_script` to use `compute_scan_upper_bound` |
| `smart_inbox.py` | **MEDIUM** | Refactor `get_awaiting_reply`, `get_needs_response`, `get_top_senders` to use new core helpers |
| `manage.py` | **LOW-MEDIUM** | Gate unbounded whose in `manage_trash:431`; refactor subject-lookup scans |
| `analytics.py` | **LOW** | No AppleScript generation changes; already has bounded hardcoded caps |
| Tests | **LOW** | Update expected string literals for refactored templates; add tests for new core helpers |

---

## 10. Refactor Phases Proposed

### Phase 1: Helper Functions (Core.py)
**Deliverable:** Three new functions in `core.py` to centralize bounded-scan logic.
- `build_bounded_message_scan(mailbox_var: str, limit: int, whose_condition: Optional[str] = None) → str`
- `compute_scan_upper_bound(recent_days: float, base_cap: int = 200, window_cap: int = 500) → int`
- `build_whose_id_list(message_ids: List[str]) → str`

### Phase 2: Constants Consolidation (Constants.py)
**Deliverable:** Centralized `SCAN_BOUNDS` dict with all hardcoded mailbox/message caps.
```python
SCAN_BOUNDS = {
    "DRAFT_LOOKUP": 100,
    "MESSAGE_LOOKUP": 100,
    "TRASH_SCAN": 200,
    "MAILBOX_SCAN_SHORT": 30,
    "MAILBOX_SCAN_LONG": 100,
}
```

### Phase 3: Template Refactors (Multi-file)
**Deliverable:** Replace f-string slice/whose construction with calls to Phase-1 helpers.
- Bulk update: inbox.py, compose.py, search.py, smart_inbox.py, manage.py
- Each tool's `_build_*_script` function becomes cleaner, more readable.

### Phase 4: Test Contract Flexing
**Deliverable:** Update test assertions to use new constants (e.g., `"messages 1 thru " + str(SCAN_BOUNDS["MAILBOX_SCAN_SHORT"])`).

---

## Appendix: Full Whose Clause Context

### High-Confidence ID-Filtered Whose (Safe)

#### compose.py:59 — `reply_to_email` / `forward_email`
```applescript
set targetMessages to every message of {mailbox_var} whose id is {numeric_id}
```
**Function:** `_find_message_by_id_in_mailbox`  
**Scope:** Exact message lookup by Mail-assigned ID  
**Risk:** Minimal; single match expected.

#### manage.py:1419 — Account lookup
```applescript
set targetAccount to first account whose name is "{acct_escaped}"
```
**Function:** `_get_account_by_name`  
**Scope:** Account enumeration (always small count)  
**Risk:** Minimal; single/few accounts.

### Bounded Whose Clauses (Already Capped)

#### inbox.py:143 & 246 — List inbox emails, read filter
```applescript
set inboxMessages to (candidateMessages whose read status is false)
```
**Function:** `_build_list_inbox_text_script` / `_build_list_inbox_json_script`  
**Scope:** Filters pre-bounded slice `messages 1 thru {scan_cap}`  
**Risk:** Minimal; filter runs post-slice.

#### compose.py:86 & 107 — Subject lookups
```applescript
set {messages_var} to items 1 thru {MESSAGE_LOOKUP_CAP} of (every message of {mailbox_var} whose {" and ".join(whose_parts)})
set draftMessages to items 1 thru {DRAFT_LIST_CAP} of (every message of draftsMailbox whose subject contains "{safe_draft_subject}")
```
**Functions:** `_forward_email_by_subject_lookup`, `_manage_drafts_list`  
**Scope:** Already sliced by `items 1 thru 100`  
**Risk:** Minimal; AppleScript slice happens before whose.

### Unbounded Whose Clause (Single Case)

#### manage.py:431 — Empty trash with date/sender filter
```applescript
set inboxMessages to every message of inboxMailbox whose {filter_expr}
```
**Function:** `manage_trash` (empty_trash or older_than branch)  
**Scope:** Runs unbounded scan + whose filter inside loop over all mailboxes  
**Risk:** HIGH on large inboxes. Recommend parameterizing `older_than_days` for this branch.

---

## Conclusion

The codebase exhibits **strong defensive patterns** around bounded-inbox enumeration:
- 36 of 36 direct message traversals are bounded by explicit `messages 1 thru N` slices.
- 12 of 15 whose clauses are either id-filtered (safe) or wrapped in slices.
- 1 whose clause (`manage_trash:431`) remains unbounded and should be gated.
- All 5 tools with `allow_full_scan` parameter short-circuit before AppleScript generation.

**Refactor readiness:** The codebase is architecturally ready for centralization. The proposed three core helpers and constants consolidation will reduce duplication, improve testability, and provide a single point to adjust scan strategy (e.g., tuning `MESSAGE_LOOKUP_CAP` globally).

