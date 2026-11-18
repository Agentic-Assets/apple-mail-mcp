"""Native calendar framing in Mail export data."""

import plistlib
import re
from io import BytesIO
from xml.parsers.expat import ExpatError

_CALENDAR = b"VCALENDAR"
_CALENDAR_START = b"BEGIN:" + _CALENDAR
_COMPONENT_NAME = re.compile(rb"[A-Z0-9-]+")
_XML_PLIST_START = re.compile(rb"(?:<\?xml[^?]*\?>\s*)?(?:<!DOCTYPE plist[^>]*>\s*)?<plist(?:\s|>)")


def _calendar_end(source: bytes) -> int:
    """Find the byte offset after the matching root component and line ending."""
    stream = BytesIO(source)
    stream.readline()
    components = [_CALENDAR]
    root_properties: set[bytes] = set()
    has_component = False

    while raw_line := stream.readline():
        line = raw_line.removesuffix(b"\n").removesuffix(b"\r")
        if line.startswith(b"BEGIN:"):
            name = line[6:]
            if name == _CALENDAR or not _COMPONENT_NAME.fullmatch(name):
                raise ValueError("Malformed native calendar component")
            components.append(name)
            has_component = True
        elif line.startswith(b"END:"):
            if components.pop() != line[4:]:
                raise ValueError("Mismatched native calendar component")
            if not components:
                if not has_component or root_properties != {b"PRODID", b"VERSION"}:
                    raise ValueError("Incomplete native calendar frame")
                return stream.tell()
        elif len(components) == 1:
            if line.startswith(b"PRODID:") and line[7:].strip():
                root_properties.add(b"PRODID")
            elif line == b"VERSION:2.0":
                root_properties.add(b"VERSION")

    raise ValueError("Native calendar frame has no closing END:VCALENDAR")


def _validate_metadata(metadata: bytes) -> None:
    decoded = metadata.lstrip()
    error_message = "Unrecognized Mail calendar metadata trailer"
    if not _XML_PLIST_START.match(decoded):
        raise ValueError(error_message)
    try:
        plist = plistlib.loads(decoded, fmt=plistlib.FMT_XML)
    except (AttributeError, ExpatError, IndexError, OverflowError, ValueError):
        raise ValueError(error_message) from None
    if not isinstance(plist, dict):
        raise ValueError(error_message)


def split_calendar_source(source: bytes) -> tuple[bytes, bytes | None] | None:
    """Split a native iCalendar record from its optional Mail XML property list.

    Ordinary RFC 822 source, including a calendar MIME attachment, returns
    None. Unrecognized data after a native calendar is an error so no source
    bytes get discarded. Successful output preserves both file segments verbatim.
    """
    if source != _CALENDAR_START and not source.startswith((_CALENDAR_START + b"\n", _CALENDAR_START + b"\r\n")):
        return None
    end = _calendar_end(source)
    metadata = source[end:]
    if not metadata.strip():
        return source, None
    _validate_metadata(metadata)
    return source[:end], metadata
