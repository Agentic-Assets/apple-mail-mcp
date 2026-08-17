#!/usr/bin/env python3
"""
Export an Apple Mail account's .emlx store to standard .eml, reassembling
detached attachments, and build a searchable SQLite FTS5 index.

Point --snapshot at a FROZEN COPY of the account directory, never the live
~/Library/Mail store. Mail rewrites Info.plist and the Envelope Index while it
runs; exporting from a copy makes the run reproducible and leaves the only
surviving copy of the mail untouched.

  ./export_emlx.py --snapshot ARCHIVE/01-raw-snapshot/<AccountUUID> \
                   --out ARCHIVE/10-export [--only "Inbox"]

Format facts this implements (all verified empirically; the reasoning and the
evidence behind each are in references/emlx-format.md):

  * .emlx layout: an 11-byte left-aligned space-padded length header, then N
    bytes of RFC822 (LF-normalized, not CRLF), then an XML plist trailer.
  * Hash buckets: Data/<d>/<d>/.../Messages/<id>.emlx, where the directory
    components are the decimal digits of (id // 1000), least significant first.
    NOT "all digits reversed" - the low 3 digits are the intra-bucket index.
    This script walks the tree rather than computing paths, so the rule only
    matters when you need to locate one specific message by id.
  * .partial.emlx keeps the full MIME skeleton; attachment part bodies are
    emptied and carry X-Apple-Content-Length. Payloads live either in
    <bucket>/Attachments/<id>/<dotted-part>/<filename>  (DECODED -> re-encode)
    or  <bucket>/Messages/<id>.<part>.emlxpart          (ENCODED -> splice raw).
    Reversing those two double-encodes the attachment.
  * Part numbers are RFC 3501 dotted numbering. Multipart containers consume an
    index at their level but are not leaves.
  * Mailbox hierarchy: children nest inside the parent .mbox. Collecting a
    node's messages must stop descending at the first child .mbox, or the parent
    absorbs every descendant's mail.
"""

from __future__ import annotations

import argparse
import base64
import email
import html.parser
import io
import json
import os
import plistlib
import quopri
import re
import sqlite3
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from email.generator import BytesGenerator
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

# Messages above this are streamed verbatim without MIME parsing. Real stores
# contain the occasional ~1 GB draft; parsing one costs several GB of RAM for no
# benefit, since an oversized message is virtually never attachment-detached.
OVERSIZE_BYTES = 300 * 1024 * 1024

# jwz's classic table, re-validated against a live Envelope Index. Bit 7 goes
# stale in the on-disk trailer and is deliberately absent.
FLAG_BITS = {
    "read": 1 << 0,
    "deleted": 1 << 1,
    "answered": 1 << 2,
    "encrypted": 1 << 3,
    "flagged": 1 << 4,
    "draft": 1 << 6,
    "forwarded": 1 << 8,
    "redirected": 1 << 9,
    "junk": 1 << 24,
    "not_junk": 1 << 25,
}


class EmlxError(Exception):
    pass


# ---------------------------------------------------------------------------
# emlx parsing
# ---------------------------------------------------------------------------


def read_emlx_header(fh) -> int:
    """Read the leading length line, tolerating the fixed-width space padding."""
    raw = fh.readline()
    if not raw:
        raise EmlxError("empty file")
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise EmlxError(f"bad length header {raw[:32]!r}") from exc


def split_emlx(path: Path) -> tuple[bytes, dict]:
    """Return (rfc822_bytes, plist_dict)."""
    with path.open("rb") as fh:
        n = read_emlx_header(fh)
        rfc = fh.read(n)
        trailer = fh.read()
    plist: dict = {}
    if trailer:
        stripped = trailer.lstrip()
        if stripped.startswith(b"<?xml") or stripped.startswith(b"bplist00"):
            try:
                plist = plistlib.loads(stripped)
            except Exception:
                plist = {}
    return rfc, plist


def stream_rfc822(path: Path, dest: Path) -> None:
    """Copy just the RFC822 span to dest without loading the whole file."""
    with path.open("rb") as fh:
        remaining = read_emlx_header(fh)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            while remaining > 0:
                chunk = fh.read(min(1 << 20, remaining))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)


