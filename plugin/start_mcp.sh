#!/bin/bash

# Startup wrapper for Apple Mail MCP.
#
# Ensures a HEALTHY virtual environment on the user's machine before launching
# the server. The venv is self-healing: if a previous venv was built against a
# Python interpreter that has since been removed or upgraded (for example
# Homebrew dropping a minor version like python@3.13), the venv's interpreter
# symlink dangles and the server can no longer start. This script detects that
# case by actually executing the interpreter, then rebuilds the venv from
# scratch. This keeps the MCP working across machines and across Python
# upgrades with no manual intervention.
#
# Flags consumed by this wrapper (everything else passes through to the server):
#   --ensure-only | --check | --doctor
#       Build/repair the venv and verify imports, then exit 0 without launching
#       the server. Used by installers and health checks to pre-warm the venv.

set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_DIR="${SCRIPT_DIR}/venv"
REQUIREMENTS="${SCRIPT_DIR}/requirements.txt"
PYTHON_SCRIPT="${SCRIPT_DIR}/apple_mail_mcp.py"

# Function to log to stderr (visible in Claude Desktop / Claude Code logs)
log_error() {
    echo "[Apple Mail MCP] $1" >&2
}

# Separate our own flags from arguments meant for the server.
ENSURE_ONLY=0
SERVER_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --ensure-only|--check|--doctor) ENSURE_ONLY=1 ;;
        *) SERVER_ARGS+=("$arg") ;;
    esac
done

# Find a usable interpreter to BUILD the venv with. Prefer specific, well
# supported minor versions (best wheel coverage) over the generic "python3",
# which may point at a brand-new release that lacks prebuilt wheels.
find_python() {
    for candidate in python3.12 python3.13 python3.11 python3.10 python3; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            version="$("${candidate}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || continue
            major="${version%%.*}"
            minor="${version#*.}"
            if [ "${major}" -gt 3 ] || { [ "${major}" -eq 3 ] && [ "${minor}" -ge 10 ]; }; then
                command -v "${candidate}"
                return 0
            fi
        fi
    done
    return 1
}

# A venv is healthy only if its interpreter EXECUTES. A dangling symlink (the
# Python it was built against was removed/upgraded) fails both the -x test and
# the exec probe, which is exactly the rot this script repairs.
venv_python_ok() {
    [ -x "${VENV_DIR}/bin/python3" ] || return 1
    "${VENV_DIR}/bin/python3" -c 'import sys' >/dev/null 2>&1
}

fastmcp_import_ok() {
    "${VENV_DIR}/bin/python3" -c "import fastmcp" >/dev/null 2>&1
}

create_venv() {
    local python_bin
    python_bin="$(find_python || true)"
    if [ -z "${python_bin}" ]; then
        log_error "ERROR: Python 3.10+ not found. Install Python 3.12 (e.g. 'brew install python@3.12')."
        exit 1
    fi

    log_error "Creating virtual environment with ${python_bin}..."
    "${python_bin}" -m venv "${VENV_DIR}" 2>&1 | while IFS= read -r line; do log_error "$line"; done

    log_error "Upgrading pip and installing dependencies..."
    "${VENV_DIR}/bin/python3" -m pip install --quiet --upgrade pip 2>&1 | while IFS= read -r line; do log_error "$line"; done
    "${VENV_DIR}/bin/python3" -m pip install --quiet -r "${REQUIREMENTS}" 2>&1 | while IFS= read -r line; do log_error "$line"; done
}

# Guarantee a healthy venv with importable dependencies, rebuilding as needed.
ensure_venv() {
    if ! venv_python_ok; then
        if [ -e "${VENV_DIR}" ]; then
            log_error "Virtualenv interpreter missing or broken (Python removed/upgraded?); rebuilding from scratch..."
            rm -rf "${VENV_DIR}"
        else
            log_error "Virtual environment not found. Creating on first run..."
        fi
        create_venv
    fi

    # Interpreter is fine but dependencies may be missing/stale: one repair pass.
    if ! fastmcp_import_ok; then
        log_error "fastmcp not importable; reinstalling dependencies once..."
        "${VENV_DIR}/bin/python3" -m pip install --quiet -r "${REQUIREMENTS}" 2>&1 | while IFS= read -r line; do log_error "$line"; done
    fi

    if ! fastmcp_import_ok; then
        log_error "ERROR: fastmcp is still not importable after reinstall. Remove ${VENV_DIR} and restart; check requirements.txt and network access."
        exit 1
    fi
}

ensure_venv

if [ "${ENSURE_ONLY}" -eq 1 ]; then
    log_error "venv healthy: $("${VENV_DIR}/bin/python3" --version 2>&1) at ${VENV_DIR}"
    exit 0
fi

# Run the Python MCP server
exec "${VENV_DIR}/bin/python3" "${PYTHON_SCRIPT}" "${SERVER_ARGS[@]}"
