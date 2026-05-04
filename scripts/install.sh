#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [options]

Install Agent Memory locally without manual venv activation.

Options:
  --vault PATH              Initialize/use this Agent Memory vault.
  --install-dir PATH        Install managed venv and metadata here.
                            Default: ~/.local/share/agent-memory
  --bin-dir PATH            Install wrapper commands here.
                            Default: ~/.local/bin
  --python PATH             Python interpreter to use.
                            Default: first python3.12/3.11/3.10/python3
  --no-venv                 Install into the current Python environment.
  --skip-install            Do not run pip install, only generate wrappers.
  --with-test               Install test extra too: .[test].
  --force                   Overwrite existing wrapper commands.
  --dry-run                 Print actions without changing files.
  -h, --help                Show this help.

Environment:
  AGENT_MEMORY_VAULT        Default vault path when --vault is omitted.
  AGENT_MEMORY_INSTALL_DIR  Default install directory.
  AGENT_MEMORY_BIN_DIR      Default wrapper directory.
USAGE
}

log() {
  printf '%s\n' "==> $*"
}

warn() {
  printf '%s\n' "warning: $*" >&2
}

fail() {
  printf '%s\n' "error: $*" >&2
  exit 1
}

expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

abs_path() {
  local path
  path="$(expand_path "$1")"
  if [ -d "$path" ]; then
    (cd "$path" && pwd -P)
  elif [ "${path#/}" != "$path" ]; then
    printf '%s\n' "$path"
  else
    printf '%s/%s\n' "$(pwd -P)" "$path"
  fi
}

write_file() {
  local path="$1"
  local mode="$2"
  local content="$3"

  if [ "$DRY_RUN" = "1" ]; then
    log "would write $path"
    return
  fi

  if [ -e "$path" ] && [ "$FORCE" != "1" ]; then
    fail "$path already exists; rerun with --force to overwrite"
  fi

  mkdir -p "$(dirname "$path")"
  printf '%s' "$content" > "$path"
  chmod "$mode" "$path"
}

run_cmd() {
  if [ "$DRY_RUN" = "1" ]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
    return
  fi
  "$@"
}

python_ok() {
  local python_bin="$1"
  command -v "$python_bin" >/dev/null 2>&1 || return 1
  "$python_bin" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

python_version() {
  local python_bin="$1"
  "$python_bin" - <<'PY'
import sys
print(".".join(str(part) for part in sys.version_info[:3]))
PY
}

select_python() {
  local requested="$1"
  if [ -n "$requested" ]; then
    if python_ok "$requested"; then
      command -v "$requested"
      return 0
    fi
    fail "Python >= 3.10 is required, but $requested is Python $(python_version "$requested" 2>/dev/null || printf 'unknown'). Install Python 3.10+ or pass --python /path/to/python3.10."
  fi

  local candidate
  for candidate in python3.12 python3.11 python3.10 python3; do
    if python_ok "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done

  fail "Python >= 3.10 is required for local installation. Install Python 3.10+ (for example with Homebrew: brew install python@3.12) or rerun with --python /path/to/python3.10."
}

detect_platform() {
  case "$(uname -s)" in
    Darwin) printf '%s\n' "macos" ;;
    Linux) printf '%s\n' "linux" ;;
    *) fail "unsupported platform: $(uname -s). This installer supports macOS and Linux." ;;
  esac
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PLATFORM="$(detect_platform)"

INSTALL_DIR="${AGENT_MEMORY_INSTALL_DIR:-$HOME/.local/share/agent-memory}"
BIN_DIR="${AGENT_MEMORY_BIN_DIR:-$HOME/.local/bin}"
PYTHON_BIN="${PYTHON:-}"
VAULT_PATH="${AGENT_MEMORY_VAULT:-}"
USE_VENV=1
SKIP_INSTALL=0
WITH_TEST=0
FORCE=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vault)
      [ "$#" -ge 2 ] || fail "--vault requires a path"
      VAULT_PATH="$2"
      shift 2
      ;;
    --install-dir)
      [ "$#" -ge 2 ] || fail "--install-dir requires a path"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      [ "$#" -ge 2 ] || fail "--bin-dir requires a path"
      BIN_DIR="$2"
      shift 2
      ;;
    --python)
      [ "$#" -ge 2 ] || fail "--python requires a path"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --no-venv)
      USE_VENV=0
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --with-test)
      WITH_TEST=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

INSTALL_DIR="$(abs_path "$INSTALL_DIR")"
BIN_DIR="$(abs_path "$BIN_DIR")"
if [ -n "$VAULT_PATH" ]; then
  VAULT_PATH="$(abs_path "$VAULT_PATH")"
fi

if [ "$DRY_RUN" = "1" ] && [ "$SKIP_INSTALL" = "1" ] && [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || printf '%s\n' python3)"
else
  PYTHON_BIN="$(select_python "$PYTHON_BIN")"
fi
VENV_DIR="$INSTALL_DIR/venv"
if [ "$USE_VENV" = "1" ]; then
  PYTHON_CMD="$VENV_DIR/bin/python"
else
  PYTHON_CMD="$PYTHON_BIN"
fi

