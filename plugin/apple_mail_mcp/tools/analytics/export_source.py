"""Standalone validation of staged Mail EML before atomic publication."""

import sys
from collections.abc import Callable
from importlib import import_module
from mmap import ACCESS_READ, mmap
from pathlib import Path
from typing import cast


def check_source(staging: Path) -> str:
    """Return the writer disposition while keeping ordinary RFC source untouched."""
    # The writer runs this script with -S. Direct execution imports these two
    # stdlib-only siblings without initializing the MCP package or its tools.
    prefix = f"{__package__}." if __package__ else ""
    split_calendar = cast(
        Callable[[bytes], tuple[bytes, bytes | None] | None],
        import_module(prefix + "export_calendar").split_calendar_source,
    )
    check_mime = cast(
        Callable[[bytes | mmap], bool],
        import_module(prefix + "export_mime").needs_source_retry,
    )
    with staging.open("rb") as source, mmap(source.fileno(), 0, access=ACCESS_READ) as data:
        marker = b"BEGIN:VCALENDAR"
        calendar = split_calendar(data[:]) if data[: len(marker)] == marker else None
        if calendar is None:
            return "retry" if check_mime(data) else "eml"
    # The subprocess only prepares files. If osascript is killed while this
    # child is running, it cannot publish anything after the timeout receipt.
    payload, metadata = calendar
    staging.write_bytes(payload)
    if metadata is None:
        return "calendar"
    staging.with_suffix(staging.suffix + ".plist").write_bytes(metadata)
    return "calendar-with-metadata"


if __name__ == "__main__":
    try:
        print(check_source(Path(sys.argv[1])))
    except ValueError:
        raise SystemExit("Mail returned malformed native calendar source or metadata.") from None
    except OSError:
        raise SystemExit("Unable to validate or publish staged Mail source.") from None