# ---------------------------------------------------------------------------
# MIME part numbering + attachment reassembly
# ---------------------------------------------------------------------------


def walk_numbered(part: Message, num: str = ""):
    """Yield (dotted_number, part) for every entity; the root is numbered ''."""
    if part.get_content_maintype() == "multipart":
        yield num, part
        payload = part.get_payload()
        if isinstance(payload, list):
            for i, sub in enumerate(payload, 1):
                child = f"{num}.{i}" if num else str(i)
                yield from walk_numbered(sub, child)
    else:
        yield (num or "1"), part


def _read_payload_file(p: Path) -> bytes:
    """Read an attachment payload.

    Mail occasionally expands a bundle attachment (.pages, .key) into a real
    directory instead of a file. open() raises IsADirectoryError on those, which
    kills an otherwise-complete run near the end, so re-zip it instead. The
    re-zipped bytes will not match the original archive byte-for-byte; that is
    unavoidable and far better than dropping the attachment.
    """
    if p.is_dir():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(p):
                for name in files:
                    full = Path(root) / name
                    zf.write(full, full.relative_to(p))
        return buf.getvalue()
    return p.read_bytes()


def _stub_has_content(part: Message) -> bool:
    """True when a part carrying an X-Apple-Content-Length stub already holds
    real content, so nothing needs filling and the stub can simply be dropped."""
    current = part.get_payload(decode=False)
    if not current:
        return False
    # Mail's emptied-stub shape is a single child holding blank text. Anything
    # else is content. The inner payload has to be type-checked before stripping
    # it: when that single child is itself a multipart container its payload is
    # a list, and calling .strip() on a list raises AttributeError. That got
    # swallowed upstream as a parse error, so a structurally unusual message
    # looked like a corrupt one and the report blamed the wrong thing.
    if isinstance(current, list) and len(current) == 1:
        inner = current[0].get_payload(decode=False)
        return bool(inner.strip()) if isinstance(inner, str) else bool(inner)
    return True


