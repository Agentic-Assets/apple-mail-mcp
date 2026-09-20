"""Per-reason operator next steps for the EventKit read fast path.

``eventkit_status()`` returns a terse reason label; this module maps every
known label to a human-actionable next step so a bare ``write_only`` or
``not_determined`` never dead-ends an operator. The consent prompt itself
stays exclusively in the human-run ``apple-mail calendar-grant`` CLI command;
this module only describes what to run, never requesting access itself.
"""

from __future__ import annotations

_GRANT_COMMAND = "apple-mail calendar-grant"
_PLUGIN_GRANT_COMMAND = "PYTHONPATH=<plugin dir> venv/bin/python3 -m apple_mail_mcp.cli calendar-grant"
_CALENDARS_PANE = "System Settings > Privacy & Security > Calendars"

_NEXT_STEPS: dict[str, str] = {
    "full_access": "The EventKit read fast path is active; no action needed.",
    "dependency_missing": (
        "The plugin install is missing the bundled EventKit dependency. "
        "Reinstall the current release; if it persists, run "
        f"'{_GRANT_COMMAND}' once from a terminal to confirm."
    ),
    "not_determined": (
        "Calendars access has never been requested for this host. Run "
        f"'{_GRANT_COMMAND}' once from a terminal and grant Full Access when "
        f"macOS prompts. (Plugin installs: {_PLUGIN_GRANT_COMMAND}.)"
    ),
    "write_only": (
        "This host has write-only Calendars access, which cannot back the read "
        f"fast path. Re-run '{_GRANT_COMMAND}' from a terminal and choose Full "
        "Access in the macOS prompt; if no prompt appears, open "
        f"{_CALENDARS_PANE} and set the host app to Full Access."
    ),
    "denied": (
        f"Calendars access is denied. Enable Full Access under {_CALENDARS_PANE} "
        "for the app that launches the server, or run: tccutil reset Calendar "
        f"and then '{_GRANT_COMMAND}' from a terminal."
    ),
    "restricted": (
        f"Calendars access is restricted by policy. {_CALENDARS_PANE} cannot "
        "override this; contact the device administrator."
    ),
}


def eventkit_next_step(reason: str) -> str:
    """Return the operator's next step for an ``eventkit_status()`` reason label.

    Handles the ``dependency_missing: ...`` and ``status_check_failed: ...``
    prefixed forms plus bare labels and unknown future ``status_{N}`` values.
    """
    if reason in _NEXT_STEPS:
        return _NEXT_STEPS[reason]
    if reason.startswith("dependency_missing"):
        return _NEXT_STEPS["dependency_missing"]
    if reason.startswith("status_check_failed"):
        return (
            "The EventKit authorization check itself failed. Confirm the plugin "
            f"venv is healthy, then re-run '{_GRANT_COMMAND}' from a terminal; "
            f"report the failure text if it persists. ({reason})"
        )
    return (
        "Unrecognized Calendars authorization state. Open "
        f"{_CALENDARS_PANE} for the host app and re-run '{_GRANT_COMMAND}' "
        f"from a terminal. ({reason})"
    )


__all__ = ["eventkit_next_step"]
