"""HTML compose must not Tab-indent the first body line.

Mail's WebKit compose field treats Tab as first-paragraph indent once the
caret is already in the body. Focus may Tab only while Accessibility
reports a header field, and must return immediately when the body already
has focus.
"""

from pathlib import Path
from unittest.mock import patch

from apple_mail_mcp.tools import compose as compose_tools


def assert_html_compose_focus_never_tabs_into_body(script: str) -> None:
    """Lock the HTML-compose focus contract used by body_html and attachment drafts."""
    assert "on focusComposeBody(theMarker)" in script
    assert 'if not my focusComposeBody(temporarySubjectMarker) then error "COMPOSE_BODY_FOCUS_FAILED"' in script
    assert "first window whose name contains theMarker" in script
    assert "set composeWindow to front window" not in script
    assert 'every UI element of composeWindow whose role is "AXWebArea"' not in script
    assert "set allElements to entire contents of composeWindow" in script
    assert "repeat with candidateElement in allElements" in script
    assert 'if candidateRole is "AXTextArea" then' in script
    assert 'else if candidateRole is "AXWebArea" and webAreaFallback is missing value then' in script
    assert 'perform action "AXFocus" of composeEditor' in script
    assert "click composeEditor" in script
    assert "headerRoles contains focusedRole" in script
    assert "repeat 7 times" not in script
    body_return = script.index('if focusedRole is "AXWebArea" or focusedRole is "AXTextArea" then return true')
    header_guard = script.index("headerRoles contains focusedRole")
    tab_press = script.index("key code 48")
    assert body_return < tab_press
    assert header_guard < tab_press
    assert script.index("focusComposeBody(temporarySubjectMarker)") < script.index('keystroke "v" using command down')


def test_html_compose_focus_never_presses_tab_before_paste() -> None:
    """body_html compose is the Joanna-email path: paste must not follow unguarded Tabs."""
    captured: dict[str, str] = {}

    def fake_run(script: str, timeout: int = 120) -> str:
        captured["script"] = script
        return "Email saved as draft (HTML)"

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        result = compose_tools.compose_email(
            account="Work",
            to="joanna@example.com",
            subject="Referral agreement",
            body="Hi Joanna,",
            body_html="<p>Hi Joanna,</p>",
            mode="draft",
        )

    assert "Email saved as draft (HTML)" in result
    assert_html_compose_focus_never_tabs_into_body(captured["script"])


def test_html_compose_returns_immediately_when_body_editor_already_has_focus() -> None:
    """Do not Tab if AX already reports the compose body is focused."""
    captured: dict[str, str] = {}

    def fake_run(script: str, timeout: int = 120) -> str:
        captured["script"] = script
        return "Email opened in Mail for review (HTML). Edit and send when ready."

    with patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run):
        compose_tools.compose_email(
            account="Work",
            to="joanna@example.com",
            subject="Referral agreement",
            body="Hi Joanna,",
            body_html="<p>Hi Joanna,</p>",
            mode="open",
        )

    script = captured["script"]
    assert_html_compose_focus_never_tabs_into_body(script)
    already_focused = script.index('if focusedRole is "AXWebArea" or focusedRole is "AXTextArea" then return true')
    tab_press = script.index("key code 48")
    assert already_focused < tab_press


def test_attachment_html_path_uses_the_same_no_tab_focus_handler(tmp_path: Path) -> None:
    """Plain-text + attachments also paste through the HTML writer."""
    attachment = tmp_path / "report.pdf"
    attachment.write_text("report")
    scripts: list[str] = []

    def fake_run(script: str, timeout: int = 120) -> str:
        scripts.append(script)
        return "Email saved as draft (HTML)\nDraft ID: 84053\n"

    with (
        patch("apple_mail_mcp.tools.compose.run_applescript", side_effect=fake_run),
        patch(
            "apple_mail_mcp.tools.compose._validate_attachment_paths",
            return_value=([str(attachment)], None),
        ),
    ):
        compose_tools.compose_email(
            account="Work",
            to="recipient@example.com",
            subject="Report",
            body="Please review.",
            attachments=str(attachment),
            mode="draft",
        )

    compose_script = next(script for script in scripts if "on focusComposeBody(theMarker)" in script)
    assert_html_compose_focus_never_tabs_into_body(compose_script)
