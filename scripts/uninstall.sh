#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/uninstall.sh [options]

Remove local Agent Memory wrappers and service files. Vault Markdown is never
deleted by default.

Options:
  --install-dir PATH    Install state directory. Default: ~/.local/share/agent-memory
  --bin-dir PATH        Wrapper directory. Default: ~/.local/bin
  --remove-venv         Remove the managed virtual environment.
  --remove-logs         Remove local service logs.
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

INSTALL_DIR="${AGENT_MEMORY_INSTALL_DIR:-$HOME/.local/share/agent-memory}"
BIN_DIR="${AGENT_MEMORY_BIN_DIR:-$HOME/.local/bin}"
REMOVE_VENV=0
REMOVE_LOGS=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      [ "$#" -ge 2 ] || { printf '%s\n' "error: --install-dir requires a path" >&2; exit 1; }
      INSTALL_DIR="$2"
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
    --remove-logs)
      REMOVE_LOGS=1
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

INSTALL_DIR="$(expand_path "$INSTALL_DIR")"
BIN_DIR="$(expand_path "$BIN_DIR")"

if [ -x "$BIN_DIR/agent-memory-service" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    log "would run $BIN_DIR/agent-memory-service uninstall"
  else
    "$BIN_DIR/agent-memory-service" uninstall || true
  fi
fi

remove_path "$BIN_DIR/memory"
remove_path "$BIN_DIR/agent-memory-service"

if [ "$REMOVE_VENV" = "1" ]; then
  remove_path "$INSTALL_DIR/venv"
fi

if [ "$REMOVE_LOGS" = "1" ]; then
  remove_path "$INSTALL_DIR/logs"
fi

log "uninstall complete"
log "vault Markdown was not removed"
