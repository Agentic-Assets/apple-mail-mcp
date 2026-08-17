"""
Apple Mail MCP Dashboard UI Module

Provides functions to create UI resources for the inbox dashboard.
"""

import json
from pathlib import Path
from typing import Any

from mcp_ui_server import create_ui_resource


def _embed_json(value: Any) -> str:
    """Serialize *value* for injection into the template's inline ``<script>``.

    ``json.dumps`` alone is not safe in an HTML script context: it leaves ``</``
    intact, so a subject, sender, account, or mailbox-error message containing
    ``</script>`` would close the element early and let the remainder render as
    markup. Escaping ``</`` keeps the value an inert JS string literal (``<\\/``
    decodes back to ``</``), and U+2028/U+2029 are escaped because JavaScript —
    unlike JSON — treats them as line terminators. The template still routes
    every field through its ``escapeHtml`` helper before it reaches the DOM.
    """
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def create_inbox_dashboard_ui(
    accounts_data: dict[str, int],
    recent_emails: list[dict[str, Any]],
    scan_errors: list[dict[str, str]] | None = None,
) -> Any:
    """
    Create a UI resource for the Apple Mail inbox dashboard.

    Args:
        accounts_data: Dictionary mapping account names to unread email counts.
                      Example: {"Gmail": 5, "Work": 12, "Personal": 3}
        recent_emails: List of recent email dictionaries with keys:
                      - subject: Email subject line
                      - sender: Sender name/email
                      - date: Date string
                      - is_read: Boolean indicating read status
                      - account: (optional) Account name
                      - preview: (optional) Email preview text
        scan_errors: Optional recent-email scan diagnostics, as collected by
                      ``inbox_dashboard`` into its ``error_details`` sink:
                      ``[{"account", "type": "mailbox_error"|"timeout",
                      "message"}]``. When non-empty the page shows a warning
                      banner and replaces the "Inbox Zero" empty state, because
                      a failed scan returns the same empty list as a quiet
                      mailbox. Default/empty renders no warning at all, so a
                      quiet mailbox never looks broken.

    Returns:
        UIResource with uri "ui://apple-mail/inbox-dashboard"
    """
    # Get the template file path
    template_path = Path(__file__).parent / "templates" / "dashboard.html"

    # Read the HTML template
    template_content = template_path.read_text(encoding="utf-8")

    # Inject data into the template.
    #
    # `var`, not `const`, and not by preference: the template's
    # "fallback if data not injected" block declares the same three identifiers
    # with `var` inside an `if`, and `var` hoists out of the block to script
    # scope. `const x` here plus `var x` there is a duplicate lexical
    # declaration, which is a **parse-time** SyntaxError — the entire inline
    # script never runs, so accounts, emails, and diagnostics all render as an
    # empty shell. `var` twice is legal, and the `typeof` guard then leaves the
    # injected value alone. Verified with `node --check`; locked by
    # tests/analytics/test_dashboard_ui_diagnostics.py.
    html_content = (
        template_content.replace(
            "/* ACCOUNTS_DATA_PLACEHOLDER */",
            f"var accountsData = {_embed_json(accounts_data)};",
        )
        .replace(
            "/* EMAILS_DATA_PLACEHOLDER */",
            f"var recentEmails = {_embed_json(recent_emails)};",
        )
        .replace(
            "/* SCAN_ERRORS_DATA_PLACEHOLDER */",
            f"var scanErrors = {_embed_json(scan_errors or [])};",
        )
    )

    # Create and return the UI resource
    return create_ui_resource(
        {
            "uri": "ui://apple-mail/inbox-dashboard",
            "content": {"type": "rawHtml", "htmlString": html_content},
            "encoding": "text",
        }
    )