def reassemble(
    msg: Message, msg_id: int, messages_dir: Path, stats: "Stats"
) -> tuple[int, int]:
    """Fill emptied attachment parts in place. Returns (filled, missing)."""
    bucket_dir = messages_dir.parent
    att_root = bucket_dir / "Attachments" / str(msg_id)
    filled = missing = 0

    for num, part in walk_numbered(msg):
        if part.get_content_maintype() == "multipart":
            continue

        xacl = part.get("X-Apple-Content-Length")
        sidecar = messages_dir / f"{msg_id}.{num}.emlxpart"
        part_dir = att_root / num
        payload_path = None
        if part_dir.is_dir():
            entries = sorted(part_dir.iterdir())
            if entries:
                payload_path = entries[0]
        has_payload = sidecar.exists() or payload_path is not None

        if xacl is None:
            # Mail *usually* marks an emptied part with X-Apple-Content-Length,
            # but not always. When it detaches the body of a single-part message
            # it can leave no marker at all: headers, a blank line, and nothing
            # else. Keying reassembly off the marker alone silently shipped 51
            # empty text/calendar messages on a 39,269-message Exchange account,
            # every one with its payload sitting on disk beside it.
            #
            # So an empty part with a payload on disk at its own part number is
            # treated as equally strong evidence of a detached body. The
            # emptiness test is what keeps this safe: a part that already holds
            # content is never touched, so this cannot overwrite real data even
            # when Mail keeps a stale payload directory around.
            if not (has_payload and not _stub_has_content(part)):
                continue
            stats.filled_unmarked += 1

        # (a) .emlxpart sidecar: ALREADY ENCODED, splice verbatim.
        if sidecar.exists():
            part.set_payload(sidecar.read_bytes().decode("ascii", "surrogateescape"))
            del part["X-Apple-Content-Length"]
            filled += 1
            stats.filled_emlxpart += 1
            continue

        # (b) Attachments/<id>/<part>/<file>: DECODED, must re-encode.
        if payload_path is None:
            # Nothing on disk. If the part already carries content, keep it.
            # Otherwise this attachment is genuinely unavailable locally: leave
            # the stub header so the gap stays auditable rather than shipping a
            # silently empty attachment that looks fine in a mail client.
            if _stub_has_content(part):
                del part["X-Apple-Content-Length"]
                continue
            missing += 1
            stats.record_missing(msg_id=msg_id, part=num, dir=str(part_dir))
            continue

        try:
            data = _read_payload_file(payload_path)
        except Exception as exc:
            missing += 1
            stats.record_missing(msg_id=msg_id, part=num, error=repr(exc))
            continue

        # message/rfc822 payloads are nested Messages, not encoded strings.
        # set_payload(str) on one of these silently produces an EMPTY part, so
        # the run reports complete success while destroying every attached
        # email. Only a byte-level comparison against the originals catches it -
        # which is exactly why verify_export.py exists and is not optional.
        if part.get_content_type() == "message/rfc822":
            try:
                nested = email.message_from_bytes(data)
                part.set_payload([nested])
                del part["X-Apple-Content-Length"]
                if part.get("Content-Transfer-Encoding"):
                    del part["Content-Transfer-Encoding"]
                filled += 1
                stats.filled_rfc822 += 1
            except Exception as exc:
                missing += 1
                stats.record_missing(msg_id=msg_id, part=num, error=f"rfc822: {exc!r}")
            continue

        cte = (part.get("Content-Transfer-Encoding") or "7bit").strip().lower()
        if cte == "base64":
            encoded = base64.encodebytes(data).decode("ascii")
        elif cte == "quoted-printable":
            # QP is not canonical, so re-encoding is not byte-reproducible. The
            # decoded content is still correct; only the wire form differs.
            encoded = quopri.encodestring(data).decode("ascii", "surrogateescape")
            stats.qp_parts += 1
        elif cte in ("uuencode", "x-uuencode", "uue"):
            # We hold decoded bytes and cannot reproduce the original uu frame,
            # so re-emit as base64 and correct the header. Content stays exact.
            encoded = base64.encodebytes(data).decode("ascii")
            part.replace_header("Content-Transfer-Encoding", "base64")
            stats.uu_parts += 1
        else:
            encoded = data.decode("ascii", "surrogateescape")

        part.set_payload(encoded)

        # X-Apple-Content-Length is a soft check only: provenance differs across
        # account types (some record the CRLF length). Warn, never fail.
        try:
            declared = int(xacl)
            actual = len(encoded.encode("ascii", "surrogateescape"))
            if abs(actual - declared) > 1:
                stats.xacl_mismatch += 1
        except (TypeError, ValueError):
            pass

        del part["X-Apple-Content-Length"]
        filled += 1
        stats.filled_attachments += 1

    return filled, missing


# ---------------------------------------------------------------------------
# Text extraction for the search index
# ---------------------------------------------------------------------------


