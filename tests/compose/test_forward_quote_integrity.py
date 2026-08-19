"""Regressions for ``forward_email`` quoted-original integrity (AGENTIC-2363 #4).

``content of foundMessage`` sat inside a bare ``try ... end try``. When that
read threw, ``origContent`` stayed ``""``, the forward body became the
"---------- Forwarded message ----------" header block with nothing under it,
and the tool still returned "Forward saved as draft." — a silently gutted
forward reported as a success.

Emptiness alone cannot be the signal: a subject-only message or an invitation
legitimately has an empty body, and turning that into an error would break
every quiet forward. So the script tracks whether the read itself succeeded
(``origContentRead``) and fails closed only on failure, the same shape the
reply path uses for ``QUOTE_PROOF_UNAVAILABLE``
(``compose/reply_scripts.py``).

Everything is mocked at ``apple_mail_mcp.tools.compose.run_applescript``;
``subprocess.run`` is poisoned so an accidental live ``osascript`` call fails
loudly instead of touching Mail.app.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools

_QUOTE_UNAVAILABLE_OUTPUT = (
    "Error: FORWARD_QUOTE_UNAVAILABLE\n"
    "Detail: Mail could not read the original message body, so the forward would have contained only the "
    "forwarded-header block with the quoted original missing. No draft was saved and nothing was sent. "
    "Retry after Mail finishes downloading the message."
)


def _saved_forward_output(draft_id: str = "84055") -> str:
    return (
        "SAVING FORWARD AS DRAFT\n"
        "\n"
        "Forward saved as draft.\n"
        "To: recipient@example.com\n"
        "Subject: Fwd: Test\n"
        f"Draft ID: {draft_id}\n"
    )


@contextmanager
def _no_live_subprocess():
    """Poison ``subprocess.run`` so an unmocked Mail call fails loudly."""
    with patch(
        "subprocess.run",
        side_effect=AssertionError("test attempted a live osascript call; patch the run_applescript seam"),
    ):
        yield


def _forward(runner, *, verify=None, **kwargs):
    verify_patch = patch(
        "apple_mail_mcp.tools.compose.verify_draft",
        side_effect=verify if verify is not None else (lambda **kw: json.dumps({"found": False})),
    )
    with (
        _no_live_subprocess(),
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=runner),
        verify_patch as verify_mock,
    ):
        result = compose_tools.forward_email(
            account="Work",
            message_id="12345",
            to="recipient@example.com",
            **kwargs,
        )
    return result, verify_mock


def test_forward_fails_closed_when_the_original_body_cannot_be_read():
    """An unreadable original must be an error, not a header-only "success"."""
    result, verify_mock = _forward(lambda script, timeout=120: _QUOTE_UNAVAILABLE_OUTPUT)

    assert result.startswith("Error: FORWARD_QUOTE_UNAVAILABLE")
    assert "Forward saved as draft." not in result
    # No saved artifact exists, so the draft verifier must never be consulted.
    verify_mock.assert_not_called()


def test_forward_script_guards_on_the_read_flag_not_on_emptiness():
    """The guard must key off the failed read, never off an empty body."""
    scripts: list[str] = []

    def runner(script, timeout=120):
        scripts.append(script)
        return _saved_forward_output()

    _forward(runner, verify=lambda **kw: json.dumps({"draft_id": kw["draft_id"], "found": True, "warnings": []}))
    script = next(s for s in scripts if "SAVING FORWARD AS DRAFT" in s)

    assert "set origContentRead to false" in script
    assert "set origContentRead to true" in script
    assert "if not origContentRead then" in script
    assert "FORWARD_QUOTE_UNAVAILABLE" in script
    # Branching on emptiness would reject every legitimately empty original.
    assert 'if origContent is "" then' not in script


def test_quote_guard_runs_before_any_outgoing_message_is_created():
    """Fail closed before Mail has a draft to leave behind."""
    scripts: list[str] = []

    def runner(script, timeout=120):
        scripts.append(script)
        return _saved_forward_output()

    _forward(runner, verify=lambda **kw: json.dumps({"draft_id": kw["draft_id"], "found": True, "warnings": []}))
    script = next(s for s in scripts if "SAVING FORWARD AS DRAFT" in s)

    assert script.index("if not origContentRead then") < script.index("make new outgoing message")


def test_forward_with_a_genuinely_empty_original_still_saves_cleanly():
    """A subject-only original is not an error: no spurious failure."""
    result, verify_mock = _forward(
        lambda script, timeout=120: _saved_forward_output(),
        verify=lambda **kw: json.dumps({"draft_id": kw["draft_id"], "found": True, "warnings": []}),
        include_signature=False,
    )

    assert "Forward saved as draft." in result
    assert "FORWARD_QUOTE_UNAVAILABLE" not in result
    assert "Verified Draft ID: 84055" in result
    verify_mock.assert_called_once()


def test_lead_message_alone_cannot_stand_in_for_the_quoted_original():
    """A caller-supplied lead message must not mask the missing quote."""
    result, _ = _forward(
        lambda script, timeout=120: _QUOTE_UNAVAILABLE_OUTPUT,
        message="Please review the thread below.",
    )

    assert result.startswith("Error: FORWARD_QUOTE_UNAVAILABLE")
    assert "Forward saved as draft." not in result
