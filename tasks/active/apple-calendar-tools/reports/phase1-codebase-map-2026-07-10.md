# Apple Calendar Tools — Codebase Integration Map (Researcher 2 of 5)

Purpose: give a later implementer everything needed to add Apple Calendar
tools to this repo with zero exploration. All paths are relative to the repo
root (`/Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp`). Every claim
below was verified by reading the actual source on `feat/apple-calendar-tools`
(working tree clean at time of writing).

---

## 1. `plugin/apple_mail_mcp/` package layout and core primitives

### 1.1 Directory shape

```
plugin/apple_mail_mcp/
  __init__.py          # imports the 6 tool-surface packages for side-effect @mcp.tool registration
  __main__.py           # MCP stdio entry point, orphan watcher, --read-only/--draft-safe plumbing
  server.py             # FastMCP instance, ToolAnnotations presets, SEND_TOOLS, env config
  constants.py           # SKIP_FOLDERS, newsletter patterns, TIME_RANGES, SCAN_BOUNDS
  bounded_scan.py         # ScanWindow capability tokens + the only sanctioned AppleScript scan builders
  metadata_index_contract.py
  applescript_snippets.py
  backend/
    __init__.py
    base.py              # ToolError, ScanWindow, WriteResult, MailBackend Protocol, target_selector_deprecated_error
  core/                  # facade package — see 1.2
    __init__.py
    applescript.py       # run_applescript, AppleScriptRunner, AppleScriptTimeout, the single-flight lock
    escaping.py          # escape_applescript, _sanitize_for_json, sanitize_pipe_delimited_field
    normalization.py     # normalize_search_terms, normalize_message_ids, parse_email_list, OR-condition builders
    preferences.py        # @inject_preferences decorator
    replied.py            # Sent-mailbox Message-ID replied-detection scripts
    script_fragments.py   # reusable AppleScript FRAGMENT builders (mailbox refs, date cutoffs, inbox localization)
    validation.py          # account name validation, save-path safety, account_not_found_json
  cli/                   # `apple-mail` CLI subcommands (facade + constants/formatting/parser/perf/draft_smoke/commands)
  tools/                  # the 31 @mcp.tool definitions, 6 packages — see section 2
  ui/                    # dashboard UI (separate top-level plugin/ui, imported as `ui`)
```

Package doc: [`plugin/apple_mail_mcp/CLAUDE.md`](../../../../plugin/apple_mail_mcp/CLAUDE.md).

### 1.2 `core.run_applescript()` — signature, escaping, timeout, single-flight lock

File: `plugin/apple_mail_mcp/core/applescript.py` (71 lines total).

```python
class AppleScriptRunner(Protocol):
    def __call__(self, script: str, timeout: int | None = 120) -> str: ...

class AppleScriptTimeout(Exception): ...

def run_applescript(script: str, timeout: int | None = 120) -> str:
```

Key facts:

- **stdin pipe, not `-e`.** Runs `subprocess.run(["osascript", "-"], input=script.encode("utf-8"), capture_output=True, timeout=effective_timeout)`. This is why multi-line scripts with embedded quotes work reliably — never invoke `osascript -e "<script>"`.
- **`timeout=None` maps to 120s** (`effective_timeout = 120 if timeout is None else timeout`), not "no timeout."
- **Raises `AppleScriptTimeout`** (a plain `Exception` subclass defined in the same module) on `subprocess.TimeoutExpired`, so callers can catch it per-account/per-call without losing sibling results in fan-out code.
- **Single-flight lock.** A module-level `threading.Lock()` (`_MAIL_LOCK`, `_LOCK_WAIT_TIMEOUT = 300`) serializes every `osascript` invocation process-wide. This is deliberate: Mail.app's AppleScript bridge is effectively single-threaded and concurrent invocations thrash it. **A future Calendar backend that shells out to `osascript` directly (bypassing `run_applescript`) would break this guarantee** — Calendar.app automation should either reuse this same lock/function or, if Calendar and Mail truly need independent concurrency, get its own dedicated lock with the same acquire-with-timeout-raise-`AppleScriptTimeout`-style discipline. Reusing `run_applescript` unmodified is the simplest and safest choice unless a strong reason emerges to keep Calendar scripts fully independent of Mail contention.
- **Output sanitization.** stdout is decoded UTF-8 (`errors="replace"`), `.strip()`-ed, then piped through `_sanitize_for_json()` before returning — this strips ASCII control chars (except `\n`/`\t`) so MCP JSON-RPC over stdio never chokes on a raw AppleScript control character.
- **Error surface.** Non-zero return code raises a bare `Exception(f"AppleScript error: {stderr}")` (or `"...exited with code N (no stderr)"`); `subprocess.SubprocessError`/`OSError` wrap into `Exception(f"AppleScript execution failed: {exc}")`. There is no dedicated `AppleScriptError` exception class — only `AppleScriptTimeout` is distinguished. Any calendar-specific error taxonomy should be layered in the tool/backend layer via `ToolError` (see 1.4), not by adding new exception types to `core/applescript.py`.

### 1.3 Escaping helpers

File: `plugin/apple_mail_mcp/core/escaping.py` (75 lines).

- `escape_applescript(value: str) -> str` — escapes `\`, `"`, `\r\n`/`\r`/`\n` → `\n`, `\t` → `\t`, and Unicode ` `/` ` line/paragraph separators. **Every user-controlled string interpolated into an AppleScript double-quoted literal must go through this.** For calendar tools this means event titles, locations, notes/descriptions, attendee names, and any free-text search term.
- `_sanitize_for_json(text: str) -> str` — normalizes `\r\n`/`\r` to `\n`, strips control chars via the compiled `_CONTROL_CHARS_RE`. Called automatically inside `run_applescript`; calendar tools get this for free.
- `sanitize_pipe_delimited_field(var_name: str) -> str` — returns an **AppleScript snippet** (not a Python string transform) that neutralizes `|||` and embedded CR/LF/tab inside an AppleScript variable *before* it is pipe-joined into a row. This exists because the pipe-delimited row protocol (see 1.6) is fragile to a value that itself contains `|||`: a corrupted field shifts every subsequent column, which — for Mail — can silently attach the wrong `message_id` to a delete. **For Calendar, the exact same risk exists** if event UIDs/ids are pipe-joined next to free-text summary/location fields: any calendar row-emission script (e.g. "list events" or "search events") that includes an event title or location as one field followed by the event id as a later field must call `sanitize_pipe_delimited_field("eventSummary")` etc. on every free-text field before the pipe-join, exactly the way `search/script.py`/`inbox/list_scripts.py` do for `messageSubject`.

### 1.4 Structured errors (`ToolError` and helpers)

File: `plugin/apple_mail_mcp/backend/base.py` (221 lines).

```python
class ToolError(Exception):
    def __init__(self, *, code: str, message: str, remediation: dict[str, Any] | None = None) -> None: ...
    def to_dict(self) -> dict[str, Any]: ...  # {"error": True, "code", "message", "remediation"}

def serialize_tool_error(error: ToolError) -> str:
    # json.dumps(error.to_dict(), indent=2)

def target_selector_deprecated_error(
    tool_name: str, selectors: tuple[str, ...], *, preferred: str, discovery: str, exact_selector: str,
) -> str:
    # Returns serialize_tool_error(ToolError(code="TARGET_SELECTOR_DEPRECATED", ...))
```

Every structured tool error a caller ever sees is `json.dumps({"error": True, "code": ..., "message": ..., "remediation": {...}}, indent=2)` — that shape is the contract. Where each code is raised:

