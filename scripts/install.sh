#!/usr/bin/env bash
set -euo pipefail

REPO="${MEMORA_REPO:-arxanter/memora}"
TAG="${MEMORA_VERSION:-latest}"
MEMORA_HOME="${MEMORA_HOME:-$HOME/memora}"
FORCE="${MEMORA_FORCE:-0}"

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [options]

Install Memora from GitHub release binaries without requiring Cargo.

Options:
  --repo OWNER/REPO     GitHub repository. Default: arxanter/memora
  --version TAG         Release tag. Default: latest
  --home PATH           Memora home. Default: ~/memora
  --force               Overwrite existing ~/memora/bin/memora
  -h, --help            Show this help

Environment:
  MEMORA_REPO
  MEMORA_VERSION
  MEMORA_HOME
  MEMORA_FORCE=1
USAGE
}

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
    --home)
      MEMORA_HOME="${2:?missing value for --home}"
      shift 2
      ;;
    --force)
      FORCE=1
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

INSTALL_ARGS=(--home "$MEMORA_HOME" self install --from "$TMP/memora" --sha256 "$EXPECTED")
if [ "$FORCE" = "1" ]; then
  INSTALL_ARGS+=(--force)
fi

"$TMP/memora" "${INSTALL_ARGS[@]}"

SHELL_NAME="$(basename "${SHELL:-zsh}")"
case "$SHELL_NAME" in
  bash|zsh|fish|powershell|elvish)
    ;;
  *)
    SHELL_NAME="zsh"
    ;;
esac

echo
echo "Memora installed at: $MEMORA_HOME/bin/memora"
echo "Activate it in this shell:"
echo "  eval \"\$($MEMORA_HOME/bin/memora self shell-init $SHELL_NAME)\""
