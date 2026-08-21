"""The MCP handshake must advertise THIS package's version, not the SDK's.

``mcp.server.fastmcp.FastMCP`` takes no ``version`` argument, and the low-level
``Server`` it builds falls back to ``importlib.metadata.version("mcp")`` when
its own ``version`` is ``None``. Before ``plugin/apple_mail_mcp/version.py``,
that fallback was live: every client saw the MCP SDK's version (1.29.x) as this
server's ``serverInfo.version``, so an installed 3.11.6 and a working-tree
3.11.7 were indistinguishable over the protocol. That is the drift that forces
live verification onto the CLI instead of the MCP tools.

These tests pin all three links in the chain:

1. ``pyproject.toml`` ``[project].version`` -> ``apple_mail_mcp.__version__``
2. ``apple_mail_mcp.__version__`` -> the handshake's ``serverInfo.version``
3. the handshake's version is not the ``mcp`` distribution's version

Link 2 is deliberately asserted through the real
``create_initialization_options()`` rather than by reading the attribute
``server.py`` writes, so a private-attribute rename inside the SDK fails here
instead of silently restoring the defect.
"""

from __future__ import annotations

import re
import unittest
from importlib.metadata import version as distribution_version
from pathlib import Path

import apple_mail_mcp
from apple_mail_mcp import server
from apple_mail_mcp.version import (
    DISTRIBUTION_NAME,
    FALLBACK_VERSION,
    _version_from_pyproject,
    resolve_version,
)

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"

REINSTALL_HINT = (
    "The package version and the installed distribution metadata disagree. If you just bumped "
    "pyproject.toml, refresh the editable install so the runtime version follows it: "
    ".venv/bin/pip install -e ."
)


def _pyproject_version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, "pyproject.toml has no [project].version"
    return match.group(1)


class PackageVersionSourceTests(unittest.TestCase):
    def test_package_version_matches_pyproject(self):
        self.assertEqual(apple_mail_mcp.__version__, _pyproject_version(), REINSTALL_HINT)

    def test_package_version_is_not_the_fallback(self):
        self.assertNotEqual(
            apple_mail_mcp.__version__,
            FALLBACK_VERSION,
            "Version resolution fell all the way through; the package could not identify itself.",
        )

    def test_resolver_reads_this_checkout_pyproject(self):
        self.assertEqual(_version_from_pyproject(PYPROJECT), _pyproject_version())

    def test_resolver_ignores_a_foreign_pyproject(self):
        """A ``pyproject.toml`` for some other project is never read as ours."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "pyproject.toml"
            foreign.write_text('[project]\nname = "something-else"\nversion = "9.9.9"\n', encoding="utf-8")
            self.assertIsNone(_version_from_pyproject(foreign))

    def test_resolver_returns_a_version_from_the_live_environment(self):
        self.assertEqual(resolve_version(), apple_mail_mcp.__version__)

    def test_distribution_name_matches_pyproject(self):
        name = re.search(r'^name\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
        assert name is not None
        self.assertEqual(DISTRIBUTION_NAME, name.group(1))


class HandshakeVersionTests(unittest.TestCase):
    def _initialization_options(self):
        return server._fastmcp_server._mcp_server.create_initialization_options()

    def test_handshake_advertises_the_package_version(self):
        self.assertEqual(
            self._initialization_options().server_version,
            apple_mail_mcp.__version__,
            "serverInfo.version drifted from the package version. If the MCP SDK renamed the "
            "`_mcp_server` seam, update server.py rather than this assertion.",
        )

    def test_handshake_does_not_advertise_the_mcp_sdk_version(self):
        """The exact regression: reporting the `mcp` library's version instead."""
        sdk_version = distribution_version("mcp")
        advertised = self._initialization_options().server_version
        if sdk_version == apple_mail_mcp.__version__:  # pragma: no cover - coincidence guard
            self.skipTest("mcp SDK and plugin versions coincide; this assertion cannot discriminate")
        self.assertNotEqual(advertised, sdk_version)

    def test_handshake_server_name_is_unchanged(self):
        self.assertEqual(self._initialization_options().server_name, "Apple Mail MCP")


if __name__ == "__main__":
    unittest.main()
