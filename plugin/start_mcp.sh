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
REQUIREMENTS_LOCK="${SCRIPT_DIR}/requirements.lock"
WHEELHOUSE="${SCRIPT_DIR}/wheelhouse"
LOCK_MARKER="${VENV_DIR}/.requirements.lock.sha256"
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

# This self-contained release carries hash-checked wheels for macOS arm64 and
# CPython 3.13. Other plugin channels may use their documented installation
# path, but this offline payload must fail closed rather than downloading.
find_python() {
    if command -v python3.13 >/dev/null 2>&1; then
        command -v python3.13
        return 0
    fi
    return 1
}

lock_sha256() {
    shasum -a 256 "${REQUIREMENTS_LOCK}" | awk '{print $1}'
}

offline_payload_ok() {
    [ -f "${REQUIREMENTS_LOCK}" ] && [ -d "${WHEELHOUSE}" ] && [ -n "$(find "${WHEELHOUSE}" -maxdepth 1 -name '*.whl' -print -quit)" ]
}

# A venv is healthy only if its interpreter EXECUTES. A dangling symlink (the
# Python it was built against was removed/upgraded) fails both the -x test and
# the exec probe, which is exactly the rot this script repairs.
venv_python_ok() {
    [ -x "${VENV_DIR}/bin/python3" ] || return 1
    "${VENV_DIR}/bin/python3" -c 'import sys' >/dev/null 2>&1
}

venv_matches_lock() {
    [ -f "${LOCK_MARKER}" ] && [ "$(cat "${LOCK_MARKER}")" = "$(lock_sha256)" ]
}

create_venv() {
    local python_bin
    python_bin="$(find_python || true)"
    if [ -z "${python_bin}" ]; then
        log_error "ERROR: This offline Apple Mail payload requires Python 3.13 on macOS arm64. Install it with 'brew install python@3.13'."
        exit 1
    fi

    if ! offline_payload_ok; then
        log_error "ERROR: Offline payload is incomplete (requirements.lock or wheelhouse missing). Reinstall the approved release."
        exit 1
    fi

    log_error "Creating virtual environment with ${python_bin}..."
    "${python_bin}" -m venv "${VENV_DIR}" 2>&1 | while IFS= read -r line; do log_error "$line"; done

    log_error "Installing hash-checked dependencies from the bundled wheelhouse..."
    "${VENV_DIR}/bin/python3" -m pip install --quiet --no-index --find-links "${WHEELHOUSE}" --require-hashes -r "${REQUIREMENTS_LOCK}" 2>&1 | while IFS= read -r line; do log_error "$line"; done
    lock_sha256 > "${LOCK_MARKER}"
}

# Guarantee a healthy venv with importable dependencies, rebuilding as needed.
ensure_venv() {
    if ! venv_python_ok || ! venv_matches_lock; then
        if [ -e "${VENV_DIR}" ]; then
            log_error "Virtualenv is missing, broken, or does not match the approved offline lock; rebuilding from scratch..."
            rm -rf "${VENV_DIR}"
        else
            log_error "Virtual environment not found. Creating on first run..."
        fi
        create_venv
    fi

    if ! "${VENV_DIR}/bin/python3" -c "import fastmcp" >/dev/null 2>&1; then
        log_error "ERROR: fastmcp is not importable from the bundled offline payload. Reinstall the approved release."
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
