# MCP Architecture Research: Backend Abstraction for apple-mail-mcp

Design research for separating the **27 `@mcp.tool` surfaces** from the **Mail.app execution backend** so we can (a) eliminate `whose`-clause regressions by construction, (b) swap AppleScript reads for SQLite Envelope Index reads later, and (c) keep mutations on the authoritative AppleScript path.

---

## TL;DR

- **Pattern:** Split into a thin **tool layer** (`tools/*.py`) and a typed **`MailBackend` Protocol** (Strategy + DI) with `AppleScriptBackend` today and `EnvelopeIndexBackend` later. Reads go through `backend.read.*`, writes through `backend.write.*` — never through ad-hoc AppleScript strings in tools.
- **Enforcement:** A **capability token** (`BoundedScan` / `ScanWindow` dataclass returned only by `core.bounded_inbox_scan()`) the backend requires as a parameter. Future authors *cannot* call `backend.read.list_messages(...)` without producing a token — caught at type-check time and at runtime. Cheaper and more durable than lint or AST tests.
- **Contract change:** Retire `allow_full_scan=True` as a *boolean opt-in*. Replace it with a structured `ToolError(code="UNBOUNDED_SCAN_REQUIRED", remediation=...)` plus a separate, explicitly named tool (e.g. `full_inbox_export`) for the legitimate all-time case. Per `mcp-builder`, error messages must guide the agent to a concrete next step — a boolean parameter does that worse than a typed error + alternate tool.
- **Swap-readiness:** Backend method signatures must be **domain-shaped, not script-shaped**: `list_messages(mailbox, window: ScanWindow, fields: FieldSet, limit: int) -> list[MessageSummary]`. No AppleScript-specific kwargs leak through. Then v2 swaps in `EnvelopeIndexBackend` with a write-through invalidation hook driven by `synchronize_account`.
- **Top anti-pattern (per mcp-builder):** *Boolean escape hatches with no cost signalling*. The agent will toggle them without realizing the latency/blast radius. Replace with structured errors that name the cheaper alternative.

---

## Skills loaded

1. **`mcp-builder`** — confirmed guidance on actionable error messages, output schemas / structured content, annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`), and the "API coverage vs workflow tools" balance. Applied to the `allow_full_scan` recommendation and to suggesting `outputSchema`-backed structured errors.
2. **`plugin-dev:mcp-integration`** — reinforced that this plugin already correctly bundles a single stdio MCP server (`plugin/start_mcp.sh` + `plugin.json`), and that environment-variable expansion + `${CLAUDE_PLUGIN_ROOT}` are the only portable references. No `.mcp.json` split needed.
3. **`plugin-dev:plugin-structure`** — confirmed the current layout (`plugin/apple_mail_mcp/` as the Python package, `plugin/start_mcp.sh` as the launcher, manifest in `plugin/.claude-plugin/plugin.json`) is canonical. The proposed `backend/` subpackage fits cleanly under `plugin/apple_mail_mcp/` without changing discovery.

I also grounded the report in the current source: `plugin/apple_mail_mcp/core.py` (`run_applescript`, `fetch_replied_ids`, `replied_ids_script` already document the "NO `whose` clauses" invariant at line 621), and the four existing `allow_full_scan=True` opt-ins in `tools/compose.py`, `tools/analytics.py`, `tools/inbox.py`.

---

## Layered backend pattern

Mature MCP servers consistently separate **what the agent asked for** from **how the data source answers**. Examples:

- **Anthropic Filesystem MCP** — tools are thin wrappers around a `FileSystemService` with bounded-window primitives (`list_directory(path, limit)`); no tool builds raw paths.
- **Anthropic Git MCP** — `git_*` tools delegate to a `Repo` object via GitPython; the tool layer only validates inputs and formats output.
- **Linear MCP (hosted, SSE)** — tools call a typed GraphQL client; the resolver layer enforces field selection and pagination.

The common seam is a **service object held on a context, accessed via DI**. For FastMCP/Python, the idiomatic shape is a `Protocol` plus a module-level singleton:

```python
# plugin/apple_mail_mcp/backend/base.py
from typing import Protocol, Sequence
from dataclasses import dataclass

