"""Tests for tools/validators/validate_manifests.py (Phase 1 CI guardrails)."""

import json
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "validators"))

import validate_manifests


class ValidateManifestsTests(unittest.TestCase):
    def test_validate_manifests_passes_on_current_repo(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "validators" / "validate_manifests.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=result.stdout + result.stderr,
        )
        self.assertIn("validate_manifests: OK", result.stdout)
        self.assertIn("module_budget_warn=", result.stdout)

    def test_public_version_checks_cover_all_release_surfaces(self):
        checks = [
            (path.relative_to(validate_manifests.ROOT).as_posix(), field, label)
            for path, field, label in validate_manifests._public_version_checks()
        ]

        self.assertEqual(
            checks,
            [
                ("plugin/.claude-plugin/plugin.json", "version", "Claude plugin manifest"),
                ("plugin/.codex-plugin/plugin.json", "version", "Codex plugin manifest"),
                (".claude-plugin/marketplace.json", "plugins[0].version", "Claude marketplace plugin"),
                ("server.json", "version", "MCP server metadata"),
                ("server.json", "packages[0].version", "MCP server package"),
                ("apple-mail-mcpb/manifest.json", "version", "MCPB manifest"),
            ],
        )

    def test_public_version_checks_reject_codex_plugin_version_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plugin/.claude-plugin").mkdir(parents=True)
            (root / "plugin/.codex-plugin").mkdir(parents=True)
            (root / ".claude-plugin").mkdir()
            (root / "apple-mail-mcpb").mkdir()
            (root / "plugin/.claude-plugin/plugin.json").write_text(
                json.dumps({"version": "3.9.1"}),
                encoding="utf-8",
            )
            (root / "plugin/.codex-plugin/plugin.json").write_text(
                json.dumps({"version": "0.0.0"}),
                encoding="utf-8",
            )
            (root / ".claude-plugin/marketplace.json").write_text(
                json.dumps({"plugins": [{"version": "3.9.1"}]}),
                encoding="utf-8",
            )
            (root / "server.json").write_text(
                json.dumps({"version": "3.9.1", "packages": [{"version": "3.9.1"}]}),
                encoding="utf-8",
            )
            (root / "apple-mail-mcpb/manifest.json").write_text(
                json.dumps({"version": "3.9.1"}),
                encoding="utf-8",
            )

            errors: list[str] = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_public_versions("3.9.1", errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(errors, ["Codex plugin manifest: got '0.0.0', expected '3.9.1'"])

    def test_module_line_budget_passes_on_current_repo(self):
        errors: list[str] = []
        warn_count = validate_manifests._check_module_line_budget(errors)
        self.assertEqual(errors, [])
        # The module-line-budget cleanup split the last oversized module
        # (tools/validators/validate_manifests.py) into the manifest_checks package, so no
        # module exceeds the 600 LOC budget; warn_count is now 0.
        self.assertEqual(warn_count, 0)

    def test_active_doc_tool_count_claims_pass_on_current_repo(self):
        errors = []
        actual_count = len(validate_manifests._extract_registered_tool_names())

        validate_manifests._check_active_doc_tool_count_claims(actual_count, errors)

        self.assertEqual(errors, [])

    def test_active_doc_tool_count_claims_rejects_stale_required_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Apple Mail MCP has 28 tools\n", encoding="utf-8")
            errors = []

            validate_manifests._check_active_doc_tool_count_claims(
                29,
                errors,
                root=root,
                required_docs=("AGENTS.md",),
                scan_only_docs=(),
            )

        self.assertEqual(errors, ["AGENTS.md:1: tool-count claim 28, registry has 29"])

    def test_active_doc_tool_count_claims_requires_required_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Apple Mail MCP active guidance\n", encoding="utf-8")
            errors = []

            validate_manifests._check_active_doc_tool_count_claims(
                29,
                errors,
                root=root,
                required_docs=("AGENTS.md",),
                scan_only_docs=(),
            )

        self.assertEqual(errors, ["AGENTS.md: missing active tool-count claim"])

    def test_active_doc_tool_count_claims_allows_scan_only_without_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir = root / "docs"
            docs_dir.mkdir()
            (docs_dir / "CLAUDE-conventions.md").write_text("Policy text without a numeric count\n", encoding="utf-8")
            errors = []

            validate_manifests._check_active_doc_tool_count_claims(
                29,
                errors,
                root=root,
                required_docs=(),
                scan_only_docs=("docs/CLAUDE-conventions.md",),
            )

        self.assertEqual(errors, [])

    def test_active_doc_tool_count_claims_ignores_historical_task_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Apple Mail MCP has 29 tools\n", encoding="utf-8")
            tasks_dir = root / "tasks"
            tasks_dir.mkdir()
            (tasks_dir / "old-plan.md").write_text("Historical note from when there were 28 tools\n", encoding="utf-8")
            errors = []

            validate_manifests._check_active_doc_tool_count_claims(
                29,
                errors,
                root=root,
                required_docs=("AGENTS.md",),
                scan_only_docs=(),
            )

        self.assertEqual(errors, [])

    def test_active_doc_tool_count_claims_checks_tools_module_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools_dir = root / "plugin/apple_mail_mcp/tools"
            tools_dir.mkdir(parents=True)
            (tools_dir / "CLAUDE.md").write_text(
                "\n".join(
                    [
                        "All handlers. **29 tools**.",
                        "| Module | # | Purpose |",
                        "| --- | --- | --- |",
                        "| `inbox.py` | 6 | Listing |",
                        "| `search.py` | 3 | Search |",
                        "| `compose.py` | 6 | Compose |",
                        "| `manage.py` | 6 | Manage |",
                        "| `analytics.py` | 4 | Analytics |",
                        "| `smart_inbox.py` | 3 | Smart |",
                    ]
                ),
                encoding="utf-8",
            )
            errors = []

            validate_manifests._check_active_doc_tool_count_claims(
                29,
                errors,
                root=root,
                required_docs=("plugin/apple_mail_mcp/tools/CLAUDE.md",),
                scan_only_docs=(),
            )

        self.assertEqual(errors, ["plugin/apple_mail_mcp/tools/CLAUDE.md: module table sums to 28, registry has 29"])

    def test_compare_zip_members_reports_stale_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            archive = tmp_path / "artifact.zip"
            source.write_text("current", encoding="utf-8")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("payload/source.txt", "old")

            errors = []
            validate_manifests._compare_zip_members(
                archive,
                [(source, "payload/source.txt")],
                "artifact.zip",
                errors,
            )

        self.assertEqual(
            errors,
            ["artifact.zip: stale payload/source.txt; rebuild artifact.zip"],
        )

    def test_compare_zip_members_reports_missing_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            archive = tmp_path / "artifact.zip"
            source.write_text("current", encoding="utf-8")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("payload/other.txt", "current")

            errors = []
            validate_manifests._compare_zip_members(
                archive,
                [(source, "payload/source.txt")],
                "artifact.zip",
                errors,
            )

        self.assertEqual(errors, ["artifact.zip: missing payload/source.txt"])

    def test_compare_zip_members_reports_unexpected_extra_member_when_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            archive = tmp_path / "artifact.zip"
            source.write_text("current", encoding="utf-8")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("payload/source.txt", "current")
                zf.writestr("payload/stale.txt", "deleted source")

            errors = []
            validate_manifests._compare_zip_members(
                archive,
                [(source, "payload/source.txt")],
                "artifact.zip",
                errors,
                exact_members=True,
            )

        self.assertEqual(
            errors,
            ["artifact.zip: unexpected payload/stale.txt; rebuild artifact.zip"],
        )

    def test_compare_zip_members_reports_duplicate_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.txt"
            archive = tmp_path / "artifact.zip"
            source.write_text("current", encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("payload/source.txt", "old")
                    zf.writestr("payload/source.txt", "current")

            errors = []
            validate_manifests._compare_zip_members(
                archive,
                [(source, "payload/source.txt")],
                "artifact.zip",
                errors,
                exact_members=True,
            )

        self.assertIn(
            "artifact.zip: duplicate member payload/source.txt; rebuild artifact.zip",
            errors,
        )

    def test_plugin_manifest_contract_rejects_strict_validator_and_runtime_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "plugin/.claude-plugin"
            manifest_path.mkdir(parents=True)
            (manifest_path / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "apple-mail",
                        "description": "Apple Mail with 29 tools",
                        "version": "1.0.0",
                        "commands": "./commands",
                        "mcpServers": {
                            "apple-mail": {
                                "command": "bash",
                                "args": ["start_mcp.sh"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_plugin_manifest_contract(errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertIn(
            "plugin.json: unsupported strict-validator field 'commands'; ship workflow entry points as skills only",
            errors,
        )
        self.assertIn("plugin.json mcpServers.apple-mail.command: expected /bin/bash", errors)
        self.assertIn(
            "plugin.json mcpServers.apple-mail.args: first arg must be ${CLAUDE_PLUGIN_ROOT}/start_mcp.sh",
            errors,
        )
        self.assertIn(
            "plugin.json mcpServers.apple-mail.args: missing --draft-safe",
            errors,
        )

    def test_mcpb_runtime_contract_rejects_missing_draft_safe_and_bad_entrypoint(self):
        manifest = {
            "user_config": {
                "default_account": {},
            },
            "server": {
                "type": "node",
                "entry_point": "missing.py",
                "mcp_config": {
                    "command": "python3",
                    "args": ["apple_mail_mcp.py"],
                    "env": {
                        "USER_EMAIL_PREFERENCES": "${user_config.missing_preferences}",
                        "DEFAULT_MAIL_ACCOUNT": "${user_config.default_account}",
                    },
                },
            },
        }
        errors = []

        validate_manifests._check_mcpb_runtime_contract(manifest, errors)

        self.assertEqual(
            errors,
            [
                "mcpb manifest server.type: expected python",
                "mcpb manifest server.entry_point: missing plugin/missing.py",
                "mcpb manifest server.mcp_config.command: expected /bin/bash",
                "mcpb manifest server.mcp_config.args: first arg must be ${__dirname}/start_mcp.sh",
                "mcpb manifest server.mcp_config.args: missing --draft-safe",
                "mcpb manifest server.mcp_config.env.USER_EMAIL_PREFERENCES: unknown user_config.missing_preferences",
                "mcpb manifest server.mcp_config.env: missing DEFAULT_MAIL_SIGNATURE",
            ],
        )

    def test_marketplace_contract_checks_source_and_skill_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marketplace = root / ".claude-plugin"
            marketplace.mkdir()
            (marketplace / "marketplace.json").write_text(
                json.dumps(
                    {
                        "plugins": [
                            {
                                "name": "wrong-name",
                                "version": "2.0.0",
                                "source": "plugin",
                                "skills": [
                                    "./plugin/skills/good-skill",
                                    "./plugin/skills/missing-skill",
                                    "plugin/skills/not-relative",
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            good = root / "plugin/skills/good-skill"
            good.mkdir(parents=True)
            (good / "SKILL.md").write_text("---\nname: good\n---\n", encoding="utf-8")

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_marketplace_contract("1.0.0", errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(
            errors,
            [
                ".claude-plugin/marketplace.json name: got 'None', expected 'Agentic-Assets'",
                "marketplace.json plugins[0].source: path must start with ./ (got plugin)",
                "marketplace.json plugins[0].name: got 'wrong-name', expected plugin.json name 'missing'",
                "marketplace.json plugins[0].version: got '2.0.0', expected '1.0.0'",
                "marketplace.json plugins[0].skills: missing ./plugin/skills/missing-skill/SKILL.md",
                "marketplace.json plugins[0].skills: path must start with ./ (got plugin/skills/not-relative)",
            ],
        )

    def _write_dual_manifest_fixture(
        self, root: Path, *, strict: bool, market_components: dict, plugin_components: dict
    ) -> None:
        plugin_dir = root / "plugin/.claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "fixture", **plugin_components}),
            encoding="utf-8",
        )
        marketplace = root / ".claude-plugin"
        marketplace.mkdir()
        market_entry = {
            "name": "fixture",
            "version": "1.0.0",
            "source": "./plugin",
            **market_components,
        }
        if strict:
            market_entry["strict"] = True
        (marketplace / "marketplace.json").write_text(
            json.dumps({"plugins": [market_entry]}),
            encoding="utf-8",
        )
        skill_dir = root / "plugin/skills/op"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: op\n---\n", encoding="utf-8")

    def _write_codex_plugin_fixture(
        self,
        root: Path,
        *,
        marketplace: dict,
        manifest: dict,
        mcp: dict,
        include_skills_dir: bool = False,
    ) -> None:
        for path, payload in (
            (root / ".agents/plugins/marketplace.json", marketplace),
            (root / "plugin/.codex-plugin/plugin.json", manifest),
            (root / "plugin/.mcp.json", mcp),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        if include_skills_dir:
            (root / "plugin/skills").mkdir(parents=True)

    def test_marketplace_contract_rejects_dual_component_declarations(self):
        """Regression: 2026-05-25 — Claude Code surfaced 'conflicting manifests'
        because marketplace.json listed `skills` while plugin.json declared
        `mcpServers` with strict: false. The fix removed the redundant skills
        array (auto-discovery handles them); this guards against re-introducing
        the conflict."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_dual_manifest_fixture(
                root,
                strict=False,
                market_components={"skills": ["./plugin/skills/op"]},
                plugin_components={"mcpServers": {"fixture": {"command": "/bin/true"}}},
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_marketplace_contract("1.0.0", errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertIn(
            "marketplace.json plugins[0]: component fields ['skills'] "
            "conflict with plugin.json components ['mcpServers']; "
            "remove components from one manifest or set strict: true "
            "(Claude Code rejects the install otherwise)",
            errors,
        )

    def test_marketplace_contract_allows_dual_components_when_strict_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_dual_manifest_fixture(
                root,
                strict=True,
                market_components={"skills": ["./plugin/skills/op"]},
                plugin_components={"mcpServers": {"fixture": {"command": "/bin/true"}}},
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_marketplace_contract("1.0.0", errors)
            finally:
                validate_manifests.ROOT = original_root

        conflict_errors = [e for e in errors if "conflict with plugin.json" in e]
        self.assertEqual(conflict_errors, [])

    def test_marketplace_contract_allows_components_only_in_plugin_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_dual_manifest_fixture(
                root,
                strict=False,
                market_components={},
                plugin_components={"mcpServers": {"fixture": {"command": "/bin/true"}}},
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_marketplace_contract("1.0.0", errors)
            finally:
                validate_manifests.ROOT = original_root

        conflict_errors = [e for e in errors if "conflict with plugin.json" in e]
        self.assertEqual(conflict_errors, [])

    def test_codex_plugin_contract_rejects_manifest_marketplace_and_mcp_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_codex_plugin_fixture(
                root,
                marketplace={
                    "name": "wrong-marketplace",
                    "interface": {"displayName": "Wrong"},
                    "plugins": [
                        {
                            "name": "wrong-plugin",
                            "source": {"source": "git", "path": "plugin"},
                            "policy": {
                                "installation": "BLOCKED",
                                "authentication": "NEVER",
                            },
                            "category": "Email",
                        }
                    ],
                },
                manifest={
                    "name": "wrong-plugin",
                    "version": "9.9.9",
                    "description": "Apple Mail with 27 MCP tools",
                    "homepage": "https://github.com/Agentic-Assets/apple-mail-mcp",
                    "repository": "https://github.com/Agentic-Assets/apple-mail-mcp",
                    "license": "MIT",
                    "keywords": ["apple-mail"],
                    "skills": "skills",
                    "mcpServers": "./missing.json",
                    "interface": {},
                },
                mcp={
                    "mcpServers": {
                        "apple-mail": {
                            "command": "bash",
                            "args": ["start_mcp.sh"],
                        }
                    }
                },
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_codex_plugin_contract("3.6.0", 29, errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(
            errors,
            [
                ".agents/plugins/marketplace.json name: got 'wrong-marketplace', expected 'Agentic-Assets'",
                ".agents/plugins/marketplace.json interface.displayName: got 'Wrong', expected 'Agentic Assets'",
                ".agents/plugins/marketplace.json plugins[0].name: got 'wrong-plugin', expected 'apple-mail'",
                ".agents/plugins/marketplace.json plugins[0].source: expected {'source': 'local', 'path': './plugin'}",
                ".agents/plugins/marketplace.json plugins[0].policy.installation: got 'BLOCKED', expected 'AVAILABLE'",
                ".agents/plugins/marketplace.json plugins[0].policy.authentication: got 'NEVER', expected 'ON_INSTALL'",
                ".agents/plugins/marketplace.json plugins[0].category: got 'Email', expected 'Productivity'",
                "plugin/.codex-plugin/plugin.json: missing author",
                "plugin/.codex-plugin/plugin.json name: got 'wrong-plugin', expected 'apple-mail'",
                "plugin/.codex-plugin/plugin.json version: got '9.9.9', expected '3.6.0'",
                "plugin/.codex-plugin/plugin.json description: description claims 27 tools, registry has 29",
                "plugin/.codex-plugin/plugin.json skills: got 'skills', expected './skills'",
                "plugin/.codex-plugin/plugin.json mcpServers: got './missing.json', expected './.mcp.json'",
                "plugin/.mcp.json mcpServers.apple-mail.command: expected /bin/bash",
                "plugin/.mcp.json mcpServers.apple-mail.args: first arg must be ./start_mcp.sh",
                "plugin/.mcp.json mcpServers.apple-mail.args: missing --draft-safe",
                "plugin/.mcp.json mcpServers.apple-mail.cwd: got 'None', expected '.'",
            ],
        )

    def test_codex_plugin_contract_rejects_literal_claude_plugin_root_launcher(self):
        """Regression: Codex 0.133.0 installed the plugin but left this argv
        literal, so the MCP server never started and no tools registered."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_codex_plugin_fixture(
                root,
                marketplace={
                    "name": "Agentic-Assets",
                    "interface": {"displayName": "Agentic Assets"},
                    "plugins": [
                        {
                            "name": "apple-mail",
                            "source": {"source": "local", "path": "./plugin"},
                            "policy": {
                                "installation": "AVAILABLE",
                                "authentication": "ON_INSTALL",
                            },
                            "category": "Productivity",
                        }
                    ],
                },
                manifest={
                    "name": "apple-mail",
                    "version": "3.6.0",
                    "description": "Apple Mail with 29 MCP tools",
                    "author": {"name": "Agentic Assets"},
                    "homepage": "https://github.com/Agentic-Assets/apple-mail-mcp",
                    "repository": "https://github.com/Agentic-Assets/apple-mail-mcp",
                    "license": "MIT",
                    "keywords": ["apple-mail"],
                    "skills": "./skills",
                    "mcpServers": "./.mcp.json",
                    "interface": {"displayName": "Apple Mail"},
                },
                mcp={
                    "mcpServers": {
                        "apple-mail": {
                            "command": "/bin/bash",
                            "args": [
                                "${CLAUDE_PLUGIN_ROOT}/start_mcp.sh",
                                "--draft-safe",
                            ],
                        }
                    },
                },
                include_skills_dir=True,
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_codex_plugin_contract("3.6.0", 29, errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertIn(
            "plugin/.mcp.json mcpServers.apple-mail.args: first arg must be ./start_mcp.sh",
            errors,
        )
        self.assertIn(
            "plugin/.mcp.json mcpServers.apple-mail.cwd: got 'None', expected '.'",
            errors,
        )
        self.assertIn(
            "plugin/.mcp.json mcpServers.apple-mail: must not contain literal ${CLAUDE_PLUGIN_ROOT} in Codex launcher fields",
            errors,
        )

    def test_codex_plugin_contract_accepts_valid_marketplace_manifest_and_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_codex_plugin_fixture(
                root,
                marketplace={
                    "name": "Agentic-Assets",
                    "interface": {"displayName": "Agentic Assets"},
                    "plugins": [
                        {
                            "name": "apple-mail",
                            "source": {"source": "local", "path": "./plugin"},
                            "policy": {
                                "installation": "AVAILABLE",
                                "authentication": "ON_INSTALL",
                            },
                            "category": "Productivity",
                        }
                    ],
                },
                manifest={
                    "name": "apple-mail",
                    "version": "3.6.0",
                    "description": "Apple Mail with 29 MCP tools",
                    "author": {"name": "Agentic Assets"},
                    "homepage": "https://github.com/Agentic-Assets/apple-mail-mcp",
                    "repository": "https://github.com/Agentic-Assets/apple-mail-mcp",
                    "license": "MIT",
                    "keywords": ["apple-mail"],
                    "skills": "./skills",
                    "mcpServers": "./.mcp.json",
                    "interface": {"displayName": "Apple Mail"},
                },
                mcp={
                    "mcpServers": {
                        "apple-mail": {
                            "command": "/bin/bash",
                            "args": [
                                "./start_mcp.sh",
                                "--draft-safe",
                            ],
                            "cwd": ".",
                        }
                    },
                },
                include_skills_dir=True,
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_codex_plugin_contract("3.6.0", 29, errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(errors, [])

    def test_codex_install_smoke_uses_marketplace_then_plugin_id(self):
        """Keep the Codex install path executable and discoverable."""
        script = (ROOT / "tools" / "gates" / "validate-codex-plugin.sh").read_text(encoding="utf-8")

        self.assertIn('export CODEX_HOME="$TMP_HOME"', script)
        self.assertIn(
            'CODEX_MARKETPLACE_SOURCE="${APPLE_MAIL_CODEX_MARKETPLACE_SOURCE:-https://github.com/Agentic-Assets/apple-mail-mcp.git}"',
            script,
        )
        self.assertIn('codex plugin marketplace add "$CODEX_MARKETPLACE_SOURCE"', script)
        self.assertIn('CODEX_PLUGIN_SELECTOR="apple-mail@${CODEX_MARKETPLACE_NAME}"', script)
        self.assertIn('codex plugin add "$CODEX_PLUGIN_SELECTOR"', script)
        self.assertIn(
            'codex plugin list --marketplace Agentic-Assets | grep -F "apple-mail@Agentic-Assets"',
            script,
        )
        self.assertIn("codex mcp get apple-mail --json", script)
        self.assertIn("tools/probes/mcp_tool_smoke.py", script)
        self.assertIn("--reject-literal '${CLAUDE_PLUGIN_ROOT}'", script)
        # The expected tool count must be derived from @mcp.tool decorators,
        # not hardcoded, so this gate stays correct as tools are added.
        self.assertIn("EXPECTED_TOOL_COUNT=", script)
        self.assertIn('--expect-count "$EXPECTED_TOOL_COUNT"', script)
        self.assertNotRegex(script, r"--expect-count\s+[0-9]+")
        for tool in (
            "reply_to_email",
            "compose_email",
            "manage_drafts",
            "list_accounts",
            "get_inbox_overview",
        ):
            self.assertIn(tool, script)

    def test_claude_plugin_contract_rejects_legacy_commands_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_dir = root / "plugin/.claude-plugin"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "plugin.json").write_text(
                json.dumps(
                    {
                        "name": "apple-mail",
                        "mcpServers": {
                            "apple-mail": {
                                "command": "/bin/bash",
                                "args": [
                                    "${CLAUDE_PLUGIN_ROOT}/start_mcp.sh",
                                    "--draft-safe",
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "plugin/commands").mkdir(parents=True)

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_plugin_manifest_contract(errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(
            errors,
            ["plugin/commands: legacy slash commands are retired; ship skills only"],
        )

    def test_developer_only_skills_are_not_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in (
                ".agents/skills/mail-scripting-dictionary",
                ".claude/skills",
                "plugin/.codex-plugin",
                "plugin/.claude-plugin",
                "plugin/skills",
                ".agents/plugins",
                ".claude-plugin",
            ):
                (root / rel).mkdir(parents=True)
            (root / "plugin/.mcp.json").write_text("{}", encoding="utf-8")

            manifests = {
                "plugin/.codex-plugin/plugin.json": {"skills": "../.agents/skills"},
                "plugin/.claude-plugin/plugin.json": {"skills": "./skills"},
                ".agents/plugins/marketplace.json": {"plugins": [{"source": "./plugin"}]},
                ".claude-plugin/marketplace.json": {"plugins": [{"source": "./plugin"}]},
            }
            for path, payload in manifests.items():
                (root / path).write_text(json.dumps(payload), encoding="utf-8")

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_developer_only_skills_not_packaged(errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(
            errors,
            [
                "plugin/.codex-plugin/plugin.json skills: must not reference repo-local developer skills "
                "(.agents/skills or .claude/skills)"
            ],
        )

    def test_server_json_contract_rejects_package_install_drift(self):
        server_json = {
            "$schema": "bad",
            "version": "1.0.0",
            "packages": [
                {
                    "registryType": "npm",
                    "identifier": "wrong-package",
                    "version": "2.0.0",
                    "transport": {"type": "http"},
                }
            ],
        }
        errors = []

        validate_manifests._check_server_json_contract(
            server_json,
            expected_version="1.0.0",
            project_name="mcp-apple-mail",
            errors=errors,
        )

        self.assertEqual(
            errors,
            [
                "server.json $schema: expected https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
                "server.json packages[0].registryType: expected pypi",
                "server.json packages[0].identifier: got 'wrong-package', expected 'mcp-apple-mail'",
                "server.json packages[0].version: got '2.0.0', expected '1.0.0'",
                "server.json packages[0].transport.type: expected stdio",
            ],
        )

    def test_python_package_contract_requires_runtime_dependency_and_ui_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                """
[project]
name = "mcp-apple-mail"
dependencies = [
    "fastmcp>=3.1.0,<4",
]

[tool.hatch.build.targets.wheel]
packages = ["plugin/apple_mail_mcp"]
""",
                encoding="utf-8",
            )
            plugin = root / "plugin"
            plugin.mkdir()
            (plugin / "requirements.txt").write_text(
                "fastmcp>=3.1.0,<4\nmcp-ui-server==1.0.0\n",
                encoding="utf-8",
            )

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_python_package_contract(errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(
            errors,
            [
                "pyproject.toml dependencies: missing runtime dependency mcp-ui-server from plugin/requirements.txt",
                "pyproject.toml wheel packages: missing plugin/ui for inbox_dashboard UI runtime",
            ],
        )

    def test_source_syntax_rejects_broken_startup_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugin"
            package = plugin / "apple_mail_mcp"
            package.mkdir(parents=True)
            (plugin / "start_mcp.sh").write_text("if true; then\n", encoding="utf-8")
            (plugin / "apple_mail_mcp.py").write_text("def broken(:\n", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = root
            try:
                validate_manifests._check_source_syntax(errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertTrue(
            any(err.startswith("plugin/start_mcp.sh: shell syntax error:") for err in errors),
            errors,
        )
        self.assertTrue(
            any(err.startswith("plugin/apple_mail_mcp.py: python syntax error:") for err in errors),
            errors,
        )

    def test_compare_zip_members_skips_absent_archive_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("current", encoding="utf-8")
            errors = []

            validate_manifests._compare_zip_members(
                Path(tmp) / "missing.zip",
                [(source, "payload/source.txt")],
                "missing.zip",
                errors,
            )

        self.assertEqual(errors, [])

    def test_compare_zip_members_can_require_absent_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("current", encoding="utf-8")
            errors = []

            validate_manifests._compare_zip_members(
                Path(tmp) / "missing.zip",
                [(source, "payload/source.txt")],
                "missing.zip",
                errors,
                require_present=True,
            )

        self.assertEqual(
            errors,
            ["missing.zip: missing archive; rebuild missing.zip"],
        )

    def test_check_no_directory_entries_flags_bare_directory_members(self):
        # Regression: raw `zip -r .` emits zero-byte entries whose names end
        # in `/`. `mcpb unpack` (and Claude Desktop's installer) treats those
        # as files and aborts with ENOENT. The MCPB must be built via
        # `mcpb pack`. See apple-mail-mcpb/build-mcpb.sh.
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.mcpb"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("ui/", b"")
                zf.writestr("ui/__init__.py", b"# real file")
                zf.writestr("apple_mail_mcp/", b"")

            errors = []
            validate_manifests._check_no_directory_entries(archive, archive.name, errors)

        self.assertEqual(len(errors), 1)
        msg = errors[0]
        self.assertIn("contains 2 directory entries", msg)
        self.assertIn("ui/", msg)
        self.assertIn("apple_mail_mcp/", msg)
        self.assertIn("mcpb pack", msg)

    def test_check_no_directory_entries_passes_on_clean_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "good.mcpb"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("ui/__init__.py", b"# real file")
                zf.writestr("manifest.json", b"{}")

            errors = []
            validate_manifests._check_no_directory_entries(archive, archive.name, errors)

        self.assertEqual(errors, [])

    def test_check_no_directory_entries_skips_absent_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            errors = []
            validate_manifests._check_no_directory_entries(Path(tmp) / "missing.mcpb", "missing.mcpb", errors)
        self.assertEqual(errors, [])

    def test_plugin_zip_has_no_directory_entries(self):
        # Regression: zero-byte directory entries (names ending in `/`) broke
        # Cowork's plugin uploader the same way they broke the MCPB
        # extractor. Build script uses `zip -D` to suppress them.
        archive = ROOT / "apple-mail-plugin.zip"
        if not archive.exists():
            self.skipTest("apple-mail-plugin.zip not built; run tools/gates/build-artifacts.sh")
        import zipfile as _zf

        with _zf.ZipFile(archive) as zf:
            offenders = [n for n in zf.namelist() if n.endswith("/")]
        self.assertEqual(
            offenders,
            [],
            msg=(
                f"plugin zip must contain no bare directory entries "
                f"(found {len(offenders)}: {offenders[:3]}); "
                f"rebuild with tools/gates/build-artifacts.sh (uses `zip -D`)"
            ),
        )

    def test_artifact_freshness_rejects_plugin_zip_directory_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugin"
            plugin_root.mkdir()
            (plugin_root / "start_mcp.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            archive = tmp_path / "apple-mail-plugin.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("start_mcp.sh", "#!/bin/sh\n")
                zf.writestr("skills/", b"")

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = tmp_path
            try:
                validate_manifests._check_artifact_freshness("1.0.0", errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(len(errors), 1)
        self.assertIn("apple-mail-plugin.zip: contains 1 directory entry", errors[0])
        self.assertIn("skills/", errors[0])

    def test_artifact_freshness_rejects_forbidden_plugin_payload_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugin"
            plugin_root.mkdir()
            (plugin_root / "start_mcp.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (plugin_root / ".env").write_text("SECRET=value\n", encoding="utf-8")
            archive = tmp_path / "apple-mail-plugin.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("start_mcp.sh", "#!/bin/sh\n")
                zf.writestr(".env", "SECRET=value\n")

            errors = []
            original_root = validate_manifests.ROOT
            validate_manifests.ROOT = tmp_path
            try:
                validate_manifests._check_artifact_freshness("1.0.0", errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertIn(
            "apple-mail-plugin.zip: unexpected .env; rebuild apple-mail-plugin.zip",
            errors,
        )

    def test_plugin_zip_has_manifest_at_root_not_nested(self):
        # Regression: Cowork (and `claude plugin validate`) look for
        # .claude-plugin/plugin.json at the unzip root. If the zip wraps
        # everything in a `plugin/` prefix, validation fails with
        # "No manifest found in directory". Always zip from inside plugin/.
        archive = ROOT / "apple-mail-plugin.zip"
        if not archive.exists():
            self.skipTest("apple-mail-plugin.zip not built; run tools/gates/build-artifacts.sh")
        import zipfile as _zf

        with _zf.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertIn(
            ".claude-plugin/plugin.json",
            names,
            msg=(
                "plugin.json must be at zip root for Cowork uploads. "
                "Rebuild with tools/gates/build-artifacts.sh (zips from inside plugin/)."
            ),
        )
        nested = [n for n in names if n.startswith("plugin/")]
        self.assertEqual(
            nested,
            [],
            msg=f"zip must not wrap files under plugin/ — found {len(nested)} such entries",
        )

    def test_plugin_file_parity_passes_when_bytes_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = b"PK\x03\x04 fake-zip-bytes for parity test"
            (tmp_path / "apple-mail-plugin.zip").write_bytes(payload)
            (tmp_path / "apple-mail.plugin").write_bytes(payload)

            errors: list[str] = []
            validate_manifests._check_plugin_file_parity(tmp_path, errors, require_present=True)

        self.assertEqual(errors, [])

    def test_plugin_file_parity_rejects_byte_divergence(self):
        # Regression: silently shipping `.zip` and `.plugin` with different
        # bytes confuses installers and breaks reproducibility — the .plugin
        # must always be a byte-identical copy of the .zip artifact.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "apple-mail-plugin.zip").write_bytes(b"zip-bytes")
            (tmp_path / "apple-mail.plugin").write_bytes(b"diverged-bytes")

            errors: list[str] = []
            validate_manifests._check_plugin_file_parity(tmp_path, errors, require_present=True)

        self.assertEqual(len(errors), 1)
        self.assertIn("bytes diverge", errors[0])
        self.assertIn("byte-identical", errors[0])

    def test_plugin_file_parity_requires_artifact_when_flagged(self):
        # Regression: shipping a release without `.plugin` would silently
        # break the Cowork upload path. The release gate must reject this.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "apple-mail-plugin.zip").write_bytes(b"zip-bytes")

            errors: list[str] = []
            validate_manifests._check_plugin_file_parity(tmp_path, errors, require_present=True)

        self.assertEqual(len(errors), 1)
        self.assertIn("apple-mail.plugin: missing artifact", errors[0])
        self.assertIn("Cowork upload", errors[0])

    def test_plugin_file_parity_skips_when_absent_and_optional(self):
        # Default (non-release) developer runs should not fail when only
        # the zip has been built — only `APPLE_MAIL_REQUIRE_DIST_ARTIFACTS`
        # promotes a missing .plugin to a hard error.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "apple-mail-plugin.zip").write_bytes(b"zip-bytes")

            errors: list[str] = []
            validate_manifests._check_plugin_file_parity(tmp_path, errors, require_present=False)

        self.assertEqual(errors, [])

    def test_plugin_file_parity_flags_orphan_plugin_without_zip(self):
        # If somebody manually drops a .plugin file without the .zip, the
        # build is inconsistent — both artifacts ship from the same build
        # step and one without the other is broken state.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "apple-mail.plugin").write_bytes(b"plugin-bytes")

            errors: list[str] = []
            validate_manifests._check_plugin_file_parity(tmp_path, errors, require_present=False)

        self.assertEqual(len(errors), 1)
        self.assertIn(
            "apple-mail-plugin.zip is missing",
            errors[0],
        )

    def test_plugin_file_artifact_matches_zip_in_repo(self):
        # Smoke test against the actually-built artifacts. The byte parity
        # is enforced inside the validator, but tying the test to the
        # on-disk file gives a clearer failure message when a build forgets
        # to update `.plugin` after a zip-only rebuild.
        zip_path = ROOT / "apple-mail-plugin.zip"
        plugin_path = ROOT / "apple-mail.plugin"
        if not zip_path.exists() or not plugin_path.exists():
            self.skipTest("Run tools/gates/build-artifacts.sh to produce both artifacts")
        self.assertEqual(
            plugin_path.read_bytes(),
            zip_path.read_bytes(),
            msg=(
                "apple-mail.plugin must be a byte-identical copy of "
                "apple-mail-plugin.zip — rebuild with tools/gates/build-artifacts.sh"
            ),
        )

    def test_no_stale_distribution_artifacts_flags_old_mcpb(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "apple-mail-mcp-v3.5.0.mcpb").write_bytes(b"stale")
            (tmp_path / "apple-mail-mcp-v3.6.1.mcpb").write_bytes(b"current")

            errors: list[str] = []
            original_root = validate_manifests.ROOT
            try:
                validate_manifests.ROOT = tmp_path
                validate_manifests._check_no_stale_distribution_artifacts("3.6.1", errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(len(errors), 1)
        self.assertIn("stale distribution artifact: apple-mail-mcp-v3.5.0.mcpb", errors[0])
        self.assertIn("tools/gates/build-artifacts.sh", errors[0])

    def test_no_stale_distribution_artifacts_passes_when_only_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "apple-mail-mcp-v3.6.1.mcpb").write_bytes(b"current")

            errors: list[str] = []
            original_root = validate_manifests.ROOT
            try:
                validate_manifests.ROOT = tmp_path
                validate_manifests._check_no_stale_distribution_artifacts("3.6.1", errors)
            finally:
                validate_manifests.ROOT = original_root

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
