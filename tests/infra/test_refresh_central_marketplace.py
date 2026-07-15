"""Behavioral checks for the non-destructive central marketplace refresher."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/gates/refresh-central-marketplace.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fake_clients(
    tmp_path: Path, *, collision: bool = False, claude_scopes: tuple[str, ...] = ("user",)
) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    source = (
        "https://github.com/other/repository.git"
        if collision
        else "https://github.com/Agentic-Assets/Agentic-Assets-Marketplace.git"
    )
    claude_plugins = json.dumps(
        [{"id": "apple-mail@agentic-assets", "scope": scope, "version": "3.11.6"} for scope in claude_scopes]
    )
    _write_executable(
        bin_dir / "claude",
        f'''#!/usr/bin/env bash
set -euo pipefail
printf 'claude %s\\n' "$*" >>"$FAKE_COMMAND_LOG"
if [[ "$*" == "plugin marketplace list --json" ]]; then
  printf '%s\\n' '[{{"name":"agentic-assets","repo":"{source}"}}]'
elif [[ "$*" == "plugin list --json" ]]; then
  if grep -q '^claude plugin install apple-mail@agentic-assets --scope user$' "$FAKE_COMMAND_LOG"; then
    printf '%s\\n' '[{{"id":"apple-mail@agentic-assets","scope":"user","version":"3.11.6"}}]'
  else
    printf '%s\\n' '{claude_plugins}'
  fi
fi
''',
    )
    _write_executable(
        bin_dir / "codex",
        f'''#!/usr/bin/env bash
set -euo pipefail
printf 'codex %s\\n' "$*" >>"$FAKE_COMMAND_LOG"
if [[ "$*" == "plugin marketplace list --json" ]]; then
  printf '%s\\n' '{{"marketplaces":[{{"name":"agentic-assets","marketplaceSource":{{"source":"{source}"}}}}]}}'
elif [[ "$*" == "plugin list --json" ]]; then
  printf '%s\\n' '{{"installed":[{{"pluginId":"apple-mail@agentic-assets","installed":true,"version":"3.11.6"}}]}}'
elif [[ "$*" == "mcp get apple-mail --json" ]]; then
  printf '%s\\n' '{{"transport":{{"command":"/usr/bin/true","args":[],"cwd":"/tmp"}}}}'
fi
''',
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_COMMAND_LOG"] = str(log)
    return env, log


def _run(
    tmp_path: Path,
    *args: str,
    collision: bool = False,
    claude_scopes: tuple[str, ...] = ("user",),
) -> tuple[subprocess.CompletedProcess[str], str]:
    env, log = _fake_clients(tmp_path, collision=collision, claude_scopes=claude_scopes)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    return result, log.read_text(encoding="utf-8")


def test_check_mode_is_read_only(tmp_path: Path) -> None:
    result, commands = _run(tmp_path, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "marketplace list --json" in commands
    for forbidden in (
        " marketplace add ",
        " marketplace update ",
        " marketplace upgrade ",
        " plugin add ",
        " plugin install ",
        " plugin remove ",
        " marketplace remove ",
    ):
        assert forbidden not in commands


def test_unknown_central_source_fails_before_mutation(tmp_path: Path) -> None:
    result, commands = _run(tmp_path, collision=True)
    assert result.returncode != 0
    assert "No marketplace or plugin registrations were changed" in result.stderr
    assert " plugin add " not in commands
    assert " plugin install " not in commands
    assert " marketplace remove " not in commands


def test_apply_refreshes_only_central_selector(tmp_path: Path) -> None:
    result, commands = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "claude plugin marketplace update agentic-assets" in commands
    assert "claude plugin update apple-mail@agentic-assets --scope user" in commands
    assert "codex plugin marketplace upgrade agentic-assets" in commands
    assert "codex plugin add apple-mail@agentic-assets" in commands
    assert "apple-mail-mcp" not in commands
    assert " marketplace remove " not in commands
    assert " plugin remove " not in commands


def test_apply_updates_the_single_existing_project_scope(tmp_path: Path) -> None:
    result, commands = _run(tmp_path, claude_scopes=("project",))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "claude plugin update apple-mail@agentic-assets --scope project" in commands
    assert "claude plugin install apple-mail@agentic-assets" not in commands


def test_apply_updates_the_single_existing_local_scope(tmp_path: Path) -> None:
    result, commands = _run(tmp_path, claude_scopes=("local",))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "claude plugin update apple-mail@agentic-assets --scope local" in commands
    assert "claude plugin install apple-mail@agentic-assets" not in commands


def test_apply_installs_user_scope_only_when_selector_is_absent(tmp_path: Path) -> None:
    result, commands = _run(tmp_path, claude_scopes=())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "claude plugin install apple-mail@agentic-assets --scope user" in commands
    assert "claude plugin update apple-mail@agentic-assets" not in commands


def test_duplicate_claude_scopes_fail_before_mutation(tmp_path: Path) -> None:
    result, commands = _run(tmp_path, claude_scopes=("user", "project"))
    assert result.returncode != 0
    assert "installed more than once across scopes" in result.stderr
    assert " plugin update apple-mail@agentic-assets" not in commands
    assert " plugin install apple-mail@agentic-assets" not in commands
    assert " marketplace update " not in commands


def test_identity_contract_names_the_same_central_selector_for_both_clients() -> None:
    identity = json.loads((ROOT / "tools/marketplace_identity.json").read_text(encoding="utf-8"))
    central = identity["primary_marketplace"]
    assert central["id"] == "agentic-assets"
    assert central["selector"] == "apple-mail@agentic-assets"