@dataclass(frozen=True)
class ScanWindow:
    """Capability token. Only producible via core.bounded_inbox_scan()."""
    mailbox: str
    since: datetime           # required, never None
    limit: int                # required, 1..MAX_SCAN
    _issued_by: str           # set to "core.bounded_inbox_scan"; checked at backend edge

class MailReadBackend(Protocol):
    def list_messages(self, window: ScanWindow, fields: FieldSet) -> list[MessageSummary]: ...
    def get_message(self, message_id: str) -> Message: ...
    def search(self, query: SearchQuery, window: ScanWindow) -> list[MessageSummary]: ...
    def get_thread(self, thread_id: str) -> list[Message]: ...

class MailWriteBackend(Protocol):
    def move(self, message_id: str, dest: Mailbox) -> WriteResult: ...
    def mark_read(self, message_id: str, read: bool) -> WriteResult: ...
    def compose(self, draft: DraftSpec) -> WriteResult: ...
    # ... etc

class MailBackend(Protocol):
    read: MailReadBackend
    write: MailWriteBackend
    def invalidate(self, scope: InvalidationScope) -> None: ...
```

Tools then look like:

```python
@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
def list_inbox_emails(account: str, recent_days: float = 2.0, limit: int = 50):
    window = core.bounded_inbox_scan(mailbox=f"INBOX:{account}",
                                     recent_days=recent_days, limit=limit)
    return backend().read.list_messages(window, fields=INBOX_FIELDS)