| Code | Producer | File |
|------|----------|------|
| `UNBOUNDED_SCAN_REQUIRED` | `bounded_inbox_scan()` (the constructor of `ScanWindow`) | `plugin/apple_mail_mcp/bounded_scan.py:88,97,106,114` |
| `UNBOUNDED_SCAN_REQUIRED` | Per-tool inline refusal (search_emails, list_inbox_emails, move_email, manage_trash, get_top_senders, get_statistics, export_emails, get_email_thread, compose lookup) | `tools/inbox/list_emails.py:198`, `tools/search/emails.py:175`, `tools/search/thread.py:205`, `tools/manage/move.py:248`, `tools/manage/trash.py:211`, `tools/smart_inbox/top_senders.py:98`, `tools/analytics/statistics.py:78`, `tools/analytics/export_helpers.py:40`, `tools/compose/lookup_scripts.py:68` |
| `TARGET_SELECTOR_DEPRECATED` | `target_selector_deprecated_error()` in `backend/base.py`, called from every mutation tool (`move_email`, `update_email_status`, `manage_trash`, `save_email_attachment`, `reply_to_email`, `forward_email`, `manage_drafts`, `export_emails`) when a deprecated selector (`subject_keyword`/`sender`/etc.) is passed without exact ids | `backend/base.py:104` (definition); call sites in every `tools/manage/*.py`, `tools/compose/*.py`, `tools/analytics/export.py` |
| `FILTER_SCAN_DISABLED` | `_filter_scan_disabled_error()` | `plugin/apple_mail_mcp/tools/manage/helpers.py:63` — raised when a mutation tool is called with filters but no exact ids and `allow_filter_scan=False` |
| `BODY_SCAN_DISABLED` | `_body_scan_disabled_error()` | `plugin/apple_mail_mcp/tools/search/records.py:182` — `search_emails(body_text=...)` without `allow_body_scan=True` |
| `INVALID_SCAN_WINDOW` | `bounded_scan.py` constructors (`bounded_inbox_scan`, `build_bounded_message_scan`, `build_bounded_filtered_scan`, `iter_id_chunks`) | `bounded_scan.py` |
| `WHOSE_ID_LIST_TOO_LARGE` | `build_whose_id_list()` when > `MAX_WHOSE_IDS` (50) ids passed | `bounded_scan.py:~220`; also `tools/manage/helpers.py:_check_message_ids_cap` |
| `UNSAFE_WHOSE_ON_LIST` | `build_bounded_message_scan(..., whose_condition=...)` — raised unconditionally, makes the bug unrepresentable | `bounded_scan.py` |

**For Calendar:** reuse `ToolError`/`serialize_tool_error` unchanged (they are Mail-agnostic — nothing in `backend/base.py` references Mail). Do **not** invent a parallel error envelope. If Calendar needs an unbounded-scan refusal (e.g. "list every event ever" without a date range), raise `ToolError(code="UNBOUNDED_SCAN_REQUIRED", ...)` with the same shape, or add a calendar-specific code (e.g. `CALENDAR_UNBOUNDED_SCAN_REQUIRED`) only if the remediation payload genuinely differs; prefer reusing the existing code when the semantics match (unbounded window refusal is unbounded window refusal regardless of app).

### 1.5 `constants.py` `SCAN_BOUNDS`

File: `plugin/apple_mail_mcp/constants.py` (113 lines). Full dict as of this branch:

```python
SCAN_BOUNDS = {
    "DRAFT_LOOKUP": 75,
    "MESSAGE_LOOKUP": 75,
    "TRASH_SCAN": 100,
    "INBOX_SHORT": 25,
    "INBOX_LONG": 75,
    "INBOX_DEFAULT_CAP": 100,
    "INBOX_MAX_CAP": 50,
    "INBOX_HARD_CEILING": 50,
    "SEARCH_BASE_CAP": 40,
    "SEARCH_WINDOW_CAP": 50,
    "SEARCH_DAYS_SCALE": 3,
    "BODY_SEARCH_AUTO_CAP": 25,
    "SEARCH_HARD_CEILING": 50,
    "MAX_MAILBOXES_PER_SEARCH": 20,
    "MAX_MAILBOXES_PER_SEARCH_ALL": 10,
}
```

This dict is Mail-specific (mailbox/message vocabulary) but the *pattern* is the template to copy: a single module-level dict of named integer caps, comment-documented with the real-world incident that motivated the cap (a 9,700-message Exchange inbox blowing wrapper timeouts), consumed by `bounded_scan.compute_scan_upper_bound()` and inline tool checks. **Recommendation:** add a sibling `CALENDAR_SCAN_BOUNDS` dict (or extend `SCAN_BOUNDS` with `CALENDAR_`-prefixed keys) in the same file, e.g. `CALENDAR_EVENT_LOOKUP`, `CALENDAR_SEARCH_WINDOW_CAP`, `CALENDAR_SEARCH_DAYS_SCALE`, `CALENDAR_HARD_CEILING` — do not hardcode calendar magic numbers inline in tool files. Calendar.app's AppleScript store is a local Core Data/EventKit-backed cache, not remote IMAP, so its hard ceiling need not mirror Mail's 50-item Exchange-driven cap, but a ceiling should still exist and be documented with its own reasoning (e.g. very large recurring-event expansions).

### 1.6 JSON-safe AppleScript output parsing pattern

There is **no JSON emission from AppleScript**. AppleScript cannot serialize JSON natively, so the entire codebase uses a **pipe-delimited row protocol**: AppleScript prints `field1|||field2|||field3...` one row per line (with `sanitize_pipe_delimited_field` scrubbing free-text fields first — see 1.3), and Python re-splits on `"|||"` with a **fixed maxsplit** so the *last* field (a long free-text preview) can safely contain literal `|`. Two concrete parsers to copy:

- `plugin/apple_mail_mcp/tools/search/records.py::_parse_search_records()` — splits `line.split("|||", 13)`, requires `len(parts) >= 8`, builds a `dict` with named keys (`message_id`, `internet_message_id`, `subject`, ...), and has a **separate marker-line convention** for embedded per-item errors: lines starting with `ERROR_MAILBOX|||` are diverted into a `mailbox_errors` list instead of being parsed as a record. This "special-prefixed line = out-of-band signal" trick (see also `__COUNT__|||N` in `inbox/parsing.py::_strip_count_marker`) is the standard way to smuggle metadata (counts, per-item errors) through a text-only AppleScript-to-Python channel without a second round-trip.
- `plugin/apple_mail_mcp/tools/inbox/parsing.py::_parse_pipe_delimited_emails()` — same pattern, additionally **defensively validates the id field is `.isdigit()`** before trusting it, specifically because a corrupted pipe-split (e.g. sanitizer bypass) must never let a wrong id reach a destructive downstream call. This defense-in-depth (AppleScript-side sanitize **and** Python-side field-shape validation) is required, not optional, for any calendar row parser whose row includes both a free-text field and an id/UID that will later be passed to an update/delete tool.

For Calendar: event **UIDs are strings** (AppleScript `calendar event`'s `uid` property returns a persistent UUID-like string, unlike Mail's numeric `id`), so the `.isdigit()` guard used for Mail ids does not transfer directly — a calendar row parser needs its own shape check (e.g. non-empty, reasonable length, no embedded `|||`) rather than `isdigit()`.

---

## 2. Tool definition and registration pattern

### 2.1 Registration flow (top to bottom)

1. `plugin/apple_mail_mcp/__init__.py` imports the six tool-surface packages purely for their import side effects:
   ```python
   from apple_mail_mcp.tools import (
       analytics, compose, inbox, manage, search, smart_inbox,
   )
   ```
   Each of those is a **package** (has `tools/<surface>/__init__.py`), not a flat module. Importing the package imports every submodule the package's `__init__.py` re-exports, and each submodule's `@mcp.tool` decorator fires at import time, registering the tool on the shared `mcp` (FastMCP) instance from `server.py`.
