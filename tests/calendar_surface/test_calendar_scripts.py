"""Script builders: escaping, sanitizers, bounded predicates, and the calendar lint.

The lint tests here are the calendar analogue of ``tests/core/test_no_unbounded_whose.py``
(final plan F1): ``every event of`` may only appear inside the two sanctioned
builder modules, and every event ``whose`` predicate must be date-bounded on
both ends.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apple_mail_mcp.calendar_core.scripts_read import (
    applescript_date_block,
    build_uid_condition,
    count_events_script,
    fetch_recurring_masters_script,
    fetch_window_events_script,
    list_calendars_script,
)
from apple_mail_mcp.calendar_core.scripts_write import (
    build_event_set_lines,
    create_calendar_script,
    create_event_script,
    delete_calendar_script,
    delete_events_script,
    rename_calendar_script,
    update_event_script,
)

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_DIRS = (ROOT / "plugin" / "apple_mail_mcp",)
SANCTIONED_EVERY_EVENT = {
    "calendar_core/scripts_read.py",
    "calendar_core/scripts_write.py",
}

UTC = timezone.utc
START = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
END = datetime(2026, 7, 17, 0, 0, tzinfo=UTC)


def _blocks():
    return (
        applescript_date_block("windowStart", START),
        applescript_date_block("windowEnd", END),
    )


class TestCalendarLint:
    def test_every_event_only_in_sanctioned_modules(self):
        offenders: list[str] = []
        for base in PRODUCTION_DIRS:
            for path in base.rglob("*.py"):
                rel = path.relative_to(base).as_posix()
                if rel in SANCTIONED_EVERY_EVENT:
                    continue
                for lineno, line in enumerate(path.read_text().splitlines(), 1):
                    if "every event of" in line:
                        offenders.append(f"{rel}:{lineno}: {line.strip()}")
        assert offenders == [], (
            "`every event of` may only be emitted by calendar_core/scripts_read.py and "
            "scripts_write.py (bounded builders). Offenders:\n  - " + "\n  - ".join(offenders)
        )

    def test_every_event_whose_predicates_are_date_bounded(self):
        pattern = re.compile(r"every event of \S+ whose (.+)$")
        offenders: list[str] = []
        for base in PRODUCTION_DIRS:
            for rel in SANCTIONED_EVERY_EVENT:
                path = base / rel
                for lineno, line in enumerate(path.read_text().splitlines(), 1):
                    match = pattern.search(line)
                    if not match:
                        continue
                    predicate = match.group(1)
                    if "start date >= windowStart" not in predicate or "start date <= windowEnd" not in predicate:
                        offenders.append(f"{rel}:{lineno}: {line.strip()}")
        assert offenders == [], (
            "Every calendar `whose` predicate must carry both start-date bounds. Offenders:\n  - "
            + "\n  - ".join(offenders)
        )

    def test_no_recurrence_predicate_in_whose(self):
        # F1: the recurring filter must run in the repeat loop, never in `whose`.
        for base in PRODUCTION_DIRS:
            for rel in SANCTIONED_EVERY_EVENT:
                for line in (base / rel).read_text().splitlines():
                    if "whose" in line:
                        assert "recurrence" not in line, line


class TestDateBlocks:
    def test_integer_components_only(self):
        block = applescript_date_block("windowStart", datetime(2026, 7, 10, 14, 30, 5, tzinfo=UTC))
        assert 'date "' not in block
        assert re.search(r"set year of windowStart to \d+", block)
        assert re.search(r"set month of windowStart to \d+", block)
        assert re.search(r"set time of windowStart to \d+", block)

    def test_day_reset_before_month_assignment(self):
        block = applescript_date_block("d", START)
        assert block.index("set day of d to 1") < block.index("set year of d to")


class TestUidCondition:
    def test_multiple_ids_ored(self):
        condition = build_uid_condition(["A-1", "B-2"])
        assert condition == '(uid is "A-1" or uid is "B-2")'

    def test_ids_are_escaped(self):
        # Shape validation upstream forbids quotes; escaping is defense in depth.
        condition = build_uid_condition(["plain-id"])
        assert '"plain-id"' in condition


class TestReadScripts:
    def test_fetch_window_script_shape(self):
        start_block, end_block = _blocks()
        script = fetch_window_events_script(
            calendar_name='Wor"k',
            start_block=start_block,
            end_block=end_block,
            scan_cap=300,
            timeout_seconds=45,
        )
        assert script.lstrip().startswith('tell application "Calendar"')
        assert "with timeout of 45 seconds" in script
        assert 'calendar "Wor\\"k"' in script  # escaped quote
        assert "items 1 thru 300" in script
        assert "text item delimiters" in script  # sanitizer present
        assert script.count("start date >= windowStart") == 1

    def test_fetch_window_uid_filter(self):
        start_block, end_block = _blocks()
        script = fetch_window_events_script(
            calendar_name="Work",
            start_block=start_block,
            end_block=end_block,
            scan_cap=300,
            timeout_seconds=30,
            uid_condition=build_uid_condition(["UID-9"]),
        )
        assert 'and (uid is "UID-9")' in script

    def test_detail_rows_only_when_requested(self):
        start_block, end_block = _blocks()
        plain = fetch_window_events_script(
            calendar_name="Work", start_block=start_block, end_block=end_block, scan_cap=10, timeout_seconds=30
        )
        detail = fetch_window_events_script(
            calendar_name="Work",
            start_block=start_block,
            end_block=end_block,
            scan_cap=10,
            timeout_seconds=30,
            include_detail=True,
        )
        assert "ATT|||" not in plain
        assert "ATT|||" in detail
        assert "ALM|||" in detail

    def test_recurring_masters_filter_in_loop(self):
        start_block, end_block = _blocks()
        script = fetch_recurring_masters_script(
            calendar_name="Work",
            start_block=start_block,
            end_block=end_block,
            scan_cap=200,
            timeout_seconds=30,
        )
        assert "if evRule is not missing value" in script
        assert "whose start date >= windowStart and start date <= windowEnd" in script

    def test_list_calendars_sanitizes_names(self):
        script = list_calendars_script(timeout_seconds=30)
        assert "CAL|||" in script
        assert "text item delimiters" in script


class TestWriteScripts:
    def test_create_event_escapes_title(self):
        start_block, end_block = _blocks()
        script = create_event_script(
            calendar_name="Work",
            title='Lunch "meeting"\nwith Bob',
            start_block=start_block,
            end_block=end_block,
            timeout_seconds=30,
        )
        assert '\\"meeting\\"' in script
        assert "\\n" in script
        assert "writable of targetCal is false" in script

    def test_set_lines_alarm_and_attendee(self):
        lines = build_event_set_lines(
            alarms_minutes_before=[15],
            add_attendees=["a@example.com"],
            location='Cafe "X"',
        )
        assert "trigger interval:-15" in lines
        assert 'email:"a@example.com"' in lines
        assert '\\"X\\"' in lines
        assert "delete every display alarm" in lines  # alarms replace

    def test_set_lines_clear_recurrence_fallback(self):
        lines = build_event_set_lines(clear_recurrence=True)
        assert 'set recurrence of targetEvent to ""' in lines
        assert "missing value" in lines

    def test_update_script_requires_uid_condition(self):
        start_block, end_block = _blocks()
        script = update_event_script(
            calendar_name="Work",
            uid_condition=build_uid_condition(["UID-1"]),
            start_block=start_block,
            end_block=end_block,
            set_lines='set summary of targetEvent to "New"',
            timeout_seconds=30,
        )
        assert 'uid is "UID-1"' in script
        assert "ERROR_NOT_FOUND" in script

    def test_delete_script_captures_before_delete(self):
        start_block, end_block = _blocks()
        script = delete_events_script(
            calendar_name="Work",
            uid_condition=build_uid_condition(["UID-1"]),
            start_block=start_block,
            end_block=end_block,
            timeout_seconds=30,
        )
        assert script.index("set evTitle to summary of anEvent") < script.index("delete anEvent")
        assert "DELETED|||" in script

    def test_calendar_crud_scripts_escape(self):
        assert '\\"X\\"' in create_calendar_script(calendar_name='Cal "X"', timeout_seconds=30)
        assert "RENAMED_CAL" in rename_calendar_script(calendar_name="Old", new_name="New", timeout_seconds=30)
        assert "DELETED_CAL" in delete_calendar_script(calendar_name="Gone", timeout_seconds=30)
        assert "COUNT|||" in count_events_script(calendar_name="Work", timeout_seconds=30)


_OSACOMPILE = shutil.which("osacompile") is not None


@pytest.mark.skipif(not _OSACOMPILE, reason="osacompile not available (non-macOS CI)")
class TestScriptsCompile:
    def _compile(self, script: str) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as handle:
            handle.write(script)
            source = handle.name
        out = source.replace(".applescript", ".scpt")
        try:
            result = subprocess.run(["osacompile", "-o", out, source], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
        finally:
            for path in (source, out):
                Path(path).unlink(missing_ok=True)

    def test_all_full_scripts_compile(self):
        start_block, end_block = _blocks()
        uid = build_uid_condition(["UID-1"])
        set_lines = build_event_set_lines(
            title="T",
            new_start=START,
            all_day=True,
            location="L",
            notes="N",
            url="https://example.com",
            recurrence="FREQ=DAILY",
            alarms_minutes_before=[10],
            add_attendees=["a@example.com"],
        )
        scripts = [
            list_calendars_script(timeout_seconds=30),
            fetch_window_events_script(
                calendar_name="Work",
                start_block=start_block,
                end_block=end_block,
                scan_cap=10,
                timeout_seconds=30,
                uid_condition=uid,
                include_detail=True,
            ),
            fetch_recurring_masters_script(
                calendar_name="Work",
                start_block=start_block,
                end_block=end_block,
                scan_cap=10,
                timeout_seconds=30,
            ),
            count_events_script(calendar_name="Work", timeout_seconds=30),
            create_event_script(
                calendar_name="Work",
                title="T",
                start_block=start_block,
                end_block=end_block,
                set_lines=set_lines,
                timeout_seconds=30,
            ),
            update_event_script(
                calendar_name="Work",
                uid_condition=uid,
                start_block=start_block,
                end_block=end_block,
                set_lines=set_lines,
                timeout_seconds=30,
            ),
            delete_events_script(
                calendar_name="Work",
                uid_condition=uid,
                start_block=start_block,
                end_block=end_block,
                timeout_seconds=30,
            ),
            create_calendar_script(calendar_name="New Cal", timeout_seconds=30),
            rename_calendar_script(calendar_name="Old", new_name="New", timeout_seconds=30),
            delete_calendar_script(calendar_name="Gone", timeout_seconds=30),
        ]
        for script in scripts:
            self._compile(script)
