"""Single runtime source of truth for this package's own version string.

Why this module exists
----------------------
``mcp.server.fastmcp.FastMCP`` takes no ``version`` argument. It builds the
low-level ``mcp.server.lowlevel.Server`` without one, and that server's
``create_initialization_options()`` falls back to
``importlib.metadata.version("mcp")`` when ``Server.version`` is ``None``. The
practical result is that the MCP handshake advertised the **MCP SDK's** version
(e.g. ``1.29.0``) as ``serverInfo.version`` for every client of this plugin, so
an installed 3.11.6 and a working-tree 3.11.7 were indistinguishable over the
protocol. That is the exact drift hazard that forces live verification onto the
CLI instead of the MCP tools. ``server.py`` stamps :data:`__version__` onto the
low-level server so the handshake reports this package.

Resolution order
----------------
1. ``importlib.metadata.version("mcp-apple-mail")`` — the installed
   distribution's own metadata, which is what a *shipped* plugin runs against
   and is generated from ``pyproject.toml`` at build time.
2. ``pyproject.toml``'s ``[project].version`` when the package is being run
   straight out of a repo checkout with no distribution installed. Accepted
   only when that file also declares ``name = "mcp-apple-mail"``, so an
   unrelated ``pyproject.toml`` further up the tree can never be read as ours.
3. :data:`FALLBACK_VERSION` — a self-evidently unusable string, chosen so a
   client sees "this build could not identify itself" rather than a plausible
   wrong number.

``pyproject.toml`` stays the single *authoring* source of truth (the release
checklist and ``tools/validators/validate_manifests.py`` both key off it);
this module is the single *runtime* reader of it.
``tests/infra/test_server_version_parity.py`` fails if the two ever disagree,
or if the advertised handshake version drifts from either.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from pathlib import Path

#: PyPI distribution name declared by ``pyproject.toml`` ``[project].name``.
DISTRIBUTION_NAME = "mcp-apple-mail"

#: Reported when neither installed metadata nor a sibling ``pyproject.toml``
#: can identify this build. Deliberately not a plausible release number.
FALLBACK_VERSION = "0+unknown"

# ``[project]`` table keys, matched line-anchored so a `version = "..."` under
# some later table (e.g. a tool section) cannot be mistaken for the project's.
_PROJECT_TABLE = re.compile(r"^\[project\]\s*$", re.MULTILINE)
_NEXT_TABLE = re.compile(r"^\[", re.MULTILINE)
_NAME_LINE = re.compile(r'^name\s*=\s*"([^"]+)"', re.MULTILINE)
_VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _version_from_pyproject(pyproject: Path) -> str | None:
    """Return ``[project].version`` from ``pyproject`` when it is *this* project.

    Hand-rolled rather than ``tomllib``-based because this runs at import time
    on Python 3.10, where ``tomllib`` does not exist. The parse is deliberately
    narrow: only the ``[project]`` table is considered, and only when its
    ``name`` matches :data:`DISTRIBUTION_NAME`.
    """
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None
    start = _PROJECT_TABLE.search(text)
    if start is None:
        return None
    rest = text[start.end() :]
    end = _NEXT_TABLE.search(rest)
    table = rest[: end.start()] if end else rest
    name = _NAME_LINE.search(table)
    if name is None or name.group(1) != DISTRIBUTION_NAME:
        return None
    found = _VERSION_LINE.search(table)
    return found.group(1) if found else None


def resolve_version() -> str:
    """Resolve this package's version. See the module docstring for the order."""
    try:
        return _distribution_version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        pass
    # ``plugin/apple_mail_mcp/version.py`` -> repo root. An installed wheel puts
    # this module in ``site-packages/apple_mail_mcp/``, where the same walk-up
    # lands on a library directory that holds no ``pyproject.toml`` at all.
    checkout_version = _version_from_pyproject(Path(__file__).resolve().parents[2] / "pyproject.toml")
    return checkout_version or FALLBACK_VERSION


#: This package's version, as advertised in the MCP handshake's ``serverInfo``.
__version__ = resolve_version()