2. Each surface package's `__init__.py` is a **facade**: it imports IO/core/server symbols (`run_applescript`, `validate_account_name`, annotation presets, etc.) *first*, then imports each tool submodule (`from apple_mail_mcp.tools.manage.move import move_email`), then re-exports everything in `__all__`. This ordering is deliberate: it makes `apple_mail_mcp.tools.manage.run_applescript` (etc.) a real, patchable module attribute, because `tests/conftest.py`'s autouse fixture and most unit tests patch `apple_mail_mcp.tools.<surface>.<name>`, not the leaf submodule. **A new `calendar` tool package must follow the same facade shape** — see section 8 for the concrete recommendation.
3. `plugin/apple_mail_mcp/tools/__init__.py` itself is **empty** (0 lines besides nothing) — the six surface packages are imported directly by the parent `__init__.py`, `tools/__init__.py` does not re-export them.

### 2.2 Exemplar read tool: `search_emails`

File: `plugin/apple_mail_mcp/tools/search/emails.py` (316 lines, whole file is the single tool plus its docstring).

```python
@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
@inject_preferences
async def search_emails(
    account: str | None = None,
    all_accounts: bool = False,
    mailbox: str = "INBOX",
    ... # 20+ params, all with defaults
    recent_days: float = 2.0,
    output_format: str = "text",
    offset: int = 0,
    limit: int | None = None,
    timeout: int | None = None,
) -> str:
    """<docstring: smart-default behavior, performance guidance, full Args:/Returns: block>"""
```

Pattern to copy:

- **Decorator order matters**: `@mcp.tool(annotations=...)` outermost, `@inject_preferences` inner, directly above `async def`. `inject_preferences` appends `USER_PREFERENCES` (from env) to the tool's `__doc__` at import time — it must wrap the function whose docstring is user-facing.
- **`output_format` validated first**, before any AppleScript I/O: `if output_format not in {"text", "json"}: return "Error: ..."`. This is the universal opening guard on every dual-format tool.
- **Unbounded-scan refusal happens before any network/AppleScript call**: `search_emails` computes `effective_recent_days`, and if `date_from is None and effective_recent_days <= 0` it returns `serialize_tool_error(ToolError(code="UNBOUNDED_SCAN_REQUIRED", ...))` immediately — this refusal fires **even when `output_format="text"`**, deliberately breaking format symmetry so the caller always gets the parseable JSON envelope for a refusal (comment at emails.py:184-186 explains this explicitly).
- **`DEFAULT_MAIL_ACCOUNT` is read lazily** off the `server` module (`from apple_mail_mcp import server as _server`, then `_server.DEFAULT_MAIL_ACCOUNT` read inside the function body), never captured as a constant at import time — tests monkeypatch `apple_mail_mcp.server.DEFAULT_MAIL_ACCOUNT` after import, and a captured-at-import value would never see that patch.
- **Account validation** happens through the package facade: `search.validate_account_name(account, timeout=...)` (not a direct `core.validate_account_name` import) so `conftest.py`'s autouse patch (`monkeypatch.setattr("apple_mail_mcp.tools.search.validate_account_name", _validate)`) intercepts it.
- **Return type is always `str`** — every tool returns a plain string (either formatted text or a JSON string via `json.dumps`), never a Python dict/object. FastMCP serializes the string as the tool result content.
- **Docstring is the tool's on-the-wire schema documentation** — the full `Args:`/`Returns:` block above is what the MCP client (and any downstream agent) actually reads to decide how to call the tool; write it as carefully as the code.

### 2.3 Exemplar mutation tool: `move_email`

File: `plugin/apple_mail_mcp/tools/manage/move.py` (325 lines: one small private id-mover helper `_move_email_by_message_ids`, then the `@mcp.tool` itself).

```python
@mcp.tool(annotations=WRITE_TOOL_ANNOTATIONS)
@inject_preferences
def move_email(
    account: str | None = None,
    to_mailbox: str = "",
    message_ids: list[str] | None = None,
    subject_keyword: str | None = None,   # deprecated selector
    from_mailbox: str = "INBOX",
    max_moves: int = 50,
    dry_run: bool = False,
    allow_filter_scan: bool = False,
    timeout: int | None = None,
) -> str:
```

Pattern to copy (mutation-specific on top of the read-tool pattern):

- **`WRITE_TOOL_ANNOTATIONS`** (not `READ_ONLY_TOOL_ANNOTATIONS`) — see `server.py` for the four presets (`READ_ONLY_TOOL_ANNOTATIONS`, `WRITE_TOOL_ANNOTATIONS`, `IDEMPOTENT_WRITE_TOOL_ANNOTATIONS`, `DESTRUCTIVE_TOOL_ANNOTATIONS`). Pick by MCP `ToolAnnotations` semantics: `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`. A calendar `delete_event` should use `DESTRUCTIVE_TOOL_ANNOTATIONS`; `create_event`/`update_event` should use `WRITE_TOOL_ANNOTATIONS` (non-idempotent create) or `IDEMPOTENT_WRITE_TOOL_ANNOTATIONS` (idempotent update-by-uid).
- **ID-first, filter-scan-gated design**: the function accepts `message_ids` (preferred, fast) OR legacy filter selectors (`subject_keyword`, `sender`, deprecated). If `message_ids is None` and a deprecated selector is present, it returns `target_selector_deprecated_error(...)` immediately — **before any account validation or AppleScript call**. If neither ids nor deprecated selectors nor `older_than_days` are present, it returns a plain-string usage error. If a date/bulk filter path is used, it requires `allow_filter_scan=True` or returns `_filter_scan_disabled_error(...)`.
- **`dry_run: bool`** is a first-class parameter on every destructive tool, always previewed via a separate `_format_dry_run_records()` call, never by adding an `if dry_run` branch deep inside the AppleScript builder.
- **`max_moves` / `max_deletes`-style safety caps** are always present with a conservative default (50 here) and are enforced both in the AppleScript (`if (count of matchingMessages) > {max_moves} then set matchingMessages to items 1 thru {max_moves} ...`) and reflected back in the response text (`"(max_moves limit reached)"`).
- **`AppleScriptTimeout` is always caught locally** and converted into a plain `"Error: ... timed out after {timeout}s on account '{account}'. Retry with a larger timeout or tighter filters."` string — never let `AppleScriptTimeout` propagate out of a tool.
- **`normalize_message_ids()`** (core/normalization.py) de-duplicates and validates ids are numeric before they are ever interpolated into AppleScript via `build_whose_id_list()`. **Calendar UIDs are non-numeric strings**, so a calendar equivalent needs a `normalize_event_ids()` (or similar) that validates UID *shape* (e.g. non-empty, no `|||`/control chars, reasonable max length) rather than reusing `normalize_message_ids`'s `isdigit()` check.
- **`_check_message_ids_cap()`** (manage/helpers.py) enforces `MAX_WHOSE_IDS` (50) — the same "AppleScript `whose id is X or id is Y ...` predicate has an undocumented parser ceiling around 200-500 terms" concern would apply to any Calendar `whose uid is X or uid is Y ...` predicate; reuse `MAX_WHOSE_IDS`/`iter_id_chunks` conceptually (a calendar-specific constant is fine, but keep the same defense: hard cap + `iter_id_chunks`-style batching helper + structured `WHOSE_ID_LIST_TOO_LARGE`-style error).

### 2.4 `output_format` handling summary

Two conventions, and every tool clearly opts into one:

1. **Dual `text`/`json` via a `_build_*_response()` helper** — `search_emails`, `list_inbox_emails`, `get_inbox_overview`, `list_mailboxes`, `get_needs_response`, `get_awaiting_reply`, `get_top_senders`, `get_statistics` all validate `output_format in {"text", "json"}` up front and dispatch to a shared formatter at the end.
2. **Plain confirmation text only** — mutation tools like `move_email`, `update_email_status`, `manage_trash` return a human-readable confirmation string; they do not offer `output_format="json"` (dry-run previews are text too). `reply_to_email(output_format="json")` is a narrow exception limited to `mode="draft"`/`mode="open"` (a "compose contract" documented in `tools/CLAUDE.md`).

Decide per-tool: discovery/read tools (list events, search events, get event) should offer `output_format="json"` from day one (agents need structured ids); action tools (create/update/delete event) can start text-only and add JSON later if a structured artifact (like the reply "compose contract") is needed.

