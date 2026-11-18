"""Detect the inconsistent root MIME framing from Mail's source property."""

import mmap

import pytest
from apple_mail_mcp.tools.analytics.export_mime import needs_source_retry


def _multipart(
    *,
    header_boundary=b"archive:part",
    body_boundary=b"archive:part",
    closing=True,
    newline=b"\n",
    transfer_encoding=None,
):
    headers = [b"From: sender@example.com", b"MIME-Version: 1.0"]
    if header_boundary is None:
        headers.append(b"Content-Type: multipart/mixed")
    else:
        headers.extend([b"Content-Type: multipart/mixed;", b'\tboundary="' + header_boundary + b'"'])
    if transfer_encoding is not None:
        headers.append(b"Content-Transfer-Encoding: " + transfer_encoding)
    body = [
        b"A multipart preamble",
        b"--" + body_boundary + b" \t",
        b"Content-Type: text/plain",
        b"",
        b"Original content",
    ]
    if closing:
        body.append(b"--" + body_boundary + b"--\t")
    return newline.join([*headers, b"", *body])


def test_wrong_outer_boundary_needs_another_source_read(tmp_path):
    path = tmp_path / "message.partial"
    first = _multipart(header_boundary=b"stale-boundary")
    path.write_bytes(first)
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as staged:
        assert needs_source_retry(staged)
    assert path.read_bytes() == first
    assert not needs_source_retry(_multipart())


@pytest.mark.parametrize(
    "case",
    [
        {"header_boundary": None},
        {"body_boundary": b"archive:part-extra"},
        {"closing": False},
    ],
    ids=["missing-parameter", "prefix-is-not-a-delimiter", "missing-closing-delimiter"],
)
def test_incomplete_root_mime_needs_another_source_read(case):
    assert needs_source_retry(_multipart(**case))


@pytest.mark.parametrize("newline", [b"\r\n", b"\n", b"\r"], ids=["crlf", "lf", "cr"])
def test_folded_quoted_boundary_and_transport_padding_are_supported(newline):
    assert not needs_source_retry(_multipart(newline=newline))


def test_boundary_in_header_and_epilogue_does_not_count_as_an_opening_delimiter():
    data = _multipart(body_boundary=b"different") + b"\nText example: --archive:part\n--archive:part--\n"
    assert needs_source_retry(data)


def test_closing_delimiter_before_first_part_still_requires_retry():
    data = _multipart().replace(b"A multipart preamble", b"--archive:part--", 1)
    assert needs_source_retry(data)


def test_intact_boundary_with_legacy_transfer_encoding_is_accepted():
    assert not needs_source_retry(_multipart(transfer_encoding=b"quoted-printable"))


def test_nested_multipart_keeps_its_own_delimiters():
    data = (
        b'Content-Type: multipart/mixed; boundary="outer"\r\n\r\n'
        b"--outer\r\n"
        b'Content-Type: multipart/alternative; boundary="inner"\r\n\r\n'
        b"--inner\r\nContent-Type: text/plain\r\n\r\nOriginal content\r\n"
        b"--inner--\r\n--outer--\r\n"
    )
    assert not needs_source_retry(data)


def test_plain_message_and_native_calendar_leave_mime_check_unchanged():
    assert not needs_source_retry(b"From: sender@example.com\n\nHello")
    assert not needs_source_retry(b"BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")
