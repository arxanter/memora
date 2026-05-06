#!/usr/bin/env bash
set -euo pipefail

REPO="${MEMORA_REPO:-arxanter/memora}"
TAG="${MEMORA_VERSION:-latest}"
MEMORA_HOME="${MEMORA_HOME:-$HOME/.memora}"
FORCE="${MEMORA_FORCE:-0}"

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [options]

Install Memora from GitHub release binaries without requiring Cargo.

Options:
  --repo OWNER/REPO     GitHub repository. Default: arxanter/memora
  --version TAG         Release tag. Default: latest
  --force               Overwrite existing ~/.memora/bin/memora
  --no-shell-integration
                        Do not update the current shell startup file
  -h, --help            Show this help

Environment:
  MEMORA_REPO
  MEMORA_VERSION
  MEMORA_HOME
  MEMORA_FORCE=1
  MEMORA_SHELL_INTEGRATION=0
USAGE
}

SHELL_INTEGRATION="${MEMORA_SHELL_INTEGRATION:-1}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      REPO="${2:?missing value for --repo}"
      shift 2
      ;;
    --version)
      TAG="${2:?missing value for --version}"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --no-shell-integration)
      SHELL_INTEGRATION=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

detect_target() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os:$arch" in
    Darwin:arm64) echo "aarch64-apple-darwin" ;;
    Darwin:x86_64) echo "x86_64-apple-darwin" ;;
    Linux:x86_64) echo "x86_64-unknown-linux-gnu" ;;
    *)
      echo "unsupported platform: $os $arch" >&2
      exit 1
      ;;
  esac
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

shell_single_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

download() {
  require_command curl
  curl -fsSL "$1" -o "$2"
}

TARGET="$(detect_target)"
ASSET="memora-$TARGET"
BASE_URL="https://github.com/$REPO/releases"
if [ "$TAG" = "latest" ]; then
  DOWNLOAD_URL="$BASE_URL/latest/download"
else
  DOWNLOAD_URL="$BASE_URL/download/$TAG"
fi

if [ -x "$MEMORA_HOME/bin/memora" ] && [ "$FORCE" != "1" ]; then
  echo "Memora already installed at $MEMORA_HOME/bin/memora" >&2
  echo "Pass --force to overwrite." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading $ASSET from $REPO ($TAG)..."
download "$DOWNLOAD_URL/$ASSET" "$TMP/memora"
download "$DOWNLOAD_URL/SHA256SUMS" "$TMP/SHA256SUMS"

EXPECTED="$(awk -v asset="$ASSET" '$2 == asset { print $1 }' "$TMP/SHA256SUMS")"
if [ -z "$EXPECTED" ]; then
  echo "checksum for $ASSET not found in SHA256SUMS" >&2
  exit 1
fi

ACTUAL="$(sha256_file "$TMP/memora")"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "sha256 mismatch for $ASSET" >&2
  echo "expected: $EXPECTED" >&2
  echo "actual:   $ACTUAL" >&2
  exit 1
fi

chmod 0755 "$TMP/memora"

INSTALL_ARGS=(self install --from "$TMP/memora" --sha256 "$EXPECTED")
if [ "$FORCE" = "1" ]; then
  INSTALL_ARGS+=(--force)
fi
if [ "$SHELL_INTEGRATION" = "0" ]; then
  INSTALL_ARGS+=(--no-shell-integration)
fi

MEMORA_HOME="$MEMORA_HOME" "$TMP/memora" "${INSTALL_ARGS[@]}"

SHELL_NAME="$(basename "${SHELL:-zsh}")"
case "$SHELL_NAME" in
  bash|zsh|fish)
    ;;
  *)
    SHELL_NAME="zsh"
    ;;
esac

echo
echo "Memora installed at: $MEMORA_HOME/bin/memora"
echo "Open a new shell or activate it now:"
case "$SHELL_NAME" in
  fish)
    echo "  env MEMORA_HOME=$(shell_single_quote "$MEMORA_HOME") $(shell_single_quote "$MEMORA_HOME/bin/memora") self shell-init fish | source"
    ;;
  *)
    echo "  eval \"\$(MEMORA_HOME=$(shell_single_quote "$MEMORA_HOME") $(shell_single_quote "$MEMORA_HOME/bin/memora") self shell-init $SHELL_NAME)\""
    ;;
esac
