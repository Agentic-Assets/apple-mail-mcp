"""``inbox_dashboard`` scan diagnostics on the **default** (``output_format="ui"``) path.

``tests/analytics/test_dashboard_scan_diagnostics.py`` locks the silent-zero fix
for JSON callers: an AppleScript throw becomes an ``ERROR_MAILBOX|||`` marker row
that lands in ``errors`` / ``error_details`` instead of an authoritative
``"recent_emails": []``.

``output_format`` defaults to ``"ui"``, so most callers never see that payload.
The UI branch collected the same diagnostics and then dropped them, rendering the
list's "Inbox Zero" empty state — the same lie the JSON fix removed. These tests
lock the diagnostics through to the rendered dashboard, and lock the
mirror-image regression: a genuinely quiet mailbox must still render clean, with
no warning at all.

Rendering is client-side JS built from JSON injected into the template (the same
channel as ``subject`` / ``sender``), so the escaping assertions here are
structural — the renderer must route error text through the template's existing
``escapeHtml`` — plus one behavioral check that no field can terminate the
inline ``<script>`` element it is injected into.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apple_mail_mcp.core import AppleScriptTimeout
from apple_mail_mcp.tools import analytics as analytics_tools

try:  # pragma: no cover - depends on optional dashboard runtime
    from ui import create_inbox_dashboard_ui

    _UI_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - exercised only where UI deps are absent
    create_inbox_dashboard_ui = None  # type: ignore[assignment]
    _UI_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

_DECLARATION_RE = re.compile(r"\b(const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", re.MULTILINE)

_MAILBOX_ERROR = "No inbox mailbox found for account Work"
_MARKER_ROW = f"ERROR_MAILBOX|||Work|||{_MAILBOX_ERROR}"
_EMAIL_ROW = "Subject|||sender@example.com|||Date|||false|||Work|||INBOX|||101|||<a@example.com>|||false|||"


def _inline_script(html: str) -> str:
    """Return the template's inline ``<script>`` body (the CDN tag has attributes)."""
    return html.rsplit("<script>", 1)[1].split("</script>", 1)[0]


def _dashboard_ui(runner, **kwargs):
    """Run ``inbox_dashboard`` on the default UI path, capturing the UI call kwargs."""
    captured: dict[str, object] = {}

    def fake_ui(**ui_kwargs):
        captured.update(ui_kwargs)
        return {"ok": True}

    options: dict[str, object] = {"account": "Work"}
    options.update(kwargs)
    with (
        patch("apple_mail_mcp.UI_AVAILABLE", True),
        patch.dict(sys.modules, {"ui": SimpleNamespace(create_inbox_dashboard_ui=fake_ui)}),
        patch("apple_mail_mcp.tools.inbox.get_mailbox_unread_counts", return_value={"Work": 1}),
        patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=runner),
    ):
        result = asyncio.run(analytics_tools.inbox_dashboard(**options))
    return result, captured


def _scan_errors_kwarg(captured: dict[str, object]) -> list[dict[str, str]]:
    """Return the diagnostics kwarg the UI branch handed to the renderer."""
    for key in ("scan_errors", "error_details", "errors"):
        if key in captured:
            value = captured[key]
            assert isinstance(value, list)
            return value
    raise AssertionError(f"UI call carried no diagnostics kwarg; got keys {sorted(captured)}")


class DashboardUiBranchPassesDiagnosticsTests(unittest.TestCase):
    """The UI branch must forward the diagnostics it already collects."""

    def test_ui_call_receives_the_collected_mailbox_error(self):
        result, captured = _dashboard_ui(lambda script, timeout=None: _MARKER_ROW)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(captured["recent_emails"], [])
        self.assertEqual(
            _scan_errors_kwarg(captured),
            [{"account": "Work", "type": "mailbox_error", "message": _MAILBOX_ERROR}],
        )

    def test_ui_call_receives_a_scan_timeout(self):
        def runner(script, timeout=None):
            raise AppleScriptTimeout("osascript timed out")

        _result, captured = _dashboard_ui(runner)

        self.assertEqual(captured["recent_emails"], [])
        self.assertEqual([item["type"] for item in _scan_errors_kwarg(captured)], ["timeout"])

    def test_quiet_mailbox_forwards_no_diagnostics(self):
        """Mirror-image guard: an empty scan with no failure stays clean."""
        _result, captured = _dashboard_ui(lambda script, timeout=None: "")

        self.assertEqual(captured["recent_emails"], [])
        self.assertEqual(_scan_errors_kwarg(captured), [])

    def test_populated_inbox_forwards_rows_and_no_diagnostics(self):
        _result, captured = _dashboard_ui(lambda script, timeout=None: _EMAIL_ROW)

        self.assertEqual(len(captured["recent_emails"]), 1)
        self.assertEqual(_scan_errors_kwarg(captured), [])


