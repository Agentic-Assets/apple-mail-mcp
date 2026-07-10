"""applescript_date_block: all-day vs timed date-component emission (F1/F6).

These tests drive ``applescript_date_block`` directly (the seam the write and
read scripts share) and assert the emitted integer date components. They are
host-timezone-independent by construction:

- All-day dates must land on the requested calendar date regardless of the
  host zone, so the expected components are the requested-zone wall clock (the
  F1 regression: a Tokyo all-day request emitted the previous day on a CDT
  host).
- Timed instants preserve the absolute moment, so the expected components are
  recomputed with the same ``astimezone()`` the production code uses; this
  matches on any host running the test.

Asia/Tokyo (+9) is several hours EAST of both the dev host (CDT, -5) and CI
(UTC); Pacific/Honolulu (-10) and America/Los_Angeles (-7/-8) are several hours
WEST of both. So every case below exercises a zone on both sides of the host.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from apple_mail_mcp.calendar_core.scripts_read import applescript_date_block

TOKYO = ZoneInfo("Asia/Tokyo")  # +9, east of host
HONOLULU = ZoneInfo("Pacific/Honolulu")  # -10, west of host
LOS_ANGELES = ZoneInfo("America/Los_Angeles")  # -7/-8, west of host


def _emitted(block: str, var: str = "d") -> tuple[int, int, int, int]:
    """Extract (year, month, day, seconds) from a date block's final assignments."""
    year = int(re.search(rf"set year of {var} to (\d+)", block).group(1))
    month = int(re.search(rf"set month of {var} to (\d+)", block).group(1))
    # `day` is set twice (reset to 1, then the real day); take the last.
    day = int(re.findall(rf"set day of {var} to (\d+)", block)[-1])
    seconds = int(re.findall(rf"set time of {var} to (\d+)", block)[-1])
    return year, month, day, seconds


def _timed_expectation(dt: datetime) -> tuple[int, int, int, int]:
    """Production converts timed instants to host-local wall clock; mirror it."""
    local = dt.astimezone()
    return local.year, local.month, local.day, local.hour * 3600 + local.minute * 60 + local.second


class TestAllDayDateBlock:
    def test_all_day_east_lands_on_requested_date(self) -> None:
        # Midnight in Tokyo on 2026-07-10: must emit day 10 regardless of host.
        block = applescript_date_block("d", datetime(2026, 7, 10, 0, 0, 0, tzinfo=TOKYO), all_day=True)
        assert _emitted(block) == (2026, 7, 10, 0)

    def test_all_day_west_lands_on_requested_date(self) -> None:
        # Midnight in Honolulu on 2026-07-10: must emit day 10 regardless of host.
        block = applescript_date_block("d", datetime(2026, 7, 10, 0, 0, 0, tzinfo=HONOLULU), all_day=True)
        assert _emitted(block) == (2026, 7, 10, 0)

    def test_all_day_ignores_time_component(self) -> None:
        # Even a non-midnight all-day datetime keeps the requested calendar day.
        block = applescript_date_block("d", datetime(2026, 7, 10, 15, 30, 0, tzinfo=TOKYO), all_day=True)
        year, month, day, _seconds = _emitted(block)
        assert (year, month, day) == (2026, 7, 10)


class TestTimedDateBlock:
    def test_timed_east_preserves_instant(self) -> None:
        dt = datetime(2026, 7, 10, 9, 0, 0, tzinfo=TOKYO)
        block = applescript_date_block("d", dt)
        assert _emitted(block) == _timed_expectation(dt)

    def test_timed_west_preserves_instant(self) -> None:
        dt = datetime(2026, 7, 10, 9, 0, 0, tzinfo=LOS_ANGELES)
        block = applescript_date_block("d", dt)
        assert _emitted(block) == _timed_expectation(dt)

    def test_timed_default_is_not_all_day(self) -> None:
        # Default (all_day omitted) must match the astimezone timed path.
        dt = datetime(2026, 7, 10, 9, 0, 0, tzinfo=HONOLULU)
        assert applescript_date_block("d", dt) == applescript_date_block("d", dt, all_day=False)