class _Stripper(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(s: str) -> str:
    p = _Stripper()
    try:
        p.feed(s)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", s)
    return " ".join("".join(p.parts).split())


def extract_body_text(msg: Message, limit: int = 200_000) -> str:
    """Plain text if present, else HTML stripped to text.

    This matters more than it looks: most bodies are base64 or quoted-printable
    on disk, so grepping the raw files under-recalls badly. Decoded text in FTS5
    is what makes the archive genuinely searchable.
    """
    plain: list[str] = []
    rich: list[str] = []
    for _num, part in walk_numbered(msg):
        if part.get_content_maintype() == "multipart":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        try:
            raw = part.get_payload(decode=True)
        except Exception:
            continue
        if not raw:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = raw.decode("utf-8", errors="replace")
        (plain if ctype == "text/plain" else rich).append(text)
        if sum(map(len, plain)) > limit:
            break
    if plain:
        return " ".join(" ".join(plain).split())[:limit]
    if rich:
        return html_to_text(" ".join(rich))[:limit]
    return ""


def attachment_names(msg: Message) -> list[str]:
    names = []
    for _num, part in walk_numbered(msg):
        if part.get_content_maintype() == "multipart":
            continue
        fn = part.get_filename()
        if fn:
            names.append(" ".join(str(fn).split()))
    return names


def _scan_header_block(block: str, boundaries: set[bytes], names: list[str]) -> None:
    """Read one MIME part header block for a boundary and a filename."""
    part = email.message_from_string(block)
    boundary = part.get_param("boundary")
    if boundary:
        boundaries.add(str(boundary).encode("utf-8", "surrogateescape"))
    try:
        fn = part.get_filename()
    except Exception:
        # compat32 raises on a header holding a lone high surrogate. An
        # unreadable name still means a part declared one, but there is nothing
        # to index, so drop it rather than ending the run.
        return
    if fn:
        names.append(" ".join(str(fn).split()))


def attachment_names_streamed(path: Path, line_cap: int = 8192,
                              header_cap: int = 1 << 20) -> list[str]:
    """Attachment filenames from a message file too large to hold in memory.

    An oversize message never reaches the parser, so index_text returned no
    attachment names for it at all. In the reference archive that hid the two
    largest attachments in the corpus: both 910 MB messages carry a real .zip
    and .png that were invisible to --stats, --has-attachment and the FTS
    attachments column while the bytes sat in the .eml the whole time. A guard
    that returns an empty list for input it cannot parse reads as "this message
    has no attachments" rather than "this message was not examined", which is
    the silent-success shape the rest of this exporter exists to avoid.

    Grepping the file for `filename=` would also match body text and quoted
    replies, so this tracks MIME structure instead: a header block is only read
    where one can legally start, directly after a delimiter line for a boundary
    an enclosing part already declared. Cost is bounded by header size, never by
    payload size, so a 910 MB message is scanned in one streaming pass.

    Body text is deliberately not extracted. Decoding a multi-hundred-MB payload
    to index it would cost the memory this path exists to avoid, and the caller
    records the message by metadata regardless.
    """
    names: list[str] = []
    boundaries: set[bytes] = set()
    block: list[bytes] = []
    block_len = 0
    in_headers = True  # a message begins with its own header block

    def flush() -> None:
        nonlocal block, block_len
        if block:
            _scan_header_block(
                b"".join(block).decode("utf-8", "surrogateescape"), boundaries, names)
        block, block_len = [], 0

    with path.open("rb") as fh:
        carry = b""
        while chunk := fh.read(1 << 20):
            lines = (carry + chunk).split(b"\n")
            carry = lines.pop()
            if len(carry) > line_cap:
                # A run this long with no newline is payload, and cannot be a
                # header line or a delimiter. Dropping it can only cost a
                # misaligned split until the next real newline arrives.
                carry = b""
            for line in lines:
                if in_headers:
                    if not line.rstrip(b"\r"):
                        flush()
                        in_headers = False
                    elif block_len < header_cap:
                        block.append(line + b"\n")
                        block_len += len(line) + 1
                    continue
                stripped = line.rstrip(b"\r")
                if stripped.startswith(b"--"):
                    inner = stripped[2:]
                    if any(inner in (b, b + b"--") for b in boundaries):
                        in_headers = True
        if in_headers:
            flush()
    return names


def index_text(msg_obj: Message | None, final_bytes: bytes | None,
               path: Path | None = None) -> tuple[str, list[str]]:
    """Body text + attachment names for the index, from whatever we have.

    Reuses the reassembled Message when one exists; otherwise parses the output
    bytes just for indexing. Falls back to a streaming header scan of the written
    file, which is what keeps an oversize or unparseable message's attachments in
    the index instead of recording it as having none. Body text is still skipped
    on that path; see attachment_names_streamed.
    """
    if msg_obj is not None:
        return extract_body_text(msg_obj), attachment_names(msg_obj)
    if final_bytes is not None and len(final_bytes) < OVERSIZE_BYTES:
        try:
            reparsed = email.message_from_bytes(final_bytes)
            return extract_body_text(reparsed), attachment_names(reparsed)
        except Exception:
            pass  # fall through: names from the file beat nothing at all
    if path is not None:
        try:
            return "", attachment_names_streamed(path)
        except Exception:
            return "", []
    return "", []


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

_HEADER_SPLIT = re.compile(rb"\r?\n\r?\n")


def raw_headers(rfc: bytes) -> str:
    m = _HEADER_SPLIT.search(rfc)
    head = rfc[: m.start()] if m else rfc[:65536]
    return head.decode("utf-8", errors="surrogateescape")


def unfold(headers: str) -> str:
    r"""Join RFC 5322 continuation lines.

    Exchange folds as `Message-ID:\n\t<...>`, so a same-line regex misses the
    Message-ID on a large fraction of an Exchange corpus (42 percent in the
    reference run). Unfolding first is not optional.
    """
    return re.sub(r"\n[ \t]+", " ", headers)


def get_header(unfolded: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}:[ \t]*(.*)$", unfolded, re.I | re.M)
    return m.group(1).strip() if m else ""


def decode_header_value(v: str) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return v


def parse_date(date_raw: str, plist: dict) -> tuple[int | None, str]:
    """(epoch, iso) from the Date header, falling back to the plist trailer's
    date-received when the header is absent or unparseable."""
    if date_raw:
        try:
            dt = parsedate_to_datetime(date_raw)
            return int(dt.timestamp()), dt.isoformat()
        except Exception:
            pass
    if plist.get("date-received"):
        epoch = int(plist["date-received"])
        return epoch, time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))
    return None, ""


