"""AGENTIC-2361: ``export_emails(scope="single_email")`` must not truncate a
pre-existing user file.

Every other export scope writes into a per-scope subdirectory with an index
prefix (``message_id_export/``, ``thread_export/``, ``correspondent_export/``,
``{mailbox}_export/``). ``single_email`` alone built a bare
``<save_directory>/<subject>.<format>`` and opened it with
``set eof of fileRef to 0`` — an in-place truncate. With the documented default
``save_directory="~/Desktop"``, exporting a message titled "Quarterly Report"
destroyed an existing ``~/Desktop/Quarterly Report.txt``.

The fix matches the siblings: ``<save_directory>/single_email_export/`` with an
``{index}_{message_id}_{subject}`` filename. The subdirectory is created by the
tool, so a name collision can only ever overwrite this tool's own prior export,
never a file the user put there.

These tests mock ``run_applescript``. The truncation and traversal tests execute
the generated script's *write block* (Mail-free string and file operations only,
sliced verbatim out of the shipped script) through ``osascript`` against a pytest
``tmp_path``, so the non-truncation property is observed rather than asserted
from source text.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from apple_mail_mcp.tools import analytics as analytics_tools

_SENTINEL = b"PRE-EXISTING USER FILE. MUST SURVIVE AN EXPORT.\n"
_MESSAGE_ID = "4242"

requires_osascript = pytest.mark.skipif(
    shutil.which("osascript") is None,
    reason="osascript is unavailable on this host",
)


class _ScriptCapture:
    def __init__(self, return_value: str = "EXPORTING EMAIL\n\n✓ Email exported successfully!"):
        self.scripts: list[str] = []
        self._return_value = return_value

    def __call__(self, script: str, timeout: int | None = 120) -> str:
        self.scripts.append(script)
        return self._return_value

    @property
    def last_script(self) -> str:
        return self.scripts[-1] if self.scripts else ""


def _export(save_directory: Path, **kwargs) -> tuple[str, _ScriptCapture]:
    """Drive export_emails with a mocked run_applescript.

    ``save_directory`` is home-restricted by ``validate_save_path``, so HOME is
    repointed at the pytest scratch tree rather than writing anywhere real.
    """
    capture = _ScriptCapture()
    defaults = dict(account="Work", save_directory=str(save_directory))
    defaults.update(kwargs)
    with (
        patch.dict(os.environ, {"HOME": str(save_directory.parent)}),
        patch("apple_mail_mcp.tools.analytics.run_applescript", side_effect=capture),
    ):
        result = analytics_tools.export_emails(**defaults)
    return result, capture


def _single_email_script(save_directory: Path, **kwargs) -> str:
    _result, capture = _export(
        save_directory,
        scope="single_email",
        message_id=_MESSAGE_ID,
        mailbox="INBOX",
        format="txt",
        **kwargs,
    )
    assert capture.last_script, "expected single_email to generate an AppleScript"
    return capture.last_script


def _run_write_block(script: str, *, subject: str) -> str:
    """Execute the shipped script's Mail-free write block and return ``filePath``.

    The block is sliced verbatim between two markers in the generated script, so
    it exercises the real ``sanitize_delimiter_block`` fragment, the real
    filename/directory arithmetic, and the real
    ``open for access`` / ``set eof to 0`` / ``write`` idiom.
    """
    start = script.index("set safeSubject to messageSubject")
    end = script.index("close access fileRef") + len("close access fileRef")
    block = script[start:end]

    # Safety property: no `tell application` block means no application can be
    # dispatched to, so nothing here can reach Mail.app. The format branches for
    # html/eml are present but unreachable for a txt export; `aMessage` is bound
    # to `missing value` so a future edit that reaches one errors out loudly
    # instead of quietly acquiring a Mail reference.
    assert "tell application" not in block, f"write block must be Mail-free, got: {block!r}"

    harness = "\n".join(
        [
            f'set messageSubject to "{subject}"',
            'set messageSender to "sender@example.com"',
            'set messageDate to "2026-01-01"',
            'set messageContent to "synthetic body"',
            "set aMessage to missing value",
            block,
            "return filePath",
        ]
    )
    completed = subprocess.run(
        ["osascript", "-e", harness],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


# ---------------------------------------------------------------------------
# Script contract: scoped subdirectory + sibling-shaped filename
# ---------------------------------------------------------------------------


def test_single_email_writes_into_a_scoped_subdirectory(tmp_path):
    save_dir = tmp_path / "exports"
    script = _single_email_script(save_dir)

    assert f'set exportDir to "{save_dir}/single_email_export"' in script
    assert 'set filePath to exportDir & "/" & fileName' in script
    # The bare, caller-directory target is what truncated user files.
    assert f'set filePath to "{save_dir}/" & fileName' not in script


def test_single_email_filename_carries_index_and_message_id(tmp_path):
    script = _single_email_script(tmp_path / "exports")

    assert f'set exportIdText to "{_MESSAGE_ID}"' in script
    assert 'set fileName to (exportCount as string) & "_" & exportIdText & "_" & safeSubject & ".txt"' in script


def test_single_email_creates_the_export_subdirectory_before_writing(tmp_path):
    script = _single_email_script(tmp_path / "exports")

    mkdir_pos = script.find('do shell script "mkdir -p " & quoted form of exportDir')
    write_pos = script.find("open for access POSIX file filePath with write permission")
    assert mkdir_pos != -1, "export subdirectory must be created"
    assert write_pos != -1
    assert mkdir_pos < write_pos


def test_no_export_scope_binds_filepath_directly_in_save_directory(tmp_path):
    """Forward guard: the truncating shape must not come back in any scope."""
    save_dir = tmp_path / "exports"
    scopes = [
        {"scope": "single_email", "message_id": _MESSAGE_ID},
        {"scope": "entire_mailbox", "max_emails": 1},
        {
            "scope": "correspondent",
            "email_address": "person@example.com",
            "date_from": "2026-07-01",
            "max_emails": 1,
        },
    ]
    for kwargs in scopes:
        _result, capture = _export(save_dir, **kwargs)
        for script in capture.scripts:
            assert f'set filePath to "{save_dir}/" & fileName' not in script, (
                f"scope {kwargs['scope']!r} writes a bare filename into the caller's directory"
            )


# ---------------------------------------------------------------------------
# Executed: a pre-existing file at the old target survives the export
# ---------------------------------------------------------------------------


@requires_osascript
def test_single_email_export_does_not_truncate_pre_existing_file(tmp_path):
    save_dir = tmp_path / "exports"
    save_dir.mkdir()
    subject = "Quarterly Report"
    victim = save_dir / f"{subject}.txt"  # exactly the old target path
    victim.write_bytes(_SENTINEL)

    script = _single_email_script(save_dir)
    written = _run_write_block(script, subject=subject)

    assert victim.read_bytes() == _SENTINEL, "pre-existing file at the old target was truncated"
    assert written == str(save_dir / "single_email_export" / f"1_{_MESSAGE_ID}_{subject}.txt")
    assert Path(written).is_file()
    assert Path(written).read_text().startswith("Subject: Quarterly Report")


@requires_osascript
def test_single_email_subject_with_slashes_cannot_escape_the_export_directory(tmp_path):
    """``sanitize_delimiter_block`` collapses "/" to "-", so a crafted subject
    stays inside the export directory. Collision, not traversal, was the risk."""
    save_dir = tmp_path / "exports"
    save_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    decoy = outside / "passwd"
    decoy.write_bytes(_SENTINEL)

    script = _single_email_script(save_dir)
    written = Path(_run_write_block(script, subject="../../outside/passwd"))

    assert written.parent == save_dir / "single_email_export"
    assert written.name == f"1_{_MESSAGE_ID}_..-..-outside-passwd.txt"
    assert decoy.read_bytes() == _SENTINEL