@unittest.skipIf(create_inbox_dashboard_ui is None, f"dashboard UI runtime unavailable ({_UI_IMPORT_ERROR})")
class DashboardUiRenderTests(unittest.TestCase):
    """The rendered page must make a failed scan unmistakable — and only then."""

    @staticmethod
    def _render(**kwargs) -> str:
        options: dict[str, object] = {"accounts_data": {"Work": 3}, "recent_emails": []}
        options.update(kwargs)
        return create_inbox_dashboard_ui(**options).resource.text

    def _render_with_errors(self, errors: list[dict[str, str]], **kwargs) -> str:
        """Render with diagnostics, whatever the accepted kwarg is named."""
        for key in ("scan_errors", "error_details", "errors"):
            try:
                return self._render(**{key: errors}, **kwargs)
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
        raise AssertionError("create_inbox_dashboard_ui accepts no diagnostics parameter")

    def test_existing_two_argument_callers_still_work(self):
        """The parameter must be optional: every current caller omits it."""
        html = create_inbox_dashboard_ui({"Work": 3}, []).resource.text

        self.assertIn("Inbox Dashboard", html)

    def test_failed_scan_injects_the_diagnostic_into_the_page(self):
        html = self._render_with_errors([{"account": "Work", "type": "mailbox_error", "message": _MAILBOX_ERROR}])

        self.assertIn(_MAILBOX_ERROR, html)
        self.assertIn("scanErrors", html)

    def test_failed_scan_renders_a_visible_warning_element(self):
        html = self._render_with_errors([{"account": "Work", "type": "mailbox_error", "message": _MAILBOX_ERROR}])

        self.assertIn('id="scanWarning"', html)
        self.assertIn("renderScanWarning", html)

    def test_empty_list_with_a_failed_scan_does_not_claim_inbox_zero(self):
        """The empty-state copy is the exact thing that lies today."""
        html = self._render_with_errors([{"account": "Work", "type": "mailbox_error", "message": _MAILBOX_ERROR}])

        # The empty-state branch must consult the diagnostics rather than
        # unconditionally rendering the "Inbox Zero" checkmark card.
        self.assertRegex(html, r"container\.innerHTML = hasScanErrors\(\) \?")
        self.assertIn("Scan Incomplete", html)
        self.assertIn("not a confirmed empty inbox", html)

    def test_quiet_mailbox_renders_a_clean_empty_state(self):
        """No errors: no banner, and the Inbox Zero card is untouched."""
        html = self._render()

        self.assertIn("var scanErrors = [];", html)
        self.assertIn("Inbox Zero", html)
        # The banner ships hidden and is only revealed from data.
        self.assertRegex(html, r'id="scanWarning"[^>]*\shidden')

    def test_error_text_is_escaped_through_the_template_escaper(self):
        """Same mechanism the template already uses for subject / sender."""
        html = self._render_with_errors([{"account": "Work", "type": "mailbox_error", "message": _MAILBOX_ERROR}])

        self.assertRegex(html, r"escapeHtml\(\s*account\s*\)")
        self.assertRegex(html, r"escapeHtml\(\s*message\s*\)")

    def test_no_injected_field_can_terminate_the_inline_script(self):
        """A mailbox name is user-controlled; it must not close the <script>."""
        hostile = '</script><img src=x onerror="boom()">'
        baseline = self._render().count("</script>")

        html = self._render_with_errors([{"account": hostile, "type": "mailbox_error", "message": hostile}])

        self.assertEqual(html.count("</script>"), baseline)

    def test_injected_declarations_do_not_collide_with_the_template_fallbacks(self):
        """`const x` injected + the template's `var x` fallback is a SyntaxError.

        The fallback block declares every injected data identifier with ``var``
        inside an ``if``, and ``var`` hoists out of the block to script scope.
        Mixing keywords for one identifier fails at **parse** time, so nothing
        in the inline script runs: no accounts, no emails, and no diagnostics
        banner. Every declaration of these identifiers must use ``var``.
        """
        script = _inline_script(
            self._render_with_errors(
                [{"account": "Work", "type": "timeout", "message": "slow"}],
                disclosure={"unread_count_measured": False, "unread_count_note": "Cached. Drifts low."},
            )
        )

        for name in ("accountsData", "recentEmails", "scanErrors", "unreadDisclosure"):
            keywords = {kind for kind, ident in _DECLARATION_RE.findall(script) if ident == name}
            self.assertEqual(keywords, {"var"}, f"{name} declared with {sorted(keywords)}")


