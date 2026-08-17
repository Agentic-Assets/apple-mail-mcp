"""The ``@mcp.tool`` ToolError boundary (AGENTIC-2369, second half).

``core.run_applescript`` gained an ``INVALID_TIMEOUT`` guard that raises
``ToolError`` from below every tool body. ``tools/CLAUDE.md`` documents
structured errors as JSON with ``code`` / ``message`` / ``remediation``, and
that was true only for the calendar surface: every calendar tool has its own
``except ToolError`` arm, and no mail tool has one. So the same caller bug
produced two different shapes depending on which surface you hit —
``list_inbox_emails(timeout=0)`` raised past the tool while
``list_calendars(timeout=0)`` returned the envelope.

Not a silent failure — a raise is loud — but a contract mismatch, and one this
branch introduced. Before the guard, ``list_inbox_emails(timeout=0)`` returned
an envelope whose *message* was wrong ("timed out"), blaming Mail.app for a
caller bug. The guard fixed the message and broke the shape. These tests pin
both: right message, right shape.

``server._envelope_tool_errors`` runs at the one registration seam every tool
shares. The tests below cover the four ways that can go wrong:

1. the envelope must actually appear on the mail surface;
2. the calendar surface must not get it *twice* (its own handler already ran);
3. a successful call must be untouched; and
4. a non-``ToolError`` exception must still propagate — converting arbitrary
   failures into tidy JSON is precisely the silent-failure pattern this branch
   exists to remove.

Plus the structural risk that outranks the bug itself: FastMCP builds each
tool's input schema by introspecting the registered function, so a wrapper that
loses the signature would ship 41 tools with empty schemas.

Nothing here touches Mail.app or Calendar.app: ``subprocess.run`` is poisoned
in every path that could reach osascript.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any
from unittest.mock import patch

import pytest
from apple_mail_mcp.backend.base import ToolError
from apple_mail_mcp.server import _envelope_tool_errors, mcp
from apple_mail_mcp.tools.calendar import list_calendars
from apple_mail_mcp.tools.inbox import list_inbox_emails, list_mailboxes

# Tools whose declared return type is a container (``list[str]`` /
# ``dict[...]``), not text. FastMCP validates their result against a generated
# structured-output schema, so handing them a JSON string turns the error into
# a pydantic "Input should be a valid list" message with the real code buried
# in ``input_value`` — worse than the raise it replaced. They keep raising.
CONTAINER_RETURN_TOOLS = {
    "list_accounts",
    "list_account_addresses",
    "get_mailbox_unread_counts",
}


def _poison_subprocess():
    """Fail loudly if anything in the call path reaches a live osascript."""
    return patch(
        "subprocess.run",
        side_effect=AssertionError("test attempted a live osascript call; the timeout guard should have refused"),
    )


def _assert_invalid_timeout_envelope(result: Any, tool_name: str) -> dict[str, Any]:
    """Assert *result* is the documented JSON envelope, serialized exactly once."""
    assert isinstance(result, str), f"{tool_name} must return the envelope as text, got {type(result).__name__}"
    payload = json.loads(result)
    assert isinstance(payload, dict), (
        f"{tool_name} returned JSON that decodes to {type(payload).__name__}, not an object — "
        "that is a double-serialized envelope (a JSON string containing JSON)"
    )
    assert payload["error"] is True
    assert payload["code"] == "INVALID_TIMEOUT"
    assert "must be greater than 0" in payload["message"], payload["message"]
    assert payload["remediation"], "the envelope must carry actionable remediation"
    return payload


# ---------------------------------------------------------------------------
# 1. The mail surface now honours the documented envelope
# ---------------------------------------------------------------------------


def test_async_mail_tool_returns_documented_envelope_for_zero_timeout():
    """The exact reported escape: ``list_inbox_emails(timeout=0)``.

    ``timeout=0`` reaches ``run_applescript`` through the
    ``min(timeout, 30)`` validation-timeout idiom, so the caller does not have
    to do anything exotic to hit it.
    """
    with _poison_subprocess():
        result = asyncio.run(list_inbox_emails(account="Work", timeout=0))
    _assert_invalid_timeout_envelope(result, "list_inbox_emails")


def test_sync_mail_tool_returns_documented_envelope_for_zero_timeout():
    """The boundary must cover sync tools too, not just the eight async ones."""
    with _poison_subprocess():
        result = list_mailboxes(timeout=0)
    _assert_invalid_timeout_envelope(result, "list_mailboxes")


def test_envelope_message_names_the_argument_not_a_mailbox_timeout():
    """Right message *and* right shape.

    The pre-guard behaviour returned a well-shaped envelope that said the call
    "timed out" — blaming Mail.app for a caller bug. Guard against a
    regression to that wording.
    """
    with _poison_subprocess():
        payload = _assert_invalid_timeout_envelope(list_mailboxes(timeout=0), "list_mailboxes")
    assert "timed out" not in payload["message"].lower()
    assert "timeout" in payload["message"]


# ---------------------------------------------------------------------------
# 2. The calendar surface must not be double-serialized
# ---------------------------------------------------------------------------


def test_calendar_tool_envelope_is_emitted_exactly_once(monkeypatch):
    """Calendar tools already catch ``ToolError``; the boundary must not re-wrap.

    A double-serialized envelope decodes to a *string* rather than an object,
    which is what ``_assert_invalid_timeout_envelope`` checks.
    """

    class _RaisingEngine:
        name = "applescript"

        def list_calendars(self, timeout: int | None = None) -> tuple[list[dict], list[str]]:
            raise ToolError(
                code="INVALID_TIMEOUT",
                message="timeout must be greater than 0 seconds; got 0.",
                remediation={"hint": "Pass a positive number of seconds."},
            )

        def default_calendar_name(self) -> str | None:
            return None

    monkeypatch.setattr("apple_mail_mcp.tools.calendar.get_engine", lambda: _RaisingEngine())
    monkeypatch.setattr(
        "apple_mail_mcp.tools.calendar.eventkit_status",
        lambda: (False, "dependency_missing"),
    )
    with _poison_subprocess():
        result = list_calendars(timeout=0)
    _assert_invalid_timeout_envelope(result, "list_calendars")


# ---------------------------------------------------------------------------
# 3. A successful call is completely unaffected
# ---------------------------------------------------------------------------


def test_successful_tool_call_passes_through_untouched(monkeypatch):
    """The boundary must be invisible on the happy path."""
    sentinel = "MAILBOXES\n\n📂 Inbox\n"
    calls: list[dict[str, Any]] = []

    def _fake_run(script: str, timeout: int | None = None) -> str:
        calls.append({"timeout": timeout})
        return sentinel

    monkeypatch.setattr("apple_mail_mcp.tools.inbox.run_applescript", _fake_run)
    with _poison_subprocess():
        result = list_mailboxes(timeout=30)
    assert "📂 Inbox" in result
    assert '"error": true' not in result
    assert calls == [{"timeout": 30}], "arguments must reach the tool body unmodified"


def test_boundary_forwards_positional_and_keyword_arguments():
    """``functools.wraps`` preserves metadata; this pins argument passing."""
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def tool(a: int, b: int = 2, *, c: int = 3) -> str:
        seen.append(((a, b), {"c": c}))
        return "ok"

    wrapped = _envelope_tool_errors(tool)
    assert wrapped(1, 5, c=9) == "ok"
    assert seen == [((1, 5), {"c": 9})]


# ---------------------------------------------------------------------------
# 4. The mirror image: non-ToolError failures are NOT swallowed
# ---------------------------------------------------------------------------


def test_non_tool_error_from_a_real_tool_is_not_swallowed(monkeypatch):
    """A ``RuntimeError`` below the tool must still reach the caller.

    Turning arbitrary exceptions into tidy JSON would be a *new* silent
    channel, which is the exact defect class this branch removes.
    """

    def _boom(script: str, timeout: int | None = None) -> str:
        raise RuntimeError("unmapped failure")

    monkeypatch.setattr("apple_mail_mcp.tools.inbox.run_applescript", _boom)
    with _poison_subprocess(), pytest.raises(RuntimeError, match="unmapped failure"):
        list_mailboxes(timeout=30)


@pytest.mark.parametrize("exc", [RuntimeError("boom"), ValueError("bad"), KeyError("missing")])
def test_boundary_only_converts_tool_error(exc):
    def tool() -> str:
        raise exc

    with pytest.raises(type(exc)):
        _envelope_tool_errors(tool)()


def test_boundary_only_converts_tool_error_on_async_tools():
    async def tool() -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_envelope_tool_errors(tool)())


def test_base_exception_is_not_intercepted():
    """``KeyboardInterrupt`` / ``SystemExit`` must never be turned into JSON."""

    def tool() -> str:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _envelope_tool_errors(tool)()


# ---------------------------------------------------------------------------
# The structural risk: the generated MCP schema must survive the wrapper
# ---------------------------------------------------------------------------


def _registered_tools():
    return mcp._tool_manager.list_tools()  # type: ignore[attr-defined]


def test_registry_still_holds_every_tool():
    assert len(_registered_tools()) == 41


def test_every_registered_tool_schema_matches_its_function_signature():
    """Load-bearing: a naive ``*args, **kwargs`` wrapper empties every schema.

    FastMCP builds ``inputSchema`` from the function it is handed, so this
    compares the published property names against the parameters of the
    underlying tool function for all 41 tools at once.
    """
    for tool in _registered_tools():
        original = getattr(tool.fn, "__wrapped__", tool.fn)
        expected = {
            name
            for name, param in inspect.signature(original).parameters.items()
            if param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD) and name != tool.context_kwarg
        }
        published = set(tool.parameters.get("properties", {}))
        assert published == expected, f"{tool.name} schema drifted: {published ^ expected}"
        assert published, f"{tool.name} published an empty input schema"
        assert "args" not in published and "kwargs" not in published


def test_registered_tools_keep_their_name_docstring_and_async_flag():
    for tool in _registered_tools():
        original = getattr(tool.fn, "__wrapped__", tool.fn)
        assert tool.fn.__name__ == original.__name__
        assert tool.fn.__doc__ == original.__doc__
        assert tool.is_async is inspect.iscoroutinefunction(original), (
            f"{tool.name}: a sync wrapper around an async tool would return a coroutine"
        )


def test_async_tools_are_still_coroutine_functions():
    """Eight tools are ``async def``; a sync wrapper would break all of them."""
    async_names = {tool.name for tool in _registered_tools() if tool.is_async}
    assert async_names == {
        "check_availability",
        "full_inbox_export",
        "get_events_by_id",
        "get_inbox_overview",
        "inbox_dashboard",
        "list_events",
        "list_inbox_emails",
        "search_emails",
    }
    for tool in _registered_tools():
        if tool.is_async:
            assert inspect.iscoroutinefunction(tool.fn)


def test_container_return_tools_are_the_only_boundary_exceptions():
    """Pin the residual inconsistency so it cannot grow silently.

    These three declare container return types, so the envelope would fail
    FastMCP's structured-output validation. Every other tool is wrapped.
    """
    unwrapped = {tool.name for tool in _registered_tools() if not hasattr(tool.fn, "__wrapped__")}
    assert unwrapped == CONTAINER_RETURN_TOOLS
