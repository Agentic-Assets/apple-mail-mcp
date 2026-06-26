"""Tests for the consolidated local plugin refresh script."""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "refresh-local-plugins.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_refresh_local_plugins_installs_cli_claude_and_codex_with_fake_commands(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    cli_venv = tmp_path / "cli-venv"
    user_bin = tmp_path / "user-bin"
    user_bin.mkdir()
    (user_bin / "apple-mail").symlink_to(cli_venv / "bin" / "apple-mail")
    log = tmp_path / "calls.log"

    _write_executable(
        fake_bin / "git",
        f"""
        #!/usr/bin/env bash
        echo "git $*" >> {log}
        if [[ "$*" == "rev-parse --abbrev-ref --symbolic-full-name @{{u}}" ]]; then
          echo "origin/main"
          exit 0
        fi
        [[ "$*" == "pull --ff-only" ]]
        """,
    )
    _write_executable(
        fake_bin / "python3",
        f"""
        #!/usr/bin/env bash
        echo "python3 $*" >> {log}
        if [[ "${{1:-}}" == "-c" ]]; then
          echo "3.7.1"
          exit 0
        fi
        if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
          mkdir -p "${{3}}/bin"
          cat > "${{3}}/bin/python" <<'SH'
        #!/usr/bin/env bash
        echo "venv-python $*" >> "{log}"
        if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "pip" ]]; then
          if [[ "${{*: -1}}" == "{ROOT}" ]]; then
            dir="$(cd "$(dirname "$0")" && pwd)"
            cat > "$dir/apple-mail" <<'CLI'
        #!/usr/bin/env bash
        echo "apple-mail 3.7.1"
        CLI
            cat > "$dir/mcp-apple-mail" <<'CLI'
        #!/usr/bin/env bash
        echo "mcp-apple-mail help"
        CLI
            chmod +x "$dir/apple-mail" "$dir/mcp-apple-mail"
          fi
          exit 0
        fi
        exit 1
        SH
          chmod +x "${{3}}/bin/python"
          exit 0
        fi
        if [[ "$#" -eq 0 || "${{1:-}}" == "-" ]]; then
          echo "{tmp_path}/unused-python-user-bin"
          exit 0
        fi
        echo "unexpected python3 invocation: $*" >&2
        exit 1
        """,
    )
    _write_executable(
        fake_bin / "codex",
        f"""
        #!/usr/bin/env bash
        echo "codex $*" >> {log}
        if [[ "$*" == "plugin list --marketplace apple-mail-mcp" ]]; then
          echo "apple-mail@apple-mail-mcp installed, enabled 3.7.1"
        elif [[ "$*" == "mcp get apple-mail --json" ]]; then
          echo '{{"command":"/bin/bash","args":["./start_mcp.sh","--draft-safe"]}}'
        fi
        """,
    )
    _write_executable(
        fake_bin / "claude",
        f"""
        #!/usr/bin/env bash
        echo "claude $*" >> {log}
        if [[ "$*" == "plugin details apple-mail@apple-mail-mcp" ]]; then
          echo "apple-mail 3.7.1"
        fi
        """,
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "APPLE_MAIL_MCP_CLI_VENV": str(cli_venv),
        "APPLE_MAIL_MCP_USER_BIN": str(user_bin),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    calls = log.read_text(encoding="utf-8")
    assert "git pull --ff-only" in calls
    assert f"python3 -m venv {cli_venv}" in calls
    assert "venv-python -m pip install --upgrade pip" in calls
    assert f"venv-python -m pip install --upgrade {ROOT}" in calls
    assert not (user_bin / "apple-mail").exists()
    assert (user_bin / "mcp-apple-mail").is_symlink()
    assert "codex plugin marketplace add ./" in calls
    assert "codex plugin add apple-mail@apple-mail-mcp" in calls
    assert "codex mcp get apple-mail --json" in calls
    assert "claude plugin marketplace add ./ --scope user" in calls
    assert "claude plugin install apple-mail@apple-mail-mcp --scope user" in calls
    assert "claude plugin details apple-mail@apple-mail-mcp" in calls
