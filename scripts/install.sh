#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [options]

Install Memora locally without manual venv activation.

Options:
  --vault PATH              Initialize/use this Memora vault and store it as
                            the wrapper default.
  --no-vault                Do not prompt for or configure a default vault.
  --install-dir PATH        Install managed venv and metadata here.
                            Default: ~/.local/share/memora
  --bin-dir PATH            Install wrapper commands here.
                            Default: ~/.local/bin
  --python PATH             Python interpreter to use.
                            Default: first python3.12/3.11/3.10/python3
  --no-venv                 Install into the current Python environment.
  --skip-install            Do not install the package, only generate wrappers.
  --with-test               Install test extra too: .[test].
  --force                   Overwrite existing wrapper commands.
  --dry-run                 Print actions without changing files.
  -h, --help                Show this help.

Environment:
  MEMORA_VAULT        Default vault path when --vault is omitted.
  MEMORA_INSTALL_DIR  Default install directory.
  MEMORA_BIN_DIR      Default wrapper directory.
  UV                  Optional uv executable to prefer when available.
                      Default: uv.
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

is_interactive() {
  [ -t 0 ] && [ -t 1 ]
}

prompt_for_vault() {
  local default_display="~/MemoryVault"
  local reply

  printf '%s\n' ""
  printf '%s\n' "Choose a default Memora vault."
  printf '%s\n' "Press Enter for $default_display, or type 'skip' to configure it later."
  printf 'Vault path [%s]: ' "$default_display"
  IFS= read -r reply

  case "$reply" in
    "")
      VAULT_PATH="$default_display"
      ;;
    skip|SKIP|none|NONE|no|NO)
      VAULT_PATH=""
      ;;
    *)
      VAULT_PATH="$reply"
      ;;
  esac
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

uv_ok() {
  command -v "$1" >/dev/null 2>&1
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

INSTALL_DIR="${MEMORA_INSTALL_DIR:-$HOME/.local/share/memora}"
BIN_DIR="${MEMORA_BIN_DIR:-$HOME/.local/bin}"
PYTHON_BIN="${PYTHON:-}"
UV_BIN="${UV:-uv}"
USE_UV=0
VAULT_PATH="${MEMORA_VAULT:-}"
NO_VAULT=0
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
    --no-vault)
      NO_VAULT=1
      VAULT_PATH=""
      shift
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

if [ -z "$VAULT_PATH" ] && [ "$NO_VAULT" != "1" ] && is_interactive; then
  prompt_for_vault
fi

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
if [ "$SKIP_INSTALL" != "1" ]; then
  if uv_ok "$UV_BIN"; then
    USE_UV=1
    log "installer: uv ($UV_BIN)"
  else
    warn "uv not found; falling back to Python venv and pip"
    log "installer: pip fallback"
  fi
fi
log "install dir: $INSTALL_DIR"
log "bin dir: $BIN_DIR"
if [ -n "$VAULT_PATH" ]; then
  log "default vault: $VAULT_PATH"
else
  log "default vault: not configured"
fi

if [ "$SKIP_INSTALL" != "1" ]; then
  if [ "$USE_VENV" = "1" ]; then
    if [ -x "$VENV_DIR/bin/python" ] && ! python_ok "$VENV_DIR/bin/python"; then
      log "existing managed venv uses Python $(python_version "$VENV_DIR/bin/python"); recreating with Python $(python_version "$PYTHON_BIN")"
      run_cmd rm -rf "$VENV_DIR"
    fi
    if [ ! -x "$VENV_DIR/bin/python" ]; then
      log "creating managed virtual environment"
      if [ "$USE_UV" = "1" ]; then
        run_cmd "$UV_BIN" venv --python "$PYTHON_BIN" "$VENV_DIR"
      else
        run_cmd "$PYTHON_BIN" -m venv "$VENV_DIR"
      fi
    fi
    if [ "$DRY_RUN" != "1" ] && ! python_ok "$PYTHON_CMD"; then
      fail "managed venv Python is $(python_version "$PYTHON_CMD"), but Memora requires Python >= 3.10"
    fi
    if [ "$USE_UV" != "1" ]; then
      log "upgrading pip"
      run_cmd "$PYTHON_CMD" -m pip install -U pip
    fi
  elif ! python_ok "$PYTHON_CMD"; then
    fail "Python >= 3.10 is required, but $PYTHON_CMD is Python $(python_version "$PYTHON_CMD")"
  fi

  INSTALL_SPEC="$REPO_ROOT"
  INSTALL_TARGET="package"
  if [ "$WITH_TEST" = "1" ]; then
    INSTALL_SPEC="$REPO_ROOT[test]"
    INSTALL_TARGET="package with test extra"
  fi
  if [ "$USE_UV" = "1" ]; then
    INSTALL_CMD=("$UV_BIN" pip install --python "$PYTHON_CMD" -e "$INSTALL_SPEC")
  else
    INSTALL_CMD=("$PYTHON_CMD" -m pip install -e "$INSTALL_SPEC")
  fi
  log "installing Memora $INSTALL_TARGET"
  run_cmd "${INSTALL_CMD[@]}"
fi

MEMORA_WRAPPER='#!/usr/bin/env bash
set -euo pipefail
# memora default vault (managed)
__DEFAULT_VAULT_EXPORT__
export MEMORA_INSTALL_DIR="${MEMORA_INSTALL_DIR:-__INSTALL_DIR__}"
if [ -n "${MEMORA_DEFAULT_VAULT:-}" ] && [ -z "${MEMORA_VAULT:-}" ]; then
  export MEMORA_VAULT="$MEMORA_DEFAULT_VAULT"
fi
exec "__PYTHON_CMD__" -m cli "$@"
'

MEMORA_WRAPPER="${MEMORA_WRAPPER//__PYTHON_CMD__/$PYTHON_CMD}"
MEMORA_WRAPPER="${MEMORA_WRAPPER//__INSTALL_DIR__/$INSTALL_DIR}"

if [ -n "$VAULT_PATH" ]; then
  DEFAULT_VAULT_EXPORT="export MEMORA_DEFAULT_VAULT=\"$VAULT_PATH\""
else
  DEFAULT_VAULT_EXPORT=":"
fi
MEMORA_WRAPPER="${MEMORA_WRAPPER//__DEFAULT_VAULT_EXPORT__/$DEFAULT_VAULT_EXPORT}"

log "installing wrapper commands"
write_file "$BIN_DIR/memora" 0755 "$MEMORA_WRAPPER"

if [ -n "$VAULT_PATH" ]; then
  log "initializing vault: $VAULT_PATH"
  run_cmd "$BIN_DIR/memora" init "$VAULT_PATH" >/dev/null
fi

cat <<EOF

Memora installed.

Add this directory to PATH if needed:
  export PATH="$BIN_DIR:\$PATH"

CLI:
  memora status
  memora reindex --clean
  memora agent integrate --client all --project /path/to/project --dry-run

Change the default vault later:
  memora vault set /path/to/initialized-vault

Notes:
  - Use generated agent instructions and CLI JSON commands for coding-agent integrations.
EOF
