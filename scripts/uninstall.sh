#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/uninstall.sh [options]

Remove local Memora wrappers. Managed vault data is never deleted.

Options:
  --home PATH           Managed Memora home. Default: ~/memora
  --install-dir PATH    Deprecated alias for --home.
  --bin-dir PATH        Wrapper directory. Default: ~/.local/bin
  --remove-venv         Remove the managed virtual environment.
  --dry-run             Print actions without changing files.
  -h, --help            Show this help.
USAGE
}

log() {
  printf '%s\n' "==> $*"
}

expand_path() {
  case "$1" in
    "~") printf '%s\n' "$HOME" ;;
    "~/"*) printf '%s/%s\n' "$HOME" "${1#~/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

remove_path() {
  local path="$1"
  if [ "$DRY_RUN" = "1" ]; then
    log "would remove $path"
    return
  fi
  rm -rf "$path"
}

MEMORA_HOME_DIR="${MEMORA_HOME:-${MEMORA_INSTALL_DIR:-$HOME/memora}}"
BIN_DIR="${MEMORA_BIN_DIR:-$HOME/.local/bin}"
REMOVE_VENV=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --home)
      [ "$#" -ge 2 ] || { printf '%s\n' "error: --home requires a path" >&2; exit 1; }
      MEMORA_HOME_DIR="$2"
      shift 2
      ;;
    --install-dir)
      [ "$#" -ge 2 ] || { printf '%s\n' "error: --install-dir requires a path" >&2; exit 1; }
      MEMORA_HOME_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      [ "$#" -ge 2 ] || { printf '%s\n' "error: --bin-dir requires a path" >&2; exit 1; }
      BIN_DIR="$2"
      shift 2
      ;;
    --remove-venv)
      REMOVE_VENV=1
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
      printf '%s\n' "error: unknown option: $1" >&2
      exit 1
      ;;
  esac
done

MEMORA_HOME_DIR="$(expand_path "$MEMORA_HOME_DIR")"
BIN_DIR="$(expand_path "$BIN_DIR")"

remove_path "$BIN_DIR/memora"

if [ "$REMOVE_VENV" = "1" ]; then
  remove_path "$MEMORA_HOME_DIR/venv"
fi

log "uninstall complete"
log "managed vault, config, state, and engine were not removed"
