"""Check the MIME framing of staged Mail source before publication."""

import re
from email import policy
from email.parser import BytesHeaderParser
from mmap import mmap

_HEADER_SEPARATOR = re.compile(rb"(?:\r\n|\n|\r(?!\n)){2}")
_LINE_END = rb"[ \t]*(?:[\r\n]|\Z)"


def needs_source_retry(data: bytes | mmap) -> bool:
    """Detect an inconsistent outer multipart header without re-encoding source."""
    separator = _HEADER_SEPARATOR.search(data)
    if separator is None:
        return False
    headers = BytesHeaderParser(policy=policy.default).parsebytes(data[: separator.start()])
    if headers.get_content_maintype() != "multipart":
        return False

    boundary = headers.get_boundary()
    if not boundary or not boundary.isascii() or "\r" in boundary or "\n" in boundary:
        return True

    # Lead with the literal marker for fast search, then restrict matches to
    # line starts. The mapped file's MIME payloads are never decoded or copied.
    marker = re.escape(b"--" + boundary.encode("ascii"))
    delimiter = re.compile(marker + rb"(--)?" + _LINE_END)
    opened = False
    for match in delimiter.finditer(data, separator.end()):
        if data[match.start() - 1] not in b"\r\n":
            continue
        if match.group(1):
            return not opened
        opened = True
    return True