def parse_sender(from_raw: str) -> tuple[str, str]:
    """(display_name, address) from a decoded From value."""
    m = re.search(r"<([^>]+)>", from_raw)
    addr = (m.group(1) if m else from_raw).strip().lower()
    name = from_raw.split("<")[0].strip().strip('"')
    return name, addr


# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


def mailbox_nodes(account_root: Path):
    """Yield (logical_path, [messages_dirs]) per mailbox.

    A .mbox node may hold its own messages, child .mbox dirs, both, or neither.
    Messages live under <StoreUUID>/Data/**/Messages, and the StoreUUID dir is
    per-store rather than per-mailbox, so glob for */Data instead of parsing it.
    """

    def recurse(node: Path, trail: list[str]):
        children = sorted(
            p for p in node.iterdir() if p.is_dir() and p.name.endswith(".mbox")
        )
        msg_dirs: list[Path] = []
        for sub in node.iterdir():
            if not sub.is_dir() or sub.name.endswith(".mbox"):
                continue
            data = sub / "Data"
            if data.is_dir():
                for root, dirs, _files in os.walk(data):
                    if os.path.basename(root) == "Messages":
                        msg_dirs.append(Path(root))
                        dirs[:] = []
        if trail:
            yield "/".join(trail), msg_dirs
        for child in children:
            yield from recurse(child, trail + [child.name[: -len(".mbox")]])

    for top in sorted(p for p in account_root.iterdir() if p.name.endswith(".mbox")):
        yield from recurse(top, [top.name[: -len(".mbox")]])


def iter_emlx(mdir: Path):
    """Yield (path, emlx_id, is_partial) for each message file in one
    Messages/ dir, skipping .emlxpart sidecars and non-numeric names."""
    for entry in sorted(mdir.iterdir()):
        name = entry.name
        if not name.endswith(".emlx"):
            continue
        stem = name[: -len(".emlx")]
        is_partial = stem.endswith(".partial")
        if is_partial:
            stem = stem[: -len(".partial")]
        try:
            emlx_id = int(stem)
        except ValueError:
            continue
        yield entry, emlx_id, is_partial


# Both output-name helpers strip the same characters a filesystem cannot take;
# they differ only in how they join what remains.
_ILLEGAL_CHARS = re.compile(r"[\x00-\x1f/\\:*?\"<>|]+")


def safe_component(s: str, maxlen: int = 80) -> str:
    s = _ILLEGAL_CHARS.sub(" ", unicodedata.normalize("NFC", s))
    s = " ".join(s.split())
    return s[:maxlen].strip(". ") or "untitled"


