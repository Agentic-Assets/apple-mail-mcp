# Phase 3 — MCP tool annotation matrix

Canonical presets live in `plugin/apple_mail_mcp/server.py`. Import the matching constant on each `@mcp.tool`; do not hand-roll `ToolAnnotations` in tool modules.

## Presets (`server.py`)

| Constant | readOnly | destructive | idempotent | openWorld | Use for |
|----------|----------|-------------|------------|-----------|---------|
| `READ_ONLY_TOOL_ANNOTATIONS` | true | false | true | true | List, search, read, verify, export, dashboard |
| `WRITE_TOOL_ANNOTATIONS` | false | false | false | true | Compose, reply, forward, move, status, sync, create mailbox |
| `IDEMPOTENT_WRITE_TOOL_ANNOTATIONS` | false | false | true | true | Repeated-safe writes (e.g. flag already set) |
| `DESTRUCTIVE_TOOL_ANNOTATIONS` | false | true | false | true | Trash, permanent delete, empty trash |

## Examples

```python
from apple_mail_mcp.server import mcp, READ_ONLY_TOOL_ANNOTATIONS

@mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
async def list_inbox_emails(...):
    ...
```

## Send tools

`SEND_TOOLS = ("compose_email", "reply_to_email", "forward_email")` — removed when the server starts with `--read-only`. Default plugin wiring passes `--draft-safe` so send paths stay blocked unless the user overrides MCP config.

## Related

- Authoring: [`plugin/apple_mail_mcp/tools/CLAUDE.md`](../../plugin/apple_mail_mcp/tools/CLAUDE.md)
- Deep rules: [`docs/CLAUDE-conventions.md`](../../docs/CLAUDE-conventions.md)