### 2.5 `server.py` imports/registration — the full picture

File: `plugin/apple_mail_mcp/server.py` (105 lines). Holds:

- The single shared `mcp = FastMCP("Apple Mail MCP", instructions="...")` instance, wrapped in a typed `_AppleMailMCP` Protocol cast purely so mypy `--strict` sees a typed `.tool()`/`.remove_tool()`/`.run()` surface across the FastMCP dependency boundary.
- The four `ToolAnnotations` presets (2.3 above).
- `SEND_TOOLS = ("compose_email", "reply_to_email", "forward_email")` — the tuple `__main__.py` iterates to `mcp.remove_tool(name)` in `--read-only` mode.
- `USER_PREFERENCES`, `DEFAULT_MAIL_ACCOUNT`, `DEFAULT_MAIL_SIGNATURE`, `READ_ONLY`, `DRAFT_SAFE` — all read from `os.environ` at **module import time** (except `READ_ONLY`/`DRAFT_SAFE`, which start `False` and are mutated later by `__main__.main()`).

**Verified tool counts** (via `grep -rc '^@mcp\.tool' plugin/apple_mail_mcp/tools/<dir> | awk -F: '{sum+=$NF} END{print sum}'` per package, and a whole-tree recursive count):

| Package | Tool count |
|---------|-----------|
| `inbox/` | 6 |
| `search/` | 4 |
| `compose/` | 7 |
| `manage/` | 6 |
| `analytics/` | 5 |
| `smart_inbox/` | 3 |
| **Total** | **31** |

(Note: `plugin/apple_mail_mcp/__init__.py`'s inline comments — `# analytics (4 tools)`, `# compose (6 tools)` — are **stale**; do not trust them, trust the `grep -c '^@mcp\.tool'` count and `plugin/apple_mail_mcp/tools/CLAUDE.md`'s module table, both of which agree on 31 and are enforced by `tools/manifest_checks/tool_count.py`.)

---

## 3. Safe modes: `--read-only` / `--draft-safe` and env vars

### 3.1 Flag plumbing (CLI → server module state → tool bodies)

`plugin/apple_mail_mcp/__main__.py` (`main()`, 70 lines total):

```python
parser.add_argument("--read-only", action="store_true", ...)
parser.add_argument("--draft-safe", action="store_true", ...)
args = parser.parse_args()

server.READ_ONLY = args.read_only
server.DRAFT_SAFE = args.draft_safe or args.read_only   # read-only implies draft-safe

from apple_mail_mcp import mcp
from apple_mail_mcp.server import SEND_TOOLS

if args.read_only:
    for name in SEND_TOOLS:
        with suppress(KeyError, ValueError):
            mcp.remove_tool(name)

mcp.run()
```

Two independent enforcement layers, both required:

1. **Tool removal** — in `--read-only` mode, `compose_email`/`reply_to_email`/`forward_email` are physically removed from the FastMCP tool registry via `mcp.remove_tool(name)`, *after* import (so `@mcp.tool` still registers them, then they're deleted). `--draft-safe` alone does **not** remove any tool.
2. **Runtime mode check inside compose tools** — `plugin/apple_mail_mcp/tools/compose/helpers.py::_send_blocked(mode)`:
   ```python
   def _send_blocked(mode: str | None) -> str | None:
       if mode != "send":
           return None
       if compose._server.READ_ONLY:
           return "Error: Sending is disabled in read-only mode."
       if compose._server.DRAFT_SAFE:
           return "Error: Sending is disabled in draft-safe mode. Use mode='draft' or mode='open'."
       return None
   ```
   This is defense-in-depth: it protects any code path that reaches a compose tool with `mode="send"` even if tool-removal didn't happen (e.g. the CLI, which calls tool functions directly without going through `__main__.py`'s registry-removal step — see `cli/commands.py:379-384`, which sets `_server.DRAFT_SAFE = True` around a smoke-test draft creation specifically so a stray `mode="send"` can never fire from CLI testing).

**For Calendar:** if a `delete_event` (or `create_event`, `update_event`) tool needs read-only/draft-safe gating, follow pattern 2 (`_server.READ_ONLY`/`_server.DRAFT_SAFE` checked inside the tool body) rather than pattern 1 (registry removal), unless the tool is unconditionally unsafe in read-only mode — in which case add it to a new `CALENDAR_WRITE_TOOLS` tuple (parallel to `SEND_TOOLS`) and remove it the same way in `__main__.py`. Given Calendar has no concept of "send" (only create/update/delete), `--read-only` should almost certainly strip all calendar write tools the same way it strips `SEND_TOOLS`; `--draft-safe` has no obvious Calendar analog unless the implementer wants a "propose but don't commit" event-staging concept — most likely calendar write tools should simply respect `--read-only` and ignore `--draft-safe`.

### 3.2 Env vars

Read once, at `server.py` import time, from `os.environ`:

| Var | `server.py` symbol | Behavior |
|-----|---------------------|----------|
| `DEFAULT_MAIL_ACCOUNT` | `DEFAULT_MAIL_ACCOUNT` (`str \| None`, empty → `None`) | Tools default to this account instead of fanning out across every configured Mail account when `account=None` and (for search) `all_accounts=False`. Read **lazily** by tool bodies (`_server.DEFAULT_MAIL_ACCOUNT`), not captured at import. |
| `DEFAULT_MAIL_SIGNATURE` | `DEFAULT_MAIL_SIGNATURE` | Compose/reply/forward apply this Mail signature by default unless `include_signature=False`. |
| `USER_EMAIL_PREFERENCES` | `USER_PREFERENCES` | Appended to every `@inject_preferences`-decorated tool's docstring. |

**For Calendar**, a `DEFAULT_CALENDAR` (or `DEFAULT_CALENDAR_ACCOUNT`, matching how Calendar.app groups calendars by account — iCloud, Exchange, Google, On My Mac) env var is the natural analog of `DEFAULT_MAIL_ACCOUNT`, read the same way: module-level `os.environ.get(...)` in `server.py`, read lazily via `_server.DEFAULT_CALENDAR` inside tool bodies so tests can monkeypatch it post-import.

---

## 4. Test architecture

### 4.1 Directory layout

`tests/<area>/` — one subfolder per tool surface plus cross-cutting buckets, each with `__init__.py`. Current top-level buckets (from `tests/CLAUDE.md`, verified against the filesystem): `analytics/`, `cli/`, `compose/`, `core/`, `cross_cutting/`, `inbox/`, `infra/`, `manage/`, `property/`, `search/`, `smart_inbox/`. `conftest.py`, `fixtures/`, and `property/` stay at `tests/` root (not nested inside a surface folder).

A `calendar/` sibling directory (`tests/calendar/__init__.py` + `tests/calendar/test_*.py`) is the natural landing spot for calendar tool tests, mirroring `tests/manage/`, `tests/search/`, etc.

### 4.2 Mocking `run_applescript`

Two established patterns, both patch `subprocess.run` (not `run_applescript` directly, in the primary pattern) so the **real** `core.run_applescript` code path (escaping, timeout, sanitization) is exercised end-to-end:

- **`side_effect` capturing the script from `kwargs["input"]`** — the dominant pattern. Example structure from `tests/search/test_mail_search_tools.py`: build a helper like `_record_line(...)` that returns a `"|||".join([...])` row, patch `subprocess.run` with a `Mock`/`side_effect` function that inspects the incoming AppleScript (via `kwargs["input"].decode()`) to decide which canned row-text to return, then assert on the tool's parsed output. See also `tests/cross_cutting/test_modernization_3_1_5.py` (`_ScriptCapture` helper class) and `tests/compose/test_compose_tools.py` for reusable capture idioms.
- **Direct `core.run_applescript` patch** for cases where the exact AppleScript text does not matter, only the return value — used heavily in `tests/manage/`, `tests/search/test_contracts_search_tools.py`, etc., via `unittest.mock.patch("apple_mail_mcp.core.run_applescript", ...)` or (per-tool facade attribute) `patch("apple_mail_mcp.tools.manage.run_applescript", ...)`.