log "platform: $PLATFORM"
log "repo: $REPO_ROOT"
log "python: $PYTHON_BIN ($(python_version "$PYTHON_BIN"))"
log "install dir: $INSTALL_DIR"
log "bin dir: $BIN_DIR"

if [ "$SKIP_INSTALL" != "1" ]; then
  if [ "$USE_VENV" = "1" ]; then
    if [ -x "$VENV_DIR/bin/python" ] && ! python_ok "$VENV_DIR/bin/python"; then
      log "existing managed venv uses Python $(python_version "$VENV_DIR/bin/python"); recreating with Python $(python_version "$PYTHON_BIN")"
      run_cmd rm -rf "$VENV_DIR"
    fi
    if [ ! -x "$VENV_DIR/bin/python" ]; then
      log "creating managed virtual environment"
      run_cmd "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    if ! python_ok "$PYTHON_CMD"; then
      fail "managed venv Python is $(python_version "$PYTHON_CMD"), but Agent Memory requires Python >= 3.10"
    fi
    log "upgrading pip"
    run_cmd "$PYTHON_CMD" -m pip install -U pip
  elif ! python_ok "$PYTHON_CMD"; then
    fail "Python >= 3.10 is required, but $PYTHON_CMD is Python $(python_version "$PYTHON_CMD")"
  fi

  EXTRA_SUFFIX=""
  if [ "$WITH_TEST" = "1" ]; then
    EXTRA_SUFFIX="[test]"
  fi
  log "installing Agent Memory ${EXTRA_SUFFIX:-package}"
  run_cmd "$PYTHON_CMD" -m pip install -e "$REPO_ROOT$EXTRA_SUFFIX"
fi

MEMORY_WRAPPER='#!/usr/bin/env bash
set -euo pipefail
__DEFAULT_VAULT_EXPORT__
export AGENT_MEMORY_INSTALL_DIR="${AGENT_MEMORY_INSTALL_DIR:-__INSTALL_DIR__}"
if [ -n "${AGENT_MEMORY_DEFAULT_VAULT:-}" ] && [ -z "${AGENT_MEMORY_VAULT:-}" ]; then
  export AGENT_MEMORY_VAULT="$AGENT_MEMORY_DEFAULT_VAULT"
fi
exec "__PYTHON_CMD__" -m agent_memory.cli "$@"
'

SERVICE_WRAPPER='#!/usr/bin/env bash
set -euo pipefail
__DEFAULT_VAULT_EXPORT__
export AGENT_MEMORY_INSTALL_DIR="${AGENT_MEMORY_INSTALL_DIR:-__INSTALL_DIR__}"
export AGENT_MEMORY_BIN_DIR="${AGENT_MEMORY_BIN_DIR:-__BIN_DIR__}"
if [ -n "${AGENT_MEMORY_DEFAULT_VAULT:-}" ] && [ -z "${AGENT_MEMORY_VAULT:-}" ]; then
  export AGENT_MEMORY_VAULT="$AGENT_MEMORY_DEFAULT_VAULT"
fi
exec "__REPO_ROOT__/scripts/agent-memory-service.sh" "$@"
'

MEMORY_WRAPPER="${MEMORY_WRAPPER//__PYTHON_CMD__/$PYTHON_CMD}"
MEMORY_WRAPPER="${MEMORY_WRAPPER//__INSTALL_DIR__/$INSTALL_DIR}"
SERVICE_WRAPPER="${SERVICE_WRAPPER//__INSTALL_DIR__/$INSTALL_DIR}"
SERVICE_WRAPPER="${SERVICE_WRAPPER//__BIN_DIR__/$BIN_DIR}"
SERVICE_WRAPPER="${SERVICE_WRAPPER//__REPO_ROOT__/$REPO_ROOT}"

if [ -n "$VAULT_PATH" ]; then
  DEFAULT_VAULT_EXPORT="export AGENT_MEMORY_DEFAULT_VAULT=\"$VAULT_PATH\""
else
  DEFAULT_VAULT_EXPORT=":"
fi
MEMORY_WRAPPER="${MEMORY_WRAPPER//__DEFAULT_VAULT_EXPORT__/$DEFAULT_VAULT_EXPORT}"
SERVICE_WRAPPER="${SERVICE_WRAPPER//__DEFAULT_VAULT_EXPORT__/$DEFAULT_VAULT_EXPORT}"

log "installing wrapper commands"
write_file "$BIN_DIR/memory" 0755 "$MEMORY_WRAPPER"
write_file "$BIN_DIR/agent-memory-service" 0755 "$SERVICE_WRAPPER"

if [ -n "$VAULT_PATH" ]; then
  log "initializing vault: $VAULT_PATH"
  run_cmd "$BIN_DIR/memory" init "$VAULT_PATH" --json >/dev/null
fi

cat <<EOF

Agent Memory installed.

Add this directory to PATH if needed:
  export PATH="$BIN_DIR:\$PATH"

CLI:
  memory status
  memory reindex --clean
  memory agent commands --client all
  agent-memory-service install
  agent-memory-service start
  agent-memory-service status

Notes:
  - Use generated agent instructions and CLI JSON commands for coding-agent integrations.
  - The background service is for local health and maintenance hooks.
EOF