class DashboardUiCarriesUnreadProvenanceTests(unittest.TestCase):
    """The account-card numbers are Mail's cached aggregate, and must say so.

    ``unread_count_disclosure()`` reached the JSON payload and stopped there: the
    UI branch popped the sentinel out of the account map and then called the
    renderer without it. So the release note claiming ``inbox_dashboard`` reports
    provenance was true only on a format nobody passes, while the default page —
    the one a person actually looks at — rendered a count measured 68% low on a
    real Exchange mailbox as a bare badge.
    """

    def test_ui_branch_forwards_the_disclosure(self):
        _result, captured = _dashboard_ui(lambda script, timeout=None: "")

        disclosure = captured.get("disclosure")
        self.assertIsInstance(disclosure, dict, f"UI call carried no disclosure; got keys {sorted(captured)}")
        self.assertIn("unread_count_source", disclosure)
        self.assertIs(disclosure["unread_count_measured"], False)

    @unittest.skipIf(create_inbox_dashboard_ui is None, f"dashboard UI runtime unavailable ({_UI_IMPORT_ERROR})")
    def test_rendered_page_carries_the_note_text(self):
        note = "Unread totals are Mail.app's cached aggregate. It drifts low."
        html = create_inbox_dashboard_ui(
            {"Work": 3},
            [],
            disclosure={
                "unread_count_source": "mail_cached_aggregate",
                "unread_count_measured": False,
                "unread_count_note": note,
            },
        ).resource.text

        self.assertIn("unreadDisclosure", html)
        self.assertIn("mail_cached_aggregate", html)
        self.assertIn('id="countProvenance"', html)
        self.assertIn("renderCountProvenance", html)

    @unittest.skipIf(create_inbox_dashboard_ui is None, f"dashboard UI runtime unavailable ({_UI_IMPORT_ERROR})")
    def test_absent_disclosure_claims_nothing_either_way(self):
        """Unknown provenance is not the same as measured, and not a reason to warn."""
        html = create_inbox_dashboard_ui({"Work": 3}, []).resource.text

        self.assertIn("var unreadDisclosure = {}", html)
        self.assertRegex(html, r"measured !== false")


@unittest.skipIf(create_inbox_dashboard_ui is None, f"dashboard UI runtime unavailable ({_UI_IMPORT_ERROR})")
@unittest.skipIf(shutil.which("node") is None, "node not available for a JS parse check")
class DashboardUiScriptParsesTests(unittest.TestCase):
    """The rendered inline script must actually parse.

    A parse error is the worst version of this defect class: the page still
    renders its static shell — header, "Recent Emails", "0 emails" — while every
    renderer, including the diagnostics banner, silently never runs. Substring
    assertions cannot see that, so parse the emitted script the way a browser
    would.
    """

    def _assert_parses(self, html: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script_path = Path(tmp) / "dashboard_inline.js"
            script_path.write_text(_inline_script(html), encoding="utf-8")
            result = subprocess.run(
                ["node", "--check", str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, f"node --check failed:\n{result.stderr}")

    def test_quiet_render_parses(self):
        self._assert_parses(create_inbox_dashboard_ui({"Work": 0}, []).resource.text)

    def test_render_with_rows_and_diagnostics_parses(self):
        html = create_inbox_dashboard_ui(
            {"Work": 2},
            [{"subject": "Subject", "sender": "sender@example.com", "date": "Date", "is_read": False}],
            scan_errors=[{"account": "Work", "type": "mailbox_error", "message": "scan failed"}],
        ).resource.text

        self._assert_parses(html)

    def test_render_with_a_disclosure_parses(self):
        html = create_inbox_dashboard_ui(
            {"Work": 2},
            [],
            disclosure={
                "unread_count_source": "mail_cached_aggregate",
                "unread_count_measured": False,
                "unread_count_note": "Cached. It drifts low. Do not derive a read count from it.",
            },
        ).resource.text

        self._assert_parses(html)

    def test_render_with_script_breaking_text_parses(self):
        hostile = '</script><img src=x onerror="boom()">'
        html = create_inbox_dashboard_ui(
            {hostile: 1},
            [{"subject": hostile, "sender": hostile}],
            scan_errors=[{"account": hostile, "type": "mailbox_error", "message": hostile}],
        ).resource.text

        self._assert_parses(html)


if __name__ == "__main__":
    unittest.main()