**Which one to patch matters** because of the facade-import pattern (section 2.1): patch the **package facade attribute** (`apple_mail_mcp.tools.manage.run_applescript`, `apple_mail_mcp.tools.search.run_applescript`, etc.), not the `core.run_applescript` origin, unless the test explicitly wants to also exercise every other facade that re-imports the same name. `conftest.py`'s autouse `validate_account_name` fixture is the canonical illustration of "patch every facade module separately":

```python
monkeypatch.setattr("apple_mail_mcp.core.validate_account_name", _validate)
monkeypatch.setattr("apple_mail_mcp.tools.inbox.validate_account_name", _validate)
monkeypatch.setattr("apple_mail_mcp.tools.search.validate_account_name", _validate)
monkeypatch.setattr("apple_mail_mcp.tools.manage.validate_account_name", _validate)
monkeypatch.setattr("apple_mail_mcp.tools.analytics.validate_account_name", _validate)
monkeypatch.setattr("apple_mail_mcp.tools.smart_inbox.validate_account_name", _validate)
monkeypatch.setattr("apple_mail_mcp.tools.compose.validate_account_name", _validate)
```

A future `apple_mail_mcp.tools.calendar` facade would need the same treatment added to this fixture (or a calendar-specific autouse fixture) if calendar tools call `validate_account_name`/an equivalent `validate_calendar_name`.

### 4.3 conftest.py fixtures worth reusing

File: `tests/conftest.py` (38 lines total — small and worth reading in full).

```python
@pytest.fixture(autouse=True)
def _pass_through_known_test_accounts(monkeypatch):
    """account='Work' passes without real Mail; account='Missing' returns account_not_found."""
```

- Stubs `list_mail_account_names` to `["Work"]` and `cli._mailbox_count` to `0` so the **entire suite runs with zero live Mail.app dependency** — this is explicitly called out because "CI runs on Ubuntu with no Mail.app; every test mocks AppleScript or tests pure Python" (`tests/CLAUDE.md`).
- **Autouse** — every test in the suite gets this fixture without opting in. A calendar equivalent (e.g. `_pass_through_known_test_calendars`, stubbing a `list_calendars`/`validate_calendar_name`-style helper against a canned `["Home", "Work"]` list) should also be autouse in `conftest.py` so calendar tests don't need to hand-roll calendar-name validation mocking in every test file.

### 4.4 Expected test count gate

`tools/expected_test_count.txt` currently reads **`1053`** (verified: `cat tools/expected_test_count.txt` → `1053`). This is the single source of truth for the collected-test count; `tools/gates/dev-check.sh`'s `run_test_count_check()` recomputes the real count and fails with an explicit "update this file to N" message on drift. **Documented recount command** (from `tools/CLAUDE.md`/root `CLAUDE.md`):

```bash
PYTEST_ADDOPTS='' .venv/bin/pytest --collect-only tests
```

Any calendar test module added will increase the collected count — **`tools/expected_test_count.txt` must be bumped to match** in the same PR/commit, or `dev-check.sh default`/`release` fails.

### 4.5 Other test-architecture notes

