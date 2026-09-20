"""calendar-doctor CLI: both fast-path gates reported, never prompting."""

import json

from apple_mail_mcp import cli


def _run(monkeypatch, capsys, argv, status, engine_env="auto"):
    # Handlers import lazily from source at call time, so patch the source
    # module attributes (the tests' source-patch seams).
    monkeypatch.setattr(
        "apple_mail_mcp.calendar_core.eventkit.eventkit_status",
        lambda: status,
    )
    monkeypatch.setattr(
        "apple_mail_mcp.calendar_core.eventkit.load_frameworks", lambda: ("EK", "NS")
    )
    if engine_env is None:
        monkeypatch.delenv("APPLE_MAIL_CALENDAR_ENGINE", raising=False)
    else:
        monkeypatch.setenv("APPLE_MAIL_CALENDAR_ENGINE", engine_env)
    # commands.py reads os.environ directly; also keep the real env aligned.
    code = cli.main(argv)
    return code, capsys.readouterr()


class TestCalendarDoctor:
    def test_inactive_reports_next_step_and_zero_exit(self, monkeypatch, capsys):
        code, out = _run(monkeypatch, capsys, ["calendar-doctor"], (False, "write_only"))
        assert code == 0
        assert "write_only" in out.out
        assert "Full Access" in out.out
        assert "400" in out.out  # AppleScript horizon note always present

    def test_active_fast_path(self, monkeypatch, capsys):
        code, out = _run(monkeypatch, capsys, ["calendar-doctor"], (True, "full_access"))
        assert code == 0
        assert "active" in out.out
        assert "Active read engine: eventkit" in out.out

    def test_json_payload_carries_both_gates(self, monkeypatch, capsys):
        code, out = _run(
            monkeypatch, capsys, ["calendar-doctor", "--json"], (False, "not_determined")
        )
        assert code == 0
        payload = json.loads(out.out)
        assert payload["dependency_present"] is True
        assert payload["eventkit_available"] is False
        assert payload["reason"] == "not_determined"
        assert "calendar-grant" in payload["next_step"]
        assert payload["active_engine"] == "applescript"
        assert payload["applescript_recurring_lookback_days"] == 400

    def test_forced_applescript_stays_applescript(self, monkeypatch, capsys):
        code, out = _run(
            monkeypatch,
            capsys,
            ["calendar-doctor", "--json"],
            (True, "full_access"),
            engine_env="applescript",
        )
        assert code == 0
        assert json.loads(out.out)["active_engine"] == "applescript"

    def test_never_requests_access(self, monkeypatch, capsys):
        """Doctor must not construct an EKEventStore (the prompt-capable path)."""
        import apple_mail_mcp.calendar_core.eventkit as eventkit_mod

        monkeypatch.setattr(eventkit_mod, "eventkit_status", lambda: (False, "not_determined"))
        # If the doctor built a store it would need the framework modules; the
        # only framework seam it may touch is this presence probe.
        probes: list[str] = []
        monkeypatch.setattr(eventkit_mod, "load_frameworks", lambda: probes.append("probe") or None)
        assert cli.main(["calendar-doctor", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dependency_present"] is False
        assert probes == ["probe"]
