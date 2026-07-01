#!/usr/bin/env python3
"""Patch generated mcporter wrapper quirks for Apple Mail.

mcporter's generated CLI reserves a global ``--timeout`` flag for MCP request
timeout milliseconds. Apple Mail tools also expose a ``timeout`` argument in
seconds. If operators pass ``apple-mail --timeout 120 search-emails ...`` they
get a 120ms request ceiling instead of a 120s Mail timeout. This patch renames
the generated global request flag while preserving per-tool ``--timeout``.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


GLOBAL_TIMEOUT_OPTION = (
    'program.option("-t, --timeout <ms>", "Call timeout in milliseconds", '
    "(value) => parseInt(value, 10), 12e4);"
)
PATCHED_TIMEOUT_OPTION = (
    'program.option("-T, --request-timeout-ms <ms>", "Call timeout in milliseconds", '
    "(value) => parseInt(value, 10), 12e4);"
)
GLOBAL_TIMEOUT_HELP = '["-t, --timeout <ms>", "Call timeout in milliseconds"]'
PATCHED_TIMEOUT_HELP = '["-T, --request-timeout-ms <ms>", "Call timeout in milliseconds"]'


def patch_source(source: str) -> tuple[str, bool]:
    """Return patched wrapper source and whether it changed."""
    patched = source.replace(GLOBAL_TIMEOUT_OPTION, PATCHED_TIMEOUT_OPTION)
    patched = patched.replace(GLOBAL_TIMEOUT_HELP, PATCHED_TIMEOUT_HELP)
    patched = patched.replace(
        "globalOptions.timeout || 12e4",
        "globalOptions.requestTimeoutMs || 12e4",
    )
    return patched, patched != source


def patch_plugin_root(source: str, plugin_root: Path) -> tuple[str, bool]:
    """Point embedded generated-wrapper startup args at ``plugin_root``."""
    start_script = str((plugin_root / "start_mcp.sh").resolve())
    patched = source
    marker = '/plugin/start_mcp.sh"'
    while marker in patched:
        idx = patched.find(marker)
        path_start = patched.rfind('"', 0, idx)
        if path_start == -1:
            break
        old = patched[path_start + 1 : idx + len(marker) - 1]
        patched = patched[: path_start + 1] + start_script + patched[idx + len(marker) - 1 :]
        if old == start_script:
            break
    return patched, patched != source


def patch_file(path: Path, *, backup: bool = True, plugin_root: Path | None = None) -> bool:
    source = path.read_text(encoding="utf-8")
    patched, changed = patch_source(source)
    if plugin_root is not None:
        patched, plugin_changed = patch_plugin_root(patched, plugin_root)
        changed = changed or plugin_changed
    if not changed:
        return False
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak-timeout")
        shutil.copy2(path, backup_path)
    path.write_text(patched, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Patch generated apple-mail mcporter wrapper timeout flag."
    )
    parser.add_argument(
        "wrapper",
        nargs="?",
        default=str(Path.home() / ".local/bin/apple-mail"),
        help="Path to generated apple-mail wrapper",
    )
    parser.add_argument("--no-backup", action="store_true", help="Do not write .bak-timeout")
    parser.add_argument(
        "--plugin-root",
        default=None,
        help="Optional plugin directory to embed in generated wrapper startup args",
    )
    args = parser.parse_args(argv)

    path = Path(args.wrapper).expanduser().resolve()
    if not path.exists():
        print(f"skip: wrapper not found: {path}")
        return 0
    plugin_root = Path(args.plugin_root).expanduser() if args.plugin_root else None
    changed = patch_file(path, backup=not args.no_backup, plugin_root=plugin_root)
    print(("patched" if changed else "ok") + f": {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