- **`tests/fixtures/module_line_budget/baseline.json`** — currently `{"threshold": 600, "modules": {}}` (empty after v3.9.1 decomposition). New calendar modules just need to individually stay under 600 LOC; no baseline edit is needed unless a tracked module *regresses* past budget.
- **`.claude/hooks/check_applescript_compiles.py`** (section 5.5 below) applies to any module under `plugin/` with a function ending in `_script` that returns a string starting with `tell application "Mail"` — **a Calendar script builder returning `tell application "Calendar"` will NOT be auto-discovered by this hook as written** (it hardcodes the `'tell application "Mail"'` string as the full-script marker at line 162: `if 'tell application "Mail"' not in head: continue`). This hook needs a small edit (accept `"Mail"` or `"Calendar"`, or generalize to any `tell application "..."` line) before it will compile-check calendar script builders — flag this explicitly to whoever implements the calendar core module.
- **Property-based tests** (`tests/property/`) — `test_escape_applescript_properties.py` and `test_validate_account_name_properties.py` use Hypothesis to fuzz `escape_applescript` and `validate_account_name`. If Calendar introduces a parallel `validate_calendar_name` or reuses `escape_applescript` as-is (it should — it's app-agnostic), no new Hypothesis strategy is strictly required for escaping, but a `validate_calendar_name` with different validation rules would want its own property test.

---

## 5. Release surfaces

### 5.1 The six version files that bump together

Verified current values (all **3.9.3**, consistent):

| # | File | JSON path / marker |
|---|------|---------------------|
| 1 | `pyproject.toml` | `[project].version` |
| 2 | `plugin/.claude-plugin/plugin.json` | `version` |
| 3 | `plugin/.codex-plugin/plugin.json` | `version` |
| 4 | `.claude-plugin/marketplace.json` | `plugins[0].version` (**not** `metadata.version`, which stays at `1.0.0` — that field versions the marketplace manifest itself, not the plugin) |
| 5 | `server.json` | top-level `version` **and** `packages[0].version` (two fields in one file) |
| 6 | `apple-mail-mcpb/manifest.json` | `version` |

`CHANGELOG.md` must also have a `## {version} - YYYY-MM-DD` heading matching `pyproject.toml`, with no bullets left under `## Unreleased` — enforced by `tools/manifest_checks/version.py::_check_changelog_release_version` as part of `dev-check.sh release`.

Any calendar-tools PR that changes behavior (new tools, new manifests entries) must bump all six files together, per root `CLAUDE.md` § Version bump.

### 5.2 Tool-count claims — where they appear and how they're enforced

`tools/manifest_checks/tool_count.py::_extract_registered_tool_names()` extracts the ground truth by scanning every `.py` under `plugin/apple_mail_mcp/tools/` (recursively — `.rglob("*.py")`) for `^@mcp\.tool` lines and grabbing the function name from the following `def`/`async def` line.

**`ACTIVE_DOC_TOOL_COUNT_REQUIRED`** (must contain a numeric claim that matches the live count exactly, or the gate fails with "missing active tool-count claim"):

```
AGENTS.md, CLAUDE.md, README.md, docs/CLAUDE.md,
plugin/apple_mail_mcp/CLAUDE.md, plugin/apple_mail_mcp/tools/CLAUDE.md,
plugin/docs/CLAUDE.md, .claude-plugin/CLAUDE.md,
apple-mail-mcpb/CLAUDE.md, apple-mail-mcpb/build-mcpb.sh,
tools/manifest_checks/artifacts.py   (embedded MCPB README generator)
```

**`ACTIVE_DOC_TOOL_COUNT_SCAN_ONLY`** (checked *if* a numeric claim is present, but its absence is not an error): `tools/CLAUDE.md`, `docs/CLAUDE-conventions.md`.

The regex family (`TOOL_COUNT_CLAIM_PATTERNS` in `tools/manifest_checks/common.py`) matches things like `\b(\d+)\s+(?:MCP\s+)?tools?\b` — i.e. **any digit immediately followed by the word "tool"/"tools"** anywhere in those files is treated as a claim and validated. **Practical implication for calendar work:** if calendar tools are added, every file in `ACTIVE_DOC_TOOL_COUNT_REQUIRED` needs its "31 tools" (or "N tools") string updated to the new total in the same change, or `validate_manifests.py`/`dev-check.sh` will fail the drift check. Also watch for **incidental false-positive matches** of the regex against unrelated numbers followed by "tool(s)" in prose — the gate is a blunt regex, not semantic.

Additionally: `apple-mail-mcpb/manifest.json`'s `tools[]` array must list every registered tool **by name**, checked separately (`_check_mcpb_tool_names` — not tool_count.py but a sibling check in `manifest_checks`), so calendar tools need entries there too.

### 5.3 `tools/gates/dev-check.sh` stages

File: `tools/gates/dev-check.sh` (175 lines). Tiers (invoke as `bash tools/gates/dev-check.sh <tier>`):

| Tier | Runs |
|------|------|
| `default` (no arg) | `validate_manifests.sh` → `validate_tasks_layout.py` → `validate_repo_root.py` → module-line-budget report → `pytest tests/ -q` → `run_test_count_check` (compares live collected count to `tools/expected_test_count.txt`); adds `check_wrapper_surface.py` only if **staged** files touch `plugin/apple_mail_mcp/tools/`, `__init__.py`, `server.py`, or the MCPB manifest |
| `lint` | `ruff check plugin/apple_mail_mcp/` → `ruff format --check plugin/apple_mail_mcp/` → `mypy --strict plugin/apple_mail_mcp/` — all **fatal** |
| `surface` | `default` + wrapper check always |
| `manifest` | `validate_manifests.sh` only |
| `live` | `default` + `.venv/bin/apple-mail quick-check --json` (touches real Mail.app) |
| `release` | `lint` → `tools/gates/build-artifacts.sh` (rebuilds zip/.plugin/.mcpb) → `validate_tasks_layout.py` → `validate_repo_root.py` → `pytest` → `run_test_count_check` → wrapper check. **Always run before commit/PR that touches `plugin/`, manifests, `pyproject.toml`, or release artifacts.** |
| `all` | `default` + wrapper check always |

`mypy --strict plugin/apple_mail_mcp/` is fatal on the `lint`/`release` tiers — **any new calendar module must be fully typed** (no bare `Any` leaks, explicit return types on every function) from the first commit, since `mypy --strict` has zero tolerance for implicit `Any`.

### 5.4 Module line budget

- **Threshold: 600 physical lines**, soft-warn everywhere, hard-fail only on **regression against baseline** (`tests/fixtures/module_line_budget/baseline.json`, currently `{"modules": {}}` — nothing tracked because nothing currently exceeds 600 LOC after the v3.9.1 decomposition).
- Scanner: `python3 tools/validators/check_module_line_budget.py` (also runs inside `dev-check.sh`, `validate_manifests.py`, CI, pre-commit).
- Scope: `plugin/apple_mail_mcp/` and `tools/` — **not** `tests/`.
- Practical guidance already encoded in the repo's own tool-surface split (see section 8): every existing 25-30-tool surface (compose 7 tools, manage 6, analytics 5, etc.) is split into multiple files under `tools/<surface>/`, each under budget, linked through a package `__init__.py` facade. This is the template a `tools/calendar/` package must follow from day one — do not write one large `tools/calendar.py` and let it grow past 600 lines before splitting.

### 5.5 The AppleScript-compile edit hook

File: `.claude/hooks/check_applescript_compiles.py` (202 lines), invoked (per `.claude/settings.json`, not read in full here but referenced by root `CLAUDE.md`'s "Post-change ship" pointer and `docs/CLAUDE-conventions.md`) as a PostEdit-style hook on files under `plugin/`.

Mechanism: imports the edited module (via `importlib.util.spec_from_file_location`, with `plugin/` prepended to `sys.path` so `apple_mail_mcp.*` imports resolve), finds every function whose **name ends in `_script`**, calls it with sample kwargs from a hardcoded `SAMPLE_KWARGS` dict (falls back to skipping the function if a required parameter isn't in that dict and has no default), and — **only if the returned string's first non-blank line contains `'tell application "Mail"'`** — pipes it to `osacompile -o /dev/null` to catch syntax errors without executing anything against real Mail.

**Action required for Calendar work** (flagged above in 4.5 too): this hook's full-script detection (`check_applescript_compiles.py:162`) is hardcoded to the literal string `tell application "Mail"`. A calendar script builder function like `create_event_script(...)` returning `tell application "Calendar" ... end tell` will be **silently skipped** by this hook (treated as a "fragment," not a full script, so never osacompiled) unless the hook is updated to also recognize `tell application "Calendar"` (or generalized to `re.match(r'tell application "\w+"', head)`). This should be one of the first small edits made when calendar script builders are introduced, otherwise a class of AppleScript syntax bugs (the same class the hook exists to catch — see its own docstring reference to the 3.3.0 `get_awaiting_reply` regression) will go undetected pre-commit for calendar code. Also extend `SAMPLE_KWARGS` with calendar-relevant parameter names (`calendar_name`, `event_uid`, `start_date`, `end_date`, etc.) as new script builders are added.

---

## 6. Skills

### 6.1 Layout

`plugin/skills/` — nine directories, each `plugin/skills/<name>/SKILL.md` (+ optional `references/` subfolder), auto-discovered by the Claude Code plugin loader from the tree (no explicit registration file). Canonical shared references live in `plugin/skills/references/` (6 files on disk: `agent-id-first-workflow.md`, `exchange-account-patterns.md`, `large-inbox-rules.md`, `pre-draft-verification.md`, `recent-first-triage.md`, `research-project-tracking.md`). Only 5 of those 6 are copied into per-skill `references/` folders via `tools/validators/sync_skill_references.py::SYNC_MAP`; `agent-id-first-workflow.md` is deliberately maintainer-index-only (linked from `plugin/skills/CLAUDE.md`, never copied) per that file's own doc comment.

### 6.2 SKILL.md frontmatter convention

```yaml
---
name: apple-mail-operator
description: This skill should be used when the user asks "how does this Mail MCP work", "which tool should I use", ... . Uses list_accounts, list_mailboxes, get_inbox_overview, .... Do NOT use for sustained inbox-zero programs (see email-management), ....
---
```

Rules (from `plugin/skills/CLAUDE.md` and `docs/CLAUDE-conventions.md`):

- **Directory name must equal frontmatter `name`.**
- `description`: third-person, **4-6 quoted trigger phrases**, names **3-5 central MCP tool names** by exact identifier, ends with an explicit **"Do NOT use for … (see sibling)"** disambiguation clause pointing at sibling skills.
- Body: imperative voice, no persona preamble ("You are an expert...").
- Target length ~1,500-2,000 words for larger/umbrella skills; narrower skills can be shorter provided they cover purpose, triggers, performance notes, sibling matrix, and destructive red lines.
- **Skills-only policy**: no `commands/` directory — the old `/email-management` slash command was retired specifically to avoid duplicate skill/command surfaces. Any calendar entry point must be a skill, not a slash command.

### 6.3 Canonical references and `sync_skill_references.py`

`tools/validators/sync_skill_references.py::SYNC_MAP` (dict literal, `skills/CLAUDE.md`'s canonical index) maps each skill directory name → list of canonical filenames it needs copied in from `plugin/skills/references/`. **Packaged Claude/Codex skills can only link files that physically exist inside their own skill directory** (`references/foo.md`, never `../references/foo.md`) — this is enforced by `tests/infra/test_packaged_skill_paths.py`. Workflow: edit the canonical file once under `plugin/skills/references/`, then run

```bash
python3 tools/validators/sync_skill_references.py          # writes copies
python3 tools/validators/sync_skill_references.py --check  # CI/dev-check parity gate, no writes
```

For calendar skills that need to share content with existing mail skills (e.g. a shared "large inbox" or "bounded scan" performance-rules doc) or need calendar-specific shared references (e.g. `calendar-recurrence-patterns.md`, `calendar-timezone-handling.md`), add the new canonical file under `plugin/skills/references/`, add an entry to `SYNC_MAP` for each calendar skill directory that needs it, and run the sync script — do not hand-copy files or symlink.

### 6.4 Where skill counts are claimed

The literal string **"9 workflow skills"** / **"nine ... skills"** appears in (verified via grep):

```
AGENTS.md:53, CLAUDE.md:51                       ("9 workflow skills" table row)
README.md:61                                      ("MCP server (31 tools) and nine bundled workflow skills")
plugin/docs/CLAUDE.md:47
plugin/skills/email-management/README.md:29
docs/AGENT_LIVE_TESTING.md:373
.claude-plugin/CLAUDE.md:47                        ("nine auto-discovered workflow skills")
```

**None of these are enforced by an automated gate** the way the "31 tools" claim is (there is no `manifest_checks` skill-count validator analogous to `tool_count.py`) — they are prose claims maintained by hand. If calendar work adds new skills (e.g. an `apple-calendar-operator` skill mirroring `apple-mail-operator`), every one of the six locations above needs a manual find-and-replace from "nine"/"9" to the new count, and this is worth adding as a lint/grep step if it isn't already covered by `finalize-apple-mail-mcp`'s doc-sync pass.

---

## 7. `tasks/` layout rules and CLI structure

### 7.1 `tasks/CLAUDE.md` agent requirements (must-read before creating/moving files)

Enforced by `tools/validators/validate_tasks_layout.py` + `tests/infra/test_tasks_layout.py`, run in `dev-check.sh` default and release tiers. Rules:

- **`tasks/` root** may only contain `CLAUDE.md`, `INDEX.md`, `todo.md` — no loose `.md` files, no workstream folders directly at root.
- **`tasks/active/<lane>/`** — open workstreams from the last ~30 days, one subfolder per lane, dated files inside (`handoff-YYYY-MM-DD.md`, `phase-plan.md`). **This report belongs under `tasks/active/apple-calendar-tools/reports/`**, which already exists as an empty directory on this branch (confirmed via `ls`) — i.e. someone has already scaffolded the workstream folder; other researchers' reports likely land as sibling files in the same `reports/` directory.
- **`tasks/reference/<name>.md`** — durable specs cited by code/CHANGELOG/docs (long-lived policy only, not ephemeral writeups).
- **`tasks/archive/YYYY-MM/`** — shipped/superseded work; never edit archived files for current work.
- After creating a new workstream: **update `tasks/todo.md`** to point at the active handoff, and **update `tasks/INDEX.md`** to add the new active row.
- Path references in code/docs must use full bucket paths (`tasks/active/...`) — never a flat `tasks/<dated-file>.md`.

When the calendar-tools implementation phase begins (post-research), the phase plan / implementation handoff should live at `tasks/active/apple-calendar-tools/phase-plan.md` (or similarly named), with this and sibling researchers' reports staying under `tasks/active/apple-calendar-tools/reports/` until the workstream ships, at which point the whole folder moves to `tasks/archive/YYYY-MM/`.

### 7.2 CLI structure (`plugin/apple_mail_mcp/cli/`)

- `cli/parser.py` (344 lines) — pure `argparse` construction inside `_build_parser()`, called lazily from `main()` (not at import time). One `subparsers.add_parser(...)` block per subcommand (`accounts`, `inbox`, `search`, `show`, `mailboxes`, `unread`, `overview`, `needs-response`, `awaiting-reply`, `top-senders`, `statistics`, `move-dry-run`, `trash-dry-run`, `drafts` [with nested `list`/`cleanup-empty` sub-subparsers], `draft`, `mcp-config`, `smoke-test`, `draft-verify-smoke`, `perf-test`, `quick-check`). Helper functions `_add_account_flag()` / `_add_json_flag()` standardize the recurring `--account`/`--json` flags.
- `cli/commands.py` (13k / ~ hundreds of lines) — the actual subcommand implementations, calling the same tool functions the MCP server calls (no separate business logic).
- `cli/__init__.py` — facade `main()` entry point (`apple-mail` console script, per `pyproject.toml` entry points).
- `cli/formatting.py`, `cli/perf.py`, `cli/draft_smoke.py`, `cli/constants.py` — supporting leaves.

**If calendar CLI subcommands are added**, follow the exact same shape: add a new `subparsers.add_parser("calendar-events", ...)`-style block (or a `calendar` subcommand with nested sub-subparsers, mirroring the `drafts` nested pattern) in `cli/parser.py`, implement the dispatch in `cli/commands.py` calling into the same calendar tool functions the MCP server registers — the CLI must never duplicate business logic, only argument parsing and output formatting.

---

## 8. Recommended file layout for calendar engine + tools

Given the 600 LOC module budget (section 5.4) and the existing package-per-surface pattern (section 2.1, 5.4), the recommendation is:

```
plugin/apple_mail_mcp/
  core/
    calendar_applescript.py      # OPTIONAL: only if Calendar needs its own lock/timeout
                                  #  discipline distinct from core/applescript.py — see 1.2.
                                  #  Default recommendation: reuse core.run_applescript unchanged.
    calendar_script_fragments.py # calendar-specific AppleScript fragment builders, mirroring
                                  #  core/script_fragments.py (e.g. calendar-name resolution with
                                  #  fallback, date/recurrence snippet builders, event-fields snippet)
    calendar_validation.py       # validate_calendar_name(), reject_unknown_calendar(),
                                  #  calendar_not_found_json() — mirrors core/validation.py exactly
  backend/
    base.py                      # UNCHANGED — ToolError/ScanWindow/WriteResult are already
                                  #  app-agnostic; add calendar-specific error-code helpers here
                                  #  only if a genuinely new remediation shape is needed (e.g. a
                                  #  calendar_target_selector_deprecated_error mirroring
                                  #  target_selector_deprecated_error, if warranted)
  constants.py                   # ADD: CALENDAR_SCAN_BOUNDS dict (or CALENDAR_-prefixed keys
                                  #  merged into the existing SCAN_BOUNDS) — see 1.5
  bounded_scan.py                # ADD calendar-parallel helpers only if the same
                                  #  bounded-slice-then-filter AppleScript hazard applies to
                                  #  Calendar (it likely does for very large recurring-event
                                  #  calendars) — e.g. build_bounded_event_scan(),
                                  #  build_whose_uid_list(), iter_uid_chunks()
  tools/
    calendar/                    # NEW package, mirrors tools/manage/, tools/search/, etc.
      __init__.py                # FACADE: import IO/core/server symbols first, then each tool
                                  #  submodule, re-export in __all__ — exact shape of
                                  #  tools/manage/__init__.py (section 2.1)
      list_events.py             # read tool: list_events (mirrors inbox/list_emails.py)
      search_events.py           # read tool: search_events (mirrors search/emails.py)
      by_id.py                   # read tool(s): get_event_by_id / get_event_by_ids
                                  #  (mirrors search/by_id.py)
      create_event.py            # mutation tool: create_event (mirrors compose/send.py's
                                  #  standalone-create shape, not reply/forward)
      update_event.py            # mutation tool: update_event (mirrors manage/status.py or
                                  #  manage/move.py depending on whether "update" covers moving
                                  #  between calendars)
      delete_event.py            # mutation tool: delete_event (mirrors manage/trash.py —
                                  #  DESTRUCTIVE_TOOL_ANNOTATIONS, dry_run, id-first + deprecated
                                  #  filter-selector gating)
      calendars.py                # read tool(s): list_calendars (mirrors inbox/accounts.py)
      helpers.py                  # shared leaf: id-cap checks, date-cutoff helpers, filter-scan
                                   #  gating error text — mirrors tools/manage/helpers.py exactly
      parsing.py / records.py     # pure leaf(s): pipe-delimited row parsers, response builders —
                                   #  mirrors tools/search/records.py + tools/inbox/parsing.py
      script.py / scripts.py      # leaf(s) holding the actual AppleScript string builders,
                                   #  kept separate from the @mcp.tool functions so
                                   #  check_applescript_compiles.py (once patched per 5.5) can
                                   #  discover *_script functions cleanly
  __init__.py                    # ADD: `calendar,` to the tuple of imported tool-surface
                                  #  packages (alongside analytics/compose/inbox/manage/search/
                                  #  smart_inbox)
tests/
  calendar/
    __init__.py
    test_calendar_tools.py       # or split per-tool the way tests/manage/, tests/search/ do
    test_calendar_parsing.py     # pure-parser unit tests (no subprocess mock needed)
    test_calendar_escaping.py    # only if calendar introduces new escaping edge cases
                                  #  (event titles/notes are free text just like Mail subjects,
                                  #  so escape_applescript should already cover them)
plugin/skills/
  apple-calendar-operator/        # OPTIONAL new skill, mirrors apple-mail-operator/ — bootstrap,
    SKILL.md                      #  tool selection, safe navigation for Calendar
  (or fold calendar guidance into existing skills if the tool surface is small)
```

Rationale for each layout choice:

1. **`tools/calendar/` as a new top-level package** (not nested under an existing surface) because Calendar is a conceptually separate MCP tool domain from Mail, exactly the way `search/`, `manage/`, `analytics/` are separate domains from each other today — not because of any technical constraint, but because the facade-per-surface pattern (section 2.1) is what makes `conftest.py`'s autouse test-patching and the module-line-budget package-splitting both work cleanly.
2. **Reuse `core.run_applescript()`, `core.escape_applescript()`, `backend.base.ToolError`/`serialize_tool_error` unchanged.** These three are the load-bearing, app-agnostic primitives; duplicating them for Calendar would violate the repo's own "reusable by default" convention (root `CLAUDE.md` / company `AGENTS.md`) and would silently lose the single-flight lock and structured-error contract that every other tool in the repo depends on. The single-flight lock question (does Calendar need its own, or does it share Mail's?) is the one open design decision worth flagging explicitly to whoever picks this up — Calendar.app and Mail.app are separate processes, so contention is not obviously required, but `osascript` itself (the CLI binary) may have its own concurrency ceiling worth verifying empirically before deciding.
3. **A `CALENDAR_SCAN_BOUNDS`-style constant, not inline magic numbers**, because the existing `SCAN_BOUNDS` dict is explicitly designed as "one edit retunes every tool" (constants.py:79) and the module-line-budget-conscious file split works best when every tool file imports its caps from one place rather than defining its own.
4. **`calendars.py` for calendar enumeration**, separate from event CRUD, mirroring `inbox/accounts.py` (`list_accounts`, `list_account_addresses`) being separate from `inbox/list_emails.py` — Calendar.app's `calendar` objects (with `writable`/`sharable` properties, per the standalone `apple-calendar` skill at `~/.claude/skills/apple-calendar/scripts/cal-list.sh` referenced by the environment) are a distinct enumeration concern from `calendar event` CRUD, exactly like Mail accounts vs. messages.
5. **Keep AppleScript string builders in a dedicated `script.py`/`scripts.py` leaf**, separate from the `@mcp.tool` function bodies, specifically so the `check_applescript_compiles.py` hook (once patched to recognize `tell application "Calendar"`, per section 5.5) can import just that leaf module and discover every `*_script`-suffixed builder function without dragging in FastMCP registration side effects.

### 8.1 Non-obvious Calendar-specific technical notes for the implementer

- **Calendar.app AppleScript event ids/UIDs are strings (persistent UUIDs), not Mail's small integers.** Every Mail id-handling primitive in this repo (`normalize_message_ids`'s `.isdigit()` check, `build_whose_id_list`'s `"id is X or id is Y"` numeric-equality predicate, `MAX_WHOSE_IDS` batching) assumes numeric ids and **cannot be reused verbatim** — a calendar-parallel `normalize_event_ids()` and `build_whose_uid_list()` (using `uid is "X" or uid is "Y"` string-equality predicates, each value run through `escape_applescript`) will be needed. This is the single largest "looks reusable but isn't" trap in this codebase for calendar work.
- **The forbidden-`whose`-on-a-slice AppleScript hazard documented in `bounded_scan.py`'s module docstring (Gmail's `[Gmail]/All Mail` crash) is Mail/IMAP-specific** — Calendar.app's stores (local, iCloud CalDAV, Exchange, Google) may or may not have an analogous crash mode when filtering a bound `events 1 thru N` slice with `whose`. This should be verified empirically against at least one non-local (iCloud or Exchange) calendar account before assuming `build_bounded_filtered_scan`'s in-loop-`if` pattern is strictly necessary for Calendar too — but given the repo's demonstrated pattern of "assume remote-store AppleScript surprises exist until proven otherwise," defaulting to the safe bounded-slice-plus-in-loop-filter pattern from day one (rather than an unbounded `whose`) is the lower-risk choice regardless.
- **A separate, non-MCP `apple-calendar` skill already exists on this machine** (`~/.claude/skills/apple-calendar/`, also mirrored at `~/.codex/skills/apple-calendar/` and `~/.agents/skills/apple-calendar/`), implemented as shell scripts wrapping `osascript` directly (`cal-list.sh`, `cal-events.sh`, `cal-create.sh`, `cal-update.sh`, `cal-delete.sh`, `cal-search.sh`, `cal-read.sh`), with its own date-format and RRULE-recurrence conventions documented in its `SKILL.md`. That skill is **not part of this repo** and is a completely separate, non-MCP tool (global to the operator's machine, not shipped with apple-mail-mcp). It is worth reading for AppleScript-dictionary syntax reference (recurrence rule construction, date formatting, read-only-calendar handling for Birthdays/Holidays) when writing the calendar core module's script builders, but its shell/CLI architecture should **not** be copied — the new calendar tools must be native `@mcp.tool` Python functions following this repo's `core.run_applescript()` pattern, not shell wrappers.

---

## Source files read in full or in relevant part (for traceability)

```
plugin/apple_mail_mcp/__init__.py
plugin/apple_mail_mcp/__main__.py
plugin/apple_mail_mcp/server.py
plugin/apple_mail_mcp/constants.py
plugin/apple_mail_mcp/bounded_scan.py
plugin/apple_mail_mcp/backend/base.py
plugin/apple_mail_mcp/core/__init__.py
plugin/apple_mail_mcp/core/applescript.py
plugin/apple_mail_mcp/core/escaping.py
plugin/apple_mail_mcp/core/normalization.py
plugin/apple_mail_mcp/core/preferences.py
plugin/apple_mail_mcp/core/replied.py
plugin/apple_mail_mcp/core/script_fragments.py
plugin/apple_mail_mcp/core/validation.py
plugin/apple_mail_mcp/tools/search/emails.py
plugin/apple_mail_mcp/tools/search/records.py
plugin/apple_mail_mcp/tools/manage/move.py
plugin/apple_mail_mcp/tools/manage/helpers.py
plugin/apple_mail_mcp/tools/manage/__init__.py
plugin/apple_mail_mcp/tools/compose/__init__.py
plugin/apple_mail_mcp/tools/compose/helpers.py (partial)
plugin/apple_mail_mcp/tools/inbox/parsing.py
plugin/apple_mail_mcp/cli/parser.py
plugin/apple_mail_mcp/CLAUDE.md
plugin/apple_mail_mcp/tools/CLAUDE.md
plugin/skills/CLAUDE.md
plugin/skills/apple-mail-operator/SKILL.md
tests/conftest.py
tests/CLAUDE.md
tests/search/test_mail_search_tools.py (partial)
tools/CLAUDE.md
tools/gates/dev-check.sh
tools/manifest_checks/tool_count.py
tools/manifest_checks/version.py (partial)
tools/manifest_checks/common.py (partial, grep-verified)
tools/validators/sync_skill_references.py
tools/expected_test_count.txt
tests/fixtures/module_line_budget/baseline.json
.claude/hooks/check_applescript_compiles.py
.claude-plugin/CLAUDE.md
apple-mail-mcpb/CLAUDE.md
tasks/CLAUDE.md
docs/CLAUDE-conventions.md (partial — Module line budget section)
CLAUDE.md (root)
pyproject.toml, plugin/.claude-plugin/plugin.json, plugin/.codex-plugin/plugin.json,
  .claude-plugin/marketplace.json, server.json, apple-mail-mcpb/manifest.json (version fields only)
CHANGELOG.md (head)
~/.claude/skills/apple-calendar/SKILL.md (external, machine-global, not part of this repo)
```
