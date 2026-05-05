#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [options]

Install Memora locally without manual venv activation.

Options:
  --home PATH               Managed Memora home containing engine, vault,
                            config, state, and venv. Default: ~/memora
  --install-dir PATH        Deprecated alias for --home.
  --engine-source PATH      Source checkout or URL used to create engine/.
                            Default: this checkout.
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
  MEMORA_HOME         Default managed Memora home.
  MEMORA_INSTALL_DIR  Deprecated alias for MEMORA_HOME.
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

MEMORA_HOME_DIR="${MEMORA_HOME:-${MEMORA_INSTALL_DIR:-$HOME/memora}}"
BIN_DIR="${MEMORA_BIN_DIR:-$HOME/.local/bin}"
ENGINE_SOURCE=""
ENGINE_REMOTE_URL=""
PYTHON_BIN="${PYTHON:-}"
UV_BIN="${UV:-uv}"
USE_UV=0
USE_VENV=1
SKIP_INSTALL=0
WITH_TEST=0
FORCE=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --home)
      [ "$#" -ge 2 ] || fail "--home requires a path"
      MEMORA_HOME_DIR="$2"
      shift 2
      ;;
    --install-dir)
      [ "$#" -ge 2 ] || fail "--install-dir requires a path"
      warn "--install-dir is deprecated; use --home"
      MEMORA_HOME_DIR="$2"
      shift 2
      ;;
    --engine-source)
      [ "$#" -ge 2 ] || fail "--engine-source requires a path or URL"
      ENGINE_SOURCE="$2"
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

MEMORA_HOME_DIR="$(abs_path "$MEMORA_HOME_DIR")"
BIN_DIR="$(abs_path "$BIN_DIR")"
ENGINE_DIR="$MEMORA_HOME_DIR/engine"
VAULT_PATH="$MEMORA_HOME_DIR/vault"
VENV_DIR="$MEMORA_HOME_DIR/venv"
CONFIG_PATH="$MEMORA_HOME_DIR/config.yaml"
STATE_DIR="$MEMORA_HOME_DIR/state"
if [ -z "$ENGINE_SOURCE" ]; then
  ENGINE_SOURCE="$REPO_ROOT"
fi
if command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
  ENGINE_REMOTE_URL="$(git -C "$REPO_ROOT" remote get-url origin)"
fi

if [ "$DRY_RUN" = "1" ] && [ "$SKIP_INSTALL" = "1" ] && [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || printf '%s\n' python3)"
else
  PYTHON_BIN="$(select_python "$PYTHON_BIN")"
fi
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
log "memora home: $MEMORA_HOME_DIR"
log "engine dir: $ENGINE_DIR"
log "vault dir: $VAULT_PATH"
log "config: $CONFIG_PATH"
log "state dir: $STATE_DIR"
log "bin dir: $BIN_DIR"

if [ "$DRY_RUN" = "1" ]; then
  log "would create managed home directories"
else
  mkdir -p "$MEMORA_HOME_DIR" "$VAULT_PATH" "$STATE_DIR"
  chmod 700 "$MEMORA_HOME_DIR" "$VAULT_PATH" 2>/dev/null || true
fi

if [ "$REPO_ROOT" != "$ENGINE_DIR" ]; then
  if [ -d "$ENGINE_DIR/.git" ]; then
    log "engine checkout already exists"
  elif [ -e "$ENGINE_DIR" ]; then
    fail "$ENGINE_DIR already exists but is not a git checkout"
  else
    log "creating engine checkout"
    run_cmd git clone "$ENGINE_SOURCE" "$ENGINE_DIR"
    if [ -n "$ENGINE_REMOTE_URL" ] && [ "$ENGINE_REMOTE_URL" != "$ENGINE_SOURCE" ]; then
      run_cmd git -C "$ENGINE_DIR" remote set-url origin "$ENGINE_REMOTE_URL"
    fi
  fi
else
  log "engine checkout: current repository"
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

  INSTALL_SPEC="$ENGINE_DIR"
  INSTALL_TARGET="package"
  if [ "$WITH_TEST" = "1" ]; then
    INSTALL_SPEC="$ENGINE_DIR[test]"
    INSTALL_TARGET="package with test extra"
  fi
  if [ "$USE_UV" = "1" ]; then
    INSTALL_CMD=("$UV_BIN" pip install --python "$PYTHON_CMD" -e "$INSTALL_SPEC")
  else
    INSTALL_CMD=("$PYTHON_CMD" -m pip install -e "$INSTALL_SPEC")
  fi
  log "installing Memora $INSTALL_TARGET"
  run_cmd "${INSTALL_CMD[@]}"
  log "verifying runtime dependencies and semantic provider"
  run_cmd "$PYTHON_CMD" - <<'PY'
import fastembed
import pydantic
import rich
import typer
import yaml
from config import SemanticConfig
from embeddings import provider_from_config

provider = provider_from_config(SemanticConfig())
provider.embed(["memora semantic install check"])
PY
fi

MEMORA_WRAPPER='#!/usr/bin/env bash
set -euo pipefail
# memora managed home
export MEMORA_HOME="${MEMORA_HOME:-__MEMORA_HOME__}"
export MEMORA_INSTALL_DIR="${MEMORA_INSTALL_DIR:-$MEMORA_HOME}"
exec "__PYTHON_CMD__" -m cli "$@"
'

MEMORA_WRAPPER="${MEMORA_WRAPPER//__PYTHON_CMD__/$PYTHON_CMD}"
MEMORA_WRAPPER="${MEMORA_WRAPPER//__MEMORA_HOME__/$MEMORA_HOME_DIR}"

log "installing wrapper commands"
write_file "$BIN_DIR/memora" 0755 "$MEMORA_WRAPPER"

log "initializing managed Memora home"
run_cmd "$BIN_DIR/memora" setup "$MEMORA_HOME_DIR" >/dev/null

cat <<EOF

Memora installed.

Add this directory to PATH if needed:
  export PATH="$BIN_DIR:\$PATH"

CLI:
  memora status
  memora reindex --clean
  memora agent integrate --client all --project /path/to/project --dry-run

Managed layout:
  home:   $MEMORA_HOME_DIR
  engine: $ENGINE_DIR
  vault:  $VAULT_PATH
  config: $CONFIG_PATH
  state:  $STATE_DIR

Notes:
  - self update changes engine and venv, never vault.
  - Use generated agent instructions and CLI JSON commands for coding-agent integrations.
EOF