```

No tool ever sees `run_applescript`, an osascript string, or a `whose` clause again. The seam is the `MailBackend` Protocol.

---

## Read/write asymmetry rules

Standard "read-from-cache, write-through-to-source" invariants (same shape as a database read-replica with a write-master):

1. **Writes are authoritative.** Always go through AppleScript (or future EventKit / MailKit). Never write to the SQLite Envelope Index directly — Mail.app owns that file and rewrites it on sync.
2. **Writes invalidate.** Every `write.*` call returns a `WriteResult` containing the scopes it touched (mailbox(es), account, message ID). The backend's `invalidate(scope)` is called automatically by a decorator on every write method.
3. **Reads tolerate staleness with a TTL.** The SQLite backend caches per-mailbox read results for N seconds (e.g. 5s for inbox lists, 60s for analytics). After a write to that scope, TTL → 0.
4. **Synchronize is a barrier.** `synchronize_account` is the only tool that *forces* a re-open of the SQLite handle (Mail rewrites the DB during sync).
5. **No partial views across the seam.** A tool either reads or writes — never both in one call. Tools that need read-then-write (e.g. `move all from sender X`) call `read.search(...)` then `write.move(...)` explicitly, so invalidation runs between them.

This is the same contract Postgres logical replication, CDN read-through caches, and the GitHub MCP server all use.

---

## Tool contract recommendations

`mcp-builder`'s guidance: "Error messages should guide agents toward solutions with specific suggestions and next steps." A boolean `allow_full_scan=True` fails this — it tells the agent *that* there's a guard but not *what the alternative is*, so agents flip it reflexively.

**Recommendation:** retire `allow_full_scan` from the four current call sites (`compose.py` ×3, `analytics.py` ×1, `inbox.py` ×1). Replace with:

1. **Bounded by default, no escape on the same tool.** Remove the parameter entirely.
2. **Structured error when the bound is missing or zero:**

   ```python
   return ToolError(
       code="UNBOUNDED_SCAN_REQUIRED",
       message="recent_days=0 would scan the entire mailbox.",
       remediation={
           "preferred": "Pass recent_days=7 or 30, or pass message_id directly.",
           "fallback_tool": "full_inbox_export",
           "fallback_tool_args": {"account": account, "format": "ndjson"},
       },
   )
   ```

   This satisfies mcp-builder's `outputSchema` + `structuredContent` recommendation and gives the agent a deterministic next move.

3. **Separate "full scan" tool, explicitly named.** If a true all-time scan is legitimate (export, migration, statistics), expose it as a *different* tool with `destructiveHint=false, readOnlyHint=true, openWorldHint=true` and a description that names the cost ("walks every message; may take minutes; streams progress"). The agent picks it intentionally.

**Opt-in vs opt-out rule:** opt-in belongs on a parameter only when the *default* is the cheap, safe behavior and the opt-in changes a *capability*, not a *cost*. Cost gates belong in tool selection, not parameter selection.

---

## Enforcement strategy: pick **capability token**

Three candidate enforcement mechanisms, ranked:

| Mechanism | Pros | Cons | Verdict |
|---|---|---|---|
| Lint rule (custom flake8 / ruff) | Familiar | Easy to suppress, only catches direct `whose` strings | Reject |
| AST inspection in tests | Catches new tool authors at CI | Brittle, scales poorly, doesn't catch dynamic compositions | Reject |
| **Capability token (`ScanWindow`)** | Enforced by the type system *and* by a runtime guard inside the backend; impossible to construct one without going through `core.bounded_inbox_scan()` | Slightly more boilerplate in tools | **Pick** |

The token pattern (a frozen dataclass that the backend refuses to accept unless `_issued_by == "core.bounded_inbox_scan"`) makes the unsafe path *unrepresentable*. A new tool author literally cannot call `backend().read.list_messages(...)` without first calling `core.bounded_inbox_scan(...)` because there's no other way to obtain a `ScanWindow`. mypy/pyright reject the wrong types; runtime rejects forged tokens. This is the same pattern Rust's typestate uses and the same pattern used by the AWS SDK's `BoundedRequest` types.

One thin AST test as belt-and-suspenders: `tests/test_no_whose.py` greps `tools/*.py` for the literal string `whose ` and the literal string `run_applescript(` — failure to be empty fails CI. Cheap, narrow, catches the only escape hatch.

---

## Helper signature for v1 (AppleScript-only)

```python
# plugin/apple_mail_mcp/core.py
def bounded_inbox_scan(
    *,
    mailbox: str,                    # "INBOX:Account" or "Sent:Account"
    recent_days: float | None = None,# at least one of recent_days / limit required
    limit: int | None = None,        # absolute message-count cap
    since: datetime | None = None,   # explicit cutoff overrides recent_days
) -> ScanWindow:
    """Returns the only object the backend will accept for read.list_messages.

    Validates that the window is bounded (recent_days <= MAX_SCAN_DAYS,
    limit <= MAX_SCAN_LIMIT) and stamps the token with _issued_by.
    """
```

The v1 `AppleScriptBackend.read.list_messages(window, fields)` then builds the AppleScript using `messages 1 thru N` (no `whose`), uses `since` as a Python-side post-filter, and returns typed `MessageSummary` rows.

## Helper signature for v2 (Envelope Index swap)

**Zero signature change.** The `ScanWindow` token already carries everything the SQLite query needs:

```python
class EnvelopeIndexBackend:
    def __init__(self, db_path: Path): ...

    def read_list_messages(self, window: ScanWindow, fields: FieldSet):
        # SELECT ... FROM messages
        # WHERE mailbox_id = ? AND date_received >= ?
        # ORDER BY date_received DESC LIMIT ?
        ...
```

The v2 swap is a one-line change in `__main__.py` or `server.py` (a `BACKEND = EnvelopeIndexBackend(...)` rather than `AppleScriptBackend()`). Because tools only know about `MailBackend`, none of the 27 tools change. Write methods stay on `AppleScriptBackend.write` and the v2 `MailBackend` becomes a *hybrid*: `read = EnvelopeIndexBackend(...)`, `write = AppleScriptBackend(...)`, with `write` calling `read.invalidate(scope)` after every mutation.

This is exactly the read-replica pattern, and the `ScanWindow` token is what makes it backend-agnostic.

---

## MCP-level wins we're missing

1. **`outputSchema` + `structuredContent`** — currently tools return text. Defining Pydantic output models lets clients (Claude Desktop, Claude Code) render and filter results without re-parsing, and lets structured errors round-trip cleanly. Highest-ROI change after the backend refactor.
2. **MCP Resources for static-ish data** — `list_accounts`, `list_mailboxes`, `list_account_addresses` are textbook *resources* (URI-addressable, cacheable on the client side), not tools. Reclassifying them frees tool budget and lets clients pre-fetch.
3. **Progress notifications for long scans** — the proposed `full_inbox_export` tool should emit `notifications/progress` so a 20k-message scan doesn't look hung. FastMCP supports this via the `Context` parameter.
4. **`ToolAnnotations` already present** — keep them; the SQLite swap doesn't change `readOnlyHint`, but it does let us flip `idempotentHint=true` on more reads (SQLite reads truly are).
5. **Session-scoped result caching** — when the SQLite backend lands, `list_mailboxes` results can be cached for the lifetime of the MCP session. mcp-builder doesn't mandate this but it pairs naturally with Resources.

---

## Anti-patterns to avoid (mcp-builder)

- **Boolean cost escapes** (`allow_full_scan=True`) — agents flip them blindly. Replace with structured errors that name an alternative.
- **Tool sprawl after a refactor** — don't split `list_inbox_emails` into `list_inbox_emails_fast` + `list_inbox_emails_full`. Keep one tool, route inside the backend based on the window. mcp-builder favours fewer, clearer tools.
- **Leaky abstraction in tool signatures** — never let backend-specific kwargs (`timeout`, `osascript_path`, `sqlite_pragma`) appear on `@mcp.tool` signatures. They become a forever-API.
- **Dual-source-of-truth writes** — never write to SQLite directly even for "fast" status flips; Mail will overwrite on sync. Writes through AppleScript, full stop.
- **Implicit cache invalidation** — don't rely on TTL alone. Explicit `invalidate(scope)` on every write, enforced by a decorator on `MailWriteBackend` methods.
- **Test-only enforcement** — AST grep tests catch *committed* mistakes; the capability token catches them at write time and at type-check time. Use both, lean on the token.

---

## References

- MCP best practices: `mcp-builder` skill, sections on tool naming/discoverability, actionable errors, `outputSchema`/`structuredContent`, and `ToolAnnotations`.
- Plugin layout: `plugin-dev:plugin-structure` (canonical `plugin/.claude-plugin/plugin.json` + root component dirs); `plugin-dev:mcp-integration` (single stdio server via `plugin.json` is correct here).
- Existing invariant already documented in code: `/Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp/plugin/apple_mail_mcp/core.py` line 621 ("NO `whose` clauses — Mail.app can materialize remote sent mailboxes") — generalize from a comment to a typed contract.
- Current `allow_full_scan=True` sites to retire:
  - `/Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp/plugin/apple_mail_mcp/tools/compose.py` (lines 43, 872, 1447)
  - `/Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp/plugin/apple_mail_mcp/tools/analytics.py` (line 365)
  - `/Users/cayman-mac-mini/Documents/GitHub/apple-mail-mcp/plugin/apple_mail_mcp/tools/inbox.py` (line 362)
- Reference servers reviewed for the layered pattern: Anthropic Filesystem MCP, Anthropic Git MCP, Linear hosted MCP (typed GraphQL resolver layer).
- Read-from-cache/write-through invariants: standard read-replica contract (Postgres logical replication, CDN read-through, GitHub MCP).
- Capability-token pattern: Rust typestate, AWS SDK `BoundedRequest`.