def slugify(s: str, maxlen: int = 60) -> str:
    s = _ILLEGAL_CHARS.sub(" ", unicodedata.normalize("NFC", s))
    s = re.sub(r"\s+", "-", s.strip())
    return re.sub(r"-{2,}", "-", s).strip("-.")[:maxlen] or "no-subject"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    total: int = 0
    written: int = 0
    verbatim: int = 0
    reassembled: int = 0
    reassembly_failed: int = 0
    oversize: int = 0
    filled_attachments: int = 0
    filled_emlxpart: int = 0
    filled_rfc822: int = 0
    filled_unmarked: int = 0
    missing_parts: int = 0
    qp_parts: int = 0
    uu_parts: int = 0
    xacl_mismatch: int = 0
    parse_errors: int = 0
    unreadable: int = 0
    error_detail: list = field(default_factory=list)
    missing_detail: list = field(default_factory=list)

    # Two distinct failure modes, split apart so the counters close exactly.
    # Conflating them once made the report unfalsifiable: a message whose
    # reassembly raised was written from raw bytes but counted in neither
    # verbatim nor reassembled, so verbatim + reassembled quietly came up short
    # of total with nothing naming the difference. Both are still counted in
    # parse_errors, which stays the single "did anything go wrong" number.

    def record_unreadable(self, file: Path, error: str) -> None:
        """The .emlx could not be split or read. Nothing is written."""
        self.unreadable += 1
        self.record_error(file, error)

    def record_reassembly_failure(self, file: Path, error: str) -> None:
        """Reassembly raised, so the message is written from its raw bytes.

        The message survives complete except for whatever was detached, which is
        why this is a distinct outcome from unreadable rather than a lost message.
        """
        self.reassembly_failed += 1
        self.record_error(file, error)

    def record_error(self, file: Path, error: str) -> None:
        self.parse_errors += 1
        self.error_detail.append({"file": str(file), "error": error})

    def record_missing(self, **detail) -> None:
        self.missing_parts += 1
        self.missing_detail.append(detail)

    def check_invariants(self) -> list[str]:
        """Return the accounting identities that do not hold.

        These are closed sums, not heuristics: every message takes exactly one
        outcome path, so a violation means a path was added without accounting
        for it. Cheap enough to run on every export.
        """
        broken = []
        if self.verbatim + self.reassembled + self.reassembly_failed != self.written:
            broken.append(
                f"verbatim({self.verbatim}) + reassembled({self.reassembled}) + "
                f"reassembly_failed({self.reassembly_failed}) != "
                f"written({self.written})")
        if self.written + self.unreadable != self.total:
            broken.append(
                f"written({self.written}) + unreadable({self.unreadable}) != "
                f"total({self.total})")
        if self.reassembly_failed + self.unreadable != self.parse_errors:
            broken.append(
                f"reassembly_failed({self.reassembly_failed}) + "
                f"unreadable({self.unreadable}) != "
                f"parse_errors({self.parse_errors})")
        return broken


@dataclass
class Exported:
    """Everything main() needs to index one written .eml."""

    emlx_id: int
    path: Path
    message_id: str
    date_iso: str
    epoch: int | None
    from_raw: str
    from_name: str
    from_addr: str
    to_raw: str
    cc_raw: str
    subject: str
    attachments: list[str]
    size: int
    is_partial: bool
    filled: int
    missing: int
    flags: int
    body: str


