"""Native calendar framing from Mail's source property."""

import plistlib

import pytest
from apple_mail_mcp.tools.analytics.export_calendar import split_calendar_source

_CALENDAR = (
    b"BEGIN:VCALENDAR\n"
    b"PRODID:-//Example//Calendar Fixture//EN\n"
    b"VERSION:2.0\n"
    b"BEGIN:VEVENT\n"
    b"UID:fixture@example.com\n"
    b"DTSTAMP:20260101T090000Z\n"
    b"SUMMARY:Calendar fixture\n"
    b"DESCRIPTION:Text with a folded line\n"
    b" END:VCALENDAR is part of the description\n"
    b"BEGIN:VALARM\n"
    b"ACTION:DISPLAY\n"
    b"TRIGGER:-PT5M\n"
    b"DESCRIPTION:Reminder\n"
    b"END:VALARM\n"
    b"END:VEVENT\n"
    b"END:VCALENDAR\n"
)
_MAIL_METADATA = b" \n" + plistlib.dumps({"flags": 1, "date-received": 0})


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_xml_metadata_splits_without_altering_calendar_or_trailer(line_ending):
    calendar = _CALENDAR.replace(b"\n", line_ending)
    source = calendar + _MAIL_METADATA

    assert split_calendar_source(source) == (calendar, _MAIL_METADATA)


@pytest.mark.parametrize("ending", [b"", b"\n", b"\n\n"], ids=["no-newline", "newline", "extra-newline"])
def test_standalone_calendar_keeps_original_bytes(ending):
    calendar = _CALENDAR.rstrip(b"\n") + ending

    assert split_calendar_source(calendar) == (calendar, None)


def test_mime_calendar_attachment_is_not_a_native_calendar():
    message = b"From: sender@example.com\nContent-Type: text/calendar\n\n" + _CALENDAR

    assert split_calendar_source(message) is None


@pytest.mark.parametrize(
    "calendar",
    [
        _CALENDAR.removesuffix(b"END:VCALENDAR\n"),
        _CALENDAR.replace(b"END:VALARM", b"END:VTODO"),
        b"BEGIN:VCALENDAR\nEND:VCALENDAR\n",
        b"BEGIN:VCALENDAR",
    ],
    ids=["truncated", "unmatched-component", "empty-frame", "only-first-line"],
)
def test_incomplete_native_calendar_fails(calendar):
    with pytest.raises(ValueError, match="calendar"):
        split_calendar_source(calendar)


@pytest.mark.parametrize(
    "trailer",
    [
        b"unrelated bytes",
        _CALENDAR,
        plistlib.dumps({"key": 1}) + b"unexpected bytes",
        plistlib.dumps([1]),
        plistlib.dumps({"key": 1}, fmt=plistlib.FMT_BINARY),
    ],
    ids=["unframed", "extra-calendar", "xml-with-extra-data", "non-dictionary-plist", "binary-plist"],
)
def test_unknown_native_trailer_fails_instead_of_discarding_data(trailer):
    with pytest.raises(ValueError, match="metadata"):
        split_calendar_source(_CALENDAR + trailer)
