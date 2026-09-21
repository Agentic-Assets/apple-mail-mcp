"""Per-reason operator next steps for the EventKit read fast path.

``eventkit_status()`` returns a terse reason label; this module maps every
known label to a human-actionable next step so a bare ``write_only`` or
``not_determined`` never dead-ends an operator. The consent prompt itself
stays exclusively in the human-run ``apple-mail calendar-grant`` CLI command;
this module only describes what to run, never requesting access itself.
"""

from __future__ import annotations

GRANT_COMMAND = "apple-mail calendar-grant"
PLUGIN_GRANT_COMMAND = "PYTHONPATH=<plugin dir> venv/bin/python3 -m apple_mail_mcp.cli calendar-grant"
CALENDARS_PANE = "System Settings > Privacy & Security > Calendars"

_STATUS_CHECK_FAILED_STEP = (
    "The EventKit authorization check itself failed. Confirm the plugin "
    f"venv is healthy, then re-run '{GRANT_COMMAND}' from a terminal; "
    "report the failure text if it persists."
)

_NEXT_STEPS: dict[str, str] = {
    "full_access": "The EventKit read fast path is active; no action needed.",
    "dependency_missing": (
        "The plugin install is missing the bundled EventKit dependency. "
        "Reinstall the current release; if it persists, run "
        f"'{GRANT_COMMAND}' once from a terminal to confirm."
    ),
    "not_determined": (
        "Calendars access has never been requested for this host. Run "
        f"'{GRANT_COMMAND}' once from a terminal and grant Full Access when "
        f"macOS prompts. (Plugin installs: {PLUGIN_GRANT_COMMAND}.)"
    ),
    "write_only": (
        "This host has write-only Calendars access, which cannot back the read "
        f"fast path. Re-run '{GRANT_COMMAND}' from a terminal and choose Full "
        "Access in the macOS prompt; if no prompt appears, open "
        f"{CALENDARS_PANE} and set the host app to Full Access."
    ),
    "denied": (
        f"Calendars access is denied. Enable Full Access under {CALENDARS_PANE} "
        "for the app that launches the server, or run: tccutil reset Calendar "
        f"and then '{GRANT_COMMAND}' from a terminal."
    ),
    "restricted": (
        f"Calendars access is restricted by policy. {CALENDARS_PANE} cannot "
        "override this; contact the device administrator."
    ),
}


def eventkit_next_step(reason: str) -> str:
    """Return the operator's next step for an ``eventkit_status()`` reason label.

    Handles the ``dependency_missing: ...`` and ``status_check_failed: ...``
    prefixed forms plus bare labels and unknown future ``status_{N}`` values.
    """
    base, _, _ = reason.partition(":")
    if base in _NEXT_STEPS:
        return _NEXT_STEPS[base]
    if base == "status_check_failed":
        return f"{_STATUS_CHECK_FAILED_STEP} ({reason})"
    return (
        "Unrecognized Calendars authorization state. Open "
        f"{CALENDARS_PANE} for the host app and re-run '{GRANT_COMMAND}' "
        f"from a terminal. ({reason})"
    )


def eventkit_denied_remediation(reason: str) -> dict[str, str]:
    """Return the forced-EventKit remediation dict for a denial ``reason``."""
    return {
        "pane": CALENDARS_PANE,
        "grant": f"Run '{GRANT_COMMAND}' from a terminal to request full access once.",
        "next_step": eventkit_next_step(reason),
        "fallback": "Unset APPLE_MAIL_CALENDAR_ENGINE to use the AppleScript engine.",
    }


__all__ = [
    "CALENDARS_PANE",
    "GRANT_COMMAND",
    "PLUGIN_GRANT_COMMAND",
    "eventkit_denied_remediation",
    "eventkit_next_step",
]