def export_message(
    entry: Path, emlx_id: int, is_partial: bool,
    mdir: Path, out_dir: Path, stats: Stats,
) -> Exported | None:
    """Convert one .emlx to a written .eml file. Returns None on parse failure
    (already recorded in stats); the message is skipped, never half-written."""
    stats.total += 1
    size = entry.stat().st_size
    plist: dict = {}
    rfc = b""
    msg_obj = None
    final_bytes = None
    out_path = None

    try:
        if size > OVERSIZE_BYTES:
            out_path = out_dir / f"oversize-{emlx_id}.eml"
            stream_rfc822(entry, out_path)
            with out_path.open("rb") as fh:
                uh = unfold(raw_headers(fh.read(65536)))
            stats.oversize += 1
            stats.verbatim += 1
        else:
            rfc, plist = split_emlx(entry)
            uh = unfold(raw_headers(rfc))
    except EmlxError as exc:
        stats.record_unreadable(entry, str(exc))
        return None
    except Exception as exc:
        stats.record_unreadable(entry, repr(exc))
        return None

    filled = missing = 0
    if out_path is None:
        if is_partial or b"X-Apple-Content-Length" in rfc[: 1 << 20]:
            try:
                msg_obj = email.message_from_bytes(rfc)
                filled, missing = reassemble(msg_obj, emlx_id, mdir, stats)
                buf = io.BytesIO()
                BytesGenerator(buf, mangle_from_=False, maxheaderlen=0).flatten(msg_obj)
                final_bytes = buf.getvalue()
                stats.reassembled += 1
            except Exception as exc:
                stats.record_reassembly_failure(entry, f"reassembly: {exc!r}")
                final_bytes = rfc
                msg_obj = None
        else:
            # Nothing detached: copy the on-disk bytes untouched. A
            # parse/reserialize round trip would mutate headers for no gain,
            # and this path is the majority of any store.
            final_bytes = rfc
            stats.verbatim += 1

    subject = decode_header_value(get_header(uh, "Subject"))
    from_raw = decode_header_value(get_header(uh, "From"))
    epoch, iso = parse_date(get_header(uh, "Date"), plist)
    from_name, from_addr = parse_sender(from_raw)

    if out_path is None:
        datestr = (time.strftime("%Y-%m-%d", time.localtime(epoch))
                   if epoch else "0000-00-00")
        out_path = out_dir / f"{datestr}_{emlx_id}_{slugify(subject)}.eml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(final_bytes)

    stats.written += 1
    body, atts = index_text(msg_obj, final_bytes, out_path)

    return Exported(
        emlx_id=emlx_id,
        path=out_path,
        message_id=get_header(uh, "Message-ID"),
        date_iso=iso,
        epoch=epoch,
        from_raw=from_raw,
        from_name=from_name,
        from_addr=from_addr,
        to_raw=decode_header_value(get_header(uh, "To")),
        cc_raw=decode_header_value(get_header(uh, "Cc")),
        subject=subject,
        attachments=atts,
        size=size,
        is_partial=is_partial,
        filled=filled,
        missing=missing,
        flags=int(plist.get("flags") or 0),
        body=body,
    )


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  emlx_id INTEGER, mailbox TEXT, eml_path TEXT, message_id TEXT,
  date_sent TEXT, date_epoch INTEGER, from_name TEXT, from_addr TEXT,
  to_addrs TEXT, cc_addrs TEXT, subject TEXT, n_attachments INTEGER,
  attachments TEXT, size_bytes INTEGER, is_partial INTEGER,
  reassembled INTEGER, missing_parts INTEGER, flags INTEGER,
  read INTEGER, flagged INTEGER, answered INTEGER, forwarded INTEGER,
  draft INTEGER, in_db INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mailbox ON messages(mailbox);
