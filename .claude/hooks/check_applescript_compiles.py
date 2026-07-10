#!/usr/bin/env python3
"""Compile-check every AppleScript script-builder in a module without executing.

Catches parse-time syntax errors like the 3.3.0
``_build_awaiting_reply_inbox_script`` regression — which used
``header value of header named "X" of msg`` (not valid Mail.app
dictionary syntax) and failed with osascript ``-2740``. Existing
unit tests passed because they only asserted the row-format protocol
the Python parser consumes, not the AppleScript source itself.

Usage:
    python3 .claude/hooks/check_applescript_compiles.py <module_path> [<module_path>...]

Only checks modules under ``plugin/apple_mail_mcp/`` — files elsewhere are
silently skipped (they would not import with the expected package layout).

Discovery rule:
    Any function in the module whose name ends in ``_script`` AND
    whose return value (when called with sample kwargs) starts with
    ``tell application "Mail"`` is treated as a full-script builder
    and piped to ``osacompile -o /dev/null``. Fragment builders
    (e.g. ``inbox_mailbox_script``) are skipped because they only
    compile inside an enclosing ``tell`` block.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN_SRC = REPO / "plugin"

# Sample values for parameters that script builders commonly take.
# Add more here if a new builder uses a parameter name not listed.
SAMPLE_KWARGS: dict[str, object] = {
    "account": "Test Account",
    "escaped_account": "Test Account",
    "days_back": 7,
    "inbox_cap": 10,
    "sent_cap": 20,
    "max_results": 5,
    "scan_cap": 100,
    "mailbox": "INBOX",
    "mailbox_name": "INBOX",
    "include_read": True,
    "var_name": "myVar",
    "account_var": "targetAccount",
    "replied_var": "repliedIds",
    "subject_keyword": "test",
    "sender": "test@example.com",
    "body_text": "test",
    "newsletter_condition": "(false)",
    "body_scan_block": "",
    "date_check": "",
    # Calendar script builders (calendar_core/scripts_read.py, scripts_write.py)
    "calendar_name": "Test Calendar",
    "timeout_seconds": 30,
    "start_block": (
        "set windowStart to current date\n"
        "set time of windowStart to 0\n"
        "set day of windowStart to 1\n"
        "set year of windowStart to 2026\n"
        "set month of windowStart to 7\n"
        "set day of windowStart to 10\n"
        "set time of windowStart to 0"
    ),
    "end_block": (
        "set windowEnd to current date\n"
        "set time of windowEnd to 0\n"
        "set day of windowEnd to 1\n"
        "set year of windowEnd to 2026\n"
        "set month of windowEnd to 7\n"
        "set day of windowEnd to 17\n"
        "set time of windowEnd to 0"
    ),
    "uid_condition": 'uid is "TEST-UID"',
    "event_uid": "TEST-UID",
    "title": "Test Event",
    "new_name": "Renamed Calendar",
    "set_lines": "",
    "include_detail": False,
}


def _import_module(module_path: Path):
    if str(PLUGIN_SRC) not in sys.path:
        sys.path.insert(0, str(PLUGIN_SRC))
    rel = module_path.resolve().relative_to(PLUGIN_SRC)
    mod_name = ".".join(rel.with_suffix("").parts)
    spec = importlib.util.spec_from_file_location(mod_name, str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sample_kwargs_for(fn) -> dict[str, object] | None:
    sig = inspect.signature(fn)
    kwargs: dict[str, object] = {}
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if name in SAMPLE_KWARGS:
            kwargs[name] = SAMPLE_KWARGS[name]
        elif param.default is not inspect.Parameter.empty:
            continue
        else:
            return None
    return kwargs


def _osacompile_check(script: str) -> tuple[bool, str]:
    """Run osacompile in a tempfile. Returns (ok, stderr_excerpt)."""
    if not shutil.which("osacompile"):
        return True, "osacompile not on PATH; skipping"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".applescript", delete=False
    ) as src_f:
        src_f.write(script)
        src_path = src_f.name
    out_path = src_path.replace(".applescript", ".scpt")
    try:
        result = subprocess.run(
            ["osacompile", "-o", out_path, src_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            return False, err
        return True, ""
    finally:
        for p in (src_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _check_module(module_path: Path) -> list[tuple[str, str]]:
    """Return list of (function_name, error) for each builder that failed to compile.

    Silently skips files outside ``plugin/`` (e.g. test fixtures) — they
    can't be imported with the expected package layout.
    """
    try:
        module_path.resolve().relative_to(PLUGIN_SRC)
    except ValueError:
        return []
    try:
        mod = _import_module(module_path)
    except Exception as exc:
        print(
            f"  ⚠ {module_path}: import failed ({exc}) — skipping AppleScript check",
            file=sys.stderr,
        )
        return []

    builders = [
        (name, fn)
        for name, fn in inspect.getmembers(mod, inspect.isfunction)
        if name.endswith("_script") and fn.__module__ == mod.__name__
    ]
    failures: list[tuple[str, str]] = []
    for name, fn in builders:
        kwargs = _sample_kwargs_for(fn)
        if kwargs is None:
            continue
        try:
            text = fn(**kwargs)
        except Exception:
            continue
        if not isinstance(text, str):
            continue
        head = text.lstrip().split("\n", 1)[0]
        if 'tell application "Mail"' not in head and 'tell application "Calendar"' not in head:
            continue  # fragment, not a full script
        ok, err = _osacompile_check(text)
        if not ok:
            failures.append((name, err))
    return failures


def main(argv: list[str]) -> int:
    if not argv:
        return 0
    all_failures: list[tuple[Path, str, str]] = []
    for raw in argv:
        path = Path(raw)
        if not path.is_absolute():
            path = (REPO / path).resolve()
        if not path.exists() or path.suffix != ".py":
            continue
        for name, err in _check_module(path):
            all_failures.append((path, name, err))
    if not all_failures:
        return 0
    lines = ["AppleScript syntax check FAILED — these script builders did not compile:"]
    for path, name, err in all_failures:
        lines.append(f"  ✗ {path.relative_to(REPO)} :: {name}")
        for ln in err.splitlines():
            lines.append(f"      {ln}")
    lines.append("")
    lines.append(
        "This is the same class of bug as 3.3.0's get_awaiting_reply regression"
    )
    lines.append("(commit 18362ab → c9e92fb). Fix the builder before relying on tests —")
    lines.append("invalid AppleScript can pass unit tests when those tests only assert")
    lines.append("the row-format protocol the Python parser consumes.")
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
