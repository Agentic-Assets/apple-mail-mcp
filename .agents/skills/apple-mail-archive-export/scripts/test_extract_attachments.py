#!/usr/bin/env python3
"""
Prove that a hostile attachment filename cannot escape the output directory or
clobber anything.

  ./test_extract_attachments.py

Attachment filenames come from email, which means they come from strangers. A
name like `../../../.ssh/authorized_keys`, an absolute path, a NUL byte, a
400-character name, or a Windows reserved name is a write outside the tree or a
collision that overwrites real data. Asserting in a comment that the sanitizer
handles those proves nothing, so this does the write.

Each case is pushed through the real `declared_filename` -> `sanitize_filename`
-> `store_blob` -> `link_into` path onto the real filesystem, inside a temporary
directory that has a sibling `canary/authorized_keys` outside the sandbox root.
The test then asserts that the canary is byte-unchanged, that no file was created
outside the sandbox at all, and that every path written resolves inside it. A
sanitizer that merely returned a safe-looking string while `os.link` still wrote
somewhere else would fail here and pass a string-comparison test.

RFC 2047 and RFC 2231 encoded names go through a real `email.message.Message`, so
`get_filename()` does its actual parsing rather than being simulated. Cases a
header cannot legally carry, such as a literal NUL, are also run through
`sanitize_filename` directly, so no case is skipped for being inexpressible.

Exits non-zero on any failure, so it can gate a release.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_attachments import (  # noqa: E402
    WIN_RESERVED, Stats, UnsafePath, assert_inside, declared_filename,
    link_into, link_names, sanitize_filename, store_blob,
)

# Grouped by what each one attacks. Every entry is exercised twice: once as a
# real Content-Disposition header value, once straight into the sanitizer.
HOSTILE: list[tuple[str, str]] = [
    ("traversal", "../../../.ssh/authorized_keys"),
    ("traversal", "..\\..\\..\\Windows\\System32\\evil.dll"),
    ("traversal", "....//....//escape.txt"),
    ("traversal", "a/../../b/../../../c.txt"),
    ("traversal", "sub/dir/ok.pdf"),
    ("absolute", "/etc/passwd"),
    ("absolute", "/../../../../etc/shadow"),
    ("absolute", "C:\\Windows\\win.ini"),
    ("absolute", "\\\\server\\share\\payload.exe"),
    ("control char", "with\x00nul.txt"),
    ("control char", "line1\nline2.png"),
    ("control char", "tab\there.pdf"),
    ("control char", "\x1b]0;title\x07escape-sequence.txt"),
    ("overlong", "A" * 400 + ".pdf"),
    ("overlong", "\u4e2d" * 200 + ".docx"),
    ("overlong", "no-extension-" + "z" * 400),
    ("win reserved", "CON"),
    ("win reserved", "NUL.txt"),
    ("win reserved", "com1.dat"),
    ("win reserved", "LPT9.pdf"),
    ("win reserved", "aux"),
    ("degenerate", ".."),
    ("degenerate", "."),
    ("degenerate", ""),
    ("degenerate", "   "),
    ("degenerate", "..."),
    ("degenerate", "....."),
    ("shell-ish", "-rf.txt"),
    ("shell-ish", "--force.sh"),
    ("shell-ish", "$(whoami).txt"),
    ("win illegal", "trailing.dot."),
    ("win illegal", "file:name.txt"),
    ("win illegal", "star*quest?.txt"),
    ("win illegal", 'quote"name<>.txt'),
    ("win illegal", "pipe|name.txt"),
    ("unicode", "Cafe\u0301 decomposed.pdf"),
    ("unicode", "\ud800lone-surrogate.txt"),
    ("unicode", "\u202enrst.txt"),  # right-to-left override
    ("rfc2047", "=?gb2312?B?RmFyZXdlbGwgdG8gVGVzdGluZyB0aGUgQm90dG9tIExpbmUgoaogQ3Jvc3Mt?="
                " =?gb2312?Q?Border_Insurance_Must_Hold_the_Red_Line.docx?="),
    ("rfc2047", "=?UTF-8?B?U2NyZWVuc2hvdCAyMDI2LTAxLTA2IGF0IDQuNTguNDLigK9QTS5wbmc=?="),
    ("rfc2047", "=?bogus-charset?B?QUJD?=.txt"),
    ("rfc2047", "=?utf-8?B?Li4vLi4vLi4vZXNjYXBlLnR4dA==?="),  # traversal, encoded
    ("case", "Report.PDF"),
    ("case", "report.pdf"),
]

# filename*= is RFC 2231, which get_filename() collapses and no plain
# filename="..." header can express, so it needs its own header shape.
RFC2231 = [
    ("rfc2231", "attachment; filename*=utf-8''%E2%82%AC-invoice.pdf"),
    ("rfc2231", "attachment; filename*0=\"../../\"; filename*1=\"esc.txt\""),
    ("rfc2231", "attachment; filename*=utf-8''%2E%2E%2F%2E%2E%2Fesc2.txt"),
]


def part_with_disposition(value: str) -> Message:
    part = Message()
    part["Content-Type"] = "application/octet-stream"
    part["Content-Disposition"] = value
    part.set_payload("")
    return part


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="extract-attachments-test."))
    sandbox = root / "sandbox"
    view = sandbox / "by-folder"
    canary_dir = root / "canary"
    canary_dir.mkdir(parents=True)
    canary = canary_dir / "authorized_keys"
    canary.write_text("DO NOT TOUCH\n", encoding="utf-8")
    canary_listing = sorted(p.name for p in canary_dir.iterdir())

    stats = Stats()
    failures: list[str] = []
    rows: list[tuple[str, str, str, str]] = []

    # declared_filename must never raise: at its only production call site an
    # exception would end the whole run with a truncated manifest, so an
    # unreadable header has to degrade to (None, True) instead.
    cases: list[tuple[str, str, str | None]] = []
    for group, raw in HOSTILE + RFC2231:
        header = raw if group == "rfc2231" else f'attachment; filename="{raw}"'
        try:
            via_header, unreadable = declared_filename(
                part_with_disposition(header), stats, "test")
        except Exception as exc:
            via_header, unreadable = None, False
            failures.append(f"declared_filename raised on {raw[:40]!r}: {exc!r}")
        if unreadable and via_header is not None:
            failures.append(f"unreadable header still returned a name: {raw[:40]!r}")
        cases.append((group, raw, via_header))
        if group != "rfc2231":
            cases.append((group, raw, raw))  # direct path, for NUL and friends

    for i, (group, raw, candidate) in enumerate(cases):
        name = sanitize_filename(candidate, "SYNTHESIZED.bin", stats)
        encoded = name.encode("utf-8", "replace")
        stem = name.rpartition(".")[0] or name
        for bad, why in (
            ("/" in name or "\\" in name, "path separator survived"),
            ("\x00" in name, "NUL survived"),
            (any(ord(c) < 32 or ord(c) == 127 for c in name), "control char survived"),
            (name in ("", ".", ".."), "degenerate name"),
            (len(encoded) > 255, f"{len(encoded)} bytes exceeds the 255-byte limit"),
            (stem.upper() in WIN_RESERVED, "Windows reserved stem survived"),
            (name.startswith("-"), "leading dash survived"),
            (name != name.strip(" ."), "leading or trailing space or dot survived"),
        ):
            if bad:
                failures.append(f"{why}: {raw[:44]!r} -> {name!r}")

        # Distinct content per case, so a clobber shows up as a wrong link target.
        payload = f"{group}:{i}".encode()
        sha = hashlib.sha256(payload).hexdigest()
        blob, _fresh = store_blob(sandbox, sha, payload, stats)
        try:
            written = link_into(view, Path("Inbox") / "2024", blob,
                                link_names("2024-01-01", str(1000 + i), "1", name),
                                stats)
        except UnsafePath as exc:
            failures.append(f"UnsafePath writing {raw[:44]!r}: {exc}")
            continue
        target = sandbox / written
        try:
            assert_inside(sandbox, target)
        except UnsafePath as exc:
            failures.append(f"written path escaped for {raw[:44]!r}: {exc}")
        # The name the filesystem actually got, prefix and collision suffix
        # included, is what has to fit inside the 255-byte component limit.
        final = len(target.name.encode("utf-8", "replace"))
        if final > 255:
            failures.append(f"written basename is {final} bytes: {target.name[:40]!r}")
        if target.read_bytes() != payload:
            failures.append(f"{written} does not hold this occurrence's bytes; "
                            f"an earlier file was clobbered")
        rows.append((group, raw, name, written))

    # Identical bytes under an identical name must dedupe to one blob and one file.
    dup = b"identical bytes"
    sha = hashlib.sha256(dup).hexdigest()
    freshness = []
    for _ in range(3):
        blob, fresh = store_blob(sandbox, sha, dup, stats)
        freshness.append(fresh)
        link_into(view, Path("Inbox") / "2024", blob,
                  link_names("2024-01-01", "9999", "1", "same.txt"), stats)
    if freshness != [True, False, False]:
        failures.append(f"blob dedup did not hold: {freshness}")
    same = list((view / "Inbox" / "2024").glob("*same.txt"))
    if len(same) != 1:
        failures.append(f"identical occurrences produced {len(same)} files, want 1")

    # The two case-only variants carry different bytes, so on a case-insensitive
    # volume they must resolve to two distinct files rather than one overwriting
    # the other.
    case_files = {p.name for p in (view / "Inbox" / "2024").iterdir()
                  if "report" in p.name.lower()}
    if len(case_files) < 2:
        failures.append(f"case-only variants collapsed into {case_files}")

    escaped = sorted(str(p) for p in root.rglob("*")
                     if not str(p).startswith(str(sandbox))
                     and p not in (canary_dir, canary))
    if escaped:
        failures.append(f"files created outside the sandbox: {escaped[:5]}")
    if canary.read_text(encoding="utf-8") != "DO NOT TOUCH\n":
        failures.append("the canary file was overwritten")
    if sorted(p.name for p in canary_dir.iterdir()) != canary_listing:
        failures.append("a file appeared in the canary directory")
    if (root / ".ssh").exists():
        failures.append(f"{root / '.ssh'} was created by a traversal filename")

    width = 46
    print(f"{'group':<13} {'hostile input':<{width}}  written filename")
    print("-" * (width + 75))
    seen: set[tuple[str, str]] = set()
    for group, raw, name, written in rows:
        if (raw, name) in seen:
            continue
        seen.add((raw, name))
        shown = raw if len(raw) <= width - 4 else raw[: width - 7] + "..."
        print(f"{group:<13} {shown!r:<{width}}  {Path(written).name}")
    print("-" * (width + 75))
    print(f"cases exercised        : {len(cases)}")
    print(f"files written          : {len(rows)}")
    print(f"names rewritten        : {stats.filename_rewritten}")
    print(f"names synthesized      : {stats.filename_synthesized}")
    print(f"RFC 2047 words decoded : {stats.filename_rfc2047}")
    print(f"RFC 2047 undecodable   : {stats.filename_rfc2047_failed}")
    print(f"unreadable headers     : {stats.filename_unreadable} "
          f"(degraded, did not raise)")
    print(f"surrogates resolved    : {stats.filename_surrogates}")
    print(f"links created / reused : {stats.links_created} / {stats.links_deduped}")
    print(f"paths outside sandbox  : {len(escaped)}")
    print(f"canary contents        : "
          f"{canary.read_text(encoding='utf-8').strip()!r} (unchanged)")
    print(f"failures               : {len(failures)}")
    for f in failures:
        print(f"   {f}")
    shutil.rmtree(root, ignore_errors=True)
    print("\nPASS" if not failures else "\nFAIL")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