CREATE INDEX IF NOT EXISTS idx_date    ON messages(date_epoch);
CREATE INDEX IF NOT EXISTS idx_msgid   ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_from    ON messages(from_addr);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
  subject, body, from_addr, to_addrs, attachments,
  content='', tokenize='porter unicode61'
);
"""

INSERT_MESSAGE = (
    "INSERT INTO messages (emlx_id,mailbox,eml_path,message_id,date_sent,"
    "date_epoch,from_name,from_addr,to_addrs,cc_addrs,subject,n_attachments,"
    "attachments,size_bytes,is_partial,reassembled,missing_parts,flags,"
    "read,flagged,answered,forwarded,draft,in_db) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

INSERT_SEARCH = (
    "INSERT INTO search (rowid,subject,body,from_addr,to_addrs,attachments) "
    "VALUES (?,?,?,?,?,?)"
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--snapshot", required=True, type=Path,
                    help="frozen copy of the account dir (the <AccountUUID> folder)")
    ap.add_argument("--out", required=True, type=Path,
                    help="output dir; eml/, index.sqlite, messages.jsonl land here")
    ap.add_argument("--only", default=None,
                    help="export only mailboxes whose logical path starts with this")
    args = ap.parse_args()

    snapshot: Path = args.snapshot
    out: Path = args.out
    if not snapshot.is_dir():
        print(f"snapshot not found: {snapshot}", file=sys.stderr)
        return 2
    if not any(p.name.endswith(".mbox") for p in snapshot.iterdir()):
        print(f"no *.mbox dirs directly under {snapshot}\n"
              f"--snapshot must point at the <AccountUUID> folder itself, "
              f"not at V10/ or the archive root.", file=sys.stderr)
        return 2

    eml_dir = out / "eml"
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "index.sqlite"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)

    stats = Stats()
    t0 = time.time()
    jsonl = (out / "messages.jsonl").open("w", encoding="utf-8")

    for logical, msg_dirs in mailbox_nodes(snapshot):
        if args.only and not logical.startswith(args.only):
            continue
        if not msg_dirs:
            continue
        out_dir = eml_dir / Path(*[safe_component(c) for c in logical.split("/")])
        n_msgs = 0

        for mdir in msg_dirs:
            for entry, emlx_id, is_partial in iter_emlx(mdir):
                m = export_message(entry, emlx_id, is_partial, mdir, out_dir, stats)
                if m is None:
                    continue
                n_msgs += 1
                eml_rel = str(m.path.relative_to(out))
                atts_joined = " | ".join(m.attachments)
                jsonl.write(json.dumps({
                    "emlx_id": m.emlx_id, "mailbox": logical, "eml": eml_rel,
                    "message_id": m.message_id,
                    "date": m.date_iso, "from": m.from_raw, "to": m.to_raw,
                    "cc": m.cc_raw, "subject": m.subject,
                    "attachments": m.attachments,
                    "is_partial": m.is_partial, "parts_filled": m.filled,
                    "parts_missing": m.missing, "flags": m.flags,
                }, ensure_ascii=False) + "\n")
                cur = db.execute(INSERT_MESSAGE, (
                    m.emlx_id, logical, eml_rel, m.message_id, m.date_iso,
                    m.epoch, m.from_name, m.from_addr, m.to_raw, m.cc_raw,
                    m.subject, len(m.attachments), atts_joined, m.size,
                    int(m.is_partial), m.filled, m.missing, m.flags,
                    int(bool(m.flags & FLAG_BITS["read"])),
                    int(bool(m.flags & FLAG_BITS["flagged"])),
                    int(bool(m.flags & FLAG_BITS["answered"])),
                    int(bool(m.flags & FLAG_BITS["forwarded"])),
                    int(bool(m.flags & FLAG_BITS["draft"])),
                    0,
                ))
                db.execute(INSERT_SEARCH, (
                    cur.lastrowid, m.subject, m.body, m.from_addr, m.to_raw,
                    atts_joined,
                ))

        if n_msgs:
            db.commit()
            print(f"  {logical:<48} {n_msgs:>6} msgs  "
                  f"[{time.time()-t0:6.1f}s]", flush=True)

    jsonl.close()
    db.commit()
    db.execute("INSERT INTO search(search) VALUES('optimize')")
    db.commit()
    db.close()

    broken = stats.check_invariants()
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_snapshot": str(snapshot),
        "elapsed_seconds": round(time.time() - t0, 1),
        **{k: v for k, v in vars(stats).items() if not k.endswith("_detail")},
        "invariants_ok": not broken,
        "invariants_broken": broken,
        "missing_detail": stats.missing_detail[:500],
        "error_detail": stats.error_detail[:500],
    }
    (out / "export-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(
        {k: v for k, v in report.items() if not k.endswith("detail")}, indent=2))
    if broken:
        # A violated sum means an outcome path exists that nothing counts, so
        # the report can no longer be trusted to describe the run. Fail rather
        # than let a plausible-looking report stand in for a correct one.
        print("\nACCOUNTING BROKEN - the report does not add up:", file=sys.stderr)
        for line in broken:
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
