"""guidance.py: every eventkit_status() reason maps to an operator next step."""

from apple_mail_mcp.calendar_core.guidance import eventkit_next_step


class TestEventkitNextStep:
    def test_full_access_needs_no_action(self):
        assert "no action needed" in eventkit_next_step("full_access")

    def test_dependency_missing_names_reinstall(self):
        step = eventkit_next_step("dependency_missing: pip install 'mcp-apple-mail[eventkit]'")
        assert "Reinstall" in step
        assert "calendar-grant" in step

    def test_bare_dependency_missing_names_reinstall(self):
        step = eventkit_next_step("dependency_missing")
        assert "Reinstall" in step
        assert "calendar-grant" in step

    def test_not_determined_points_at_grant(self):
        step = eventkit_next_step("not_determined")
        assert "apple-mail calendar-grant" in step
        assert "Terminal" in step or "terminal" in step

    def test_write_only_demands_full_access_upgrade(self):
        step = eventkit_next_step("write_only")
        assert "write-only" in step
        assert "Full Access" in step
        assert "calendar-grant" in step

    def test_denied_names_pane_and_reset(self):
        step = eventkit_next_step("denied")
        assert "Privacy & Security > Calendars" in step
        assert "tccutil reset Calendar" in step

    def test_restricted_is_policy_not_settings(self):
        step = eventkit_next_step("restricted")
        assert "administrator" in step

    def test_status_check_failure_echoes_reason(self):
        step = eventkit_next_step("status_check_failed: no tccd")
        assert "status_check_failed: no tccd" in step
        assert "calendar-grant" in step

    def test_bare_status_check_failure_echoes_reason(self):
        step = eventkit_next_step("status_check_failed")
        assert "status_check_failed" in step
        assert "calendar-grant" in step

    def test_unknown_future_status_falls_back_with_reason(self):
        step = eventkit_next_step("status_9")
        assert "status_9" in step
        assert "calendar-grant" in step

    def test_every_documented_label_has_a_step(self):
        for reason in (
            "full_access",
            "dependency_missing: pip install 'mcp-apple-mail[eventkit]'",
            "not_determined",
            "restricted",
            "denied",
            "write_only",
            "status_check_failed: boom",
            "status_7",
        ):
            assert eventkit_next_step(reason), reason
