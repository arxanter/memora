#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="agent-memory"
MACOS_LABEL="com.agent-memory.service"
SYSTEMD_UNIT="agent-memory.service"

usage() {
  cat <<'USAGE'
Usage: agent-memory-service <command> [options]

Manage the local Agent Memory maintenance service.

Commands:
  install       Install the user-level launchd/systemd service.
  uninstall     Remove the user-level service.
  start         Start the service.
  stop          Stop the service.
  restart       Restart the service.
  status        Show service status.
  logs          Follow or print service logs.
  doctor        Run memory doctor against the configured vault.
  run           Internal service loop used by launchd/systemd.

Options:
  --vault PATH      Override AGENT_MEMORY_VAULT for this invocation.
  --interval SEC    Service doctor interval for install/run.
  --no-follow       For logs: print current logs instead of following.
  -h, --help        Show this help.

Environment:
  AGENT_MEMORY_VAULT                     Vault path.
  AGENT_MEMORY_BIN_DIR                   Wrapper directory. Default: ~/.local/bin
  AGENT_MEMORY_INSTALL_DIR               Install state directory. Default: ~/.local/share/agent-memory
  AGENT_MEMORY_SERVICE_INTERVAL_SECONDS  Doctor interval. Default: 3600
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

platform() {
  case "$(uname -s)" in
    Darwin) printf '%s\n' "macos" ;;
    Linux) printf '%s\n' "linux" ;;
    *) fail "unsupported platform: $(uname -s). This service supports macOS and Linux." ;;
  esac
}

memory_cmd() {
  if command -v memory >/dev/null 2>&1; then
    command -v memory
  elif [ -x "$BIN_DIR/memory" ]; then
    printf '%s\n' "$BIN_DIR/memory"
  else
    fail "memory command not found. Run scripts/install.sh first or add wrappers to PATH."
  fi
}

service_cmd() {
  if command -v agent-memory-service >/dev/null 2>&1; then
    command -v agent-memory-service
  elif [ -x "$BIN_DIR/agent-memory-service" ]; then
    printf '%s\n' "$BIN_DIR/agent-memory-service"
  else
    abs_path "$0"
  fi
}

require_vault() {
  if [ -z "${AGENT_MEMORY_VAULT:-}" ]; then
    fail "AGENT_MEMORY_VAULT is not set. Pass --vault or reinstall with scripts/install.sh --vault PATH."
  fi
}

write_macos_plist() {
  require_vault
  mkdir -p "$(dirname "$MACOS_PLIST")" "$LOG_DIR"
  local service
  service="$(service_cmd)"
  cat > "$MACOS_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$MACOS_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$service</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AGENT_MEMORY_VAULT</key>
    <string>$AGENT_MEMORY_VAULT</string>
    <key>AGENT_MEMORY_INSTALL_DIR</key>
    <string>$INSTALL_DIR</string>
    <key>AGENT_MEMORY_BIN_DIR</key>
    <string>$BIN_DIR</string>
    <key>AGENT_MEMORY_SERVICE_INTERVAL_SECONDS</key>
    <string>$INTERVAL_SECONDS</string>
    <key>PATH</key>
    <string>$BIN_DIR:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$STDOUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$STDERR_LOG</string>
</dict>
</plist>
EOF
}

write_systemd_unit() {
  require_vault
  mkdir -p "$(dirname "$SYSTEMD_UNIT_PATH")" "$LOG_DIR"
  local service
  service="$(service_cmd)"
  cat > "$SYSTEMD_UNIT_PATH" <<EOF
[Unit]
Description=Agent Memory local maintenance service
Documentation=file://$INSTALL_DIR

[Service]
Type=simple
ExecStart=$service run
Restart=always
RestartSec=5
Environment=AGENT_MEMORY_VAULT=$AGENT_MEMORY_VAULT
Environment=AGENT_MEMORY_INSTALL_DIR=$INSTALL_DIR
Environment=AGENT_MEMORY_BIN_DIR=$BIN_DIR
Environment=AGENT_MEMORY_SERVICE_INTERVAL_SECONDS=$INTERVAL_SECONDS
Environment=PATH=$BIN_DIR:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
EOF
}

install_service() {
  case "$PLATFORM" in
    macos)
      write_macos_plist
      log "installed $MACOS_PLIST"
      ;;
    linux)
      write_systemd_unit
      systemctl --user daemon-reload
      systemctl --user enable "$SYSTEMD_UNIT"
      log "installed $SYSTEMD_UNIT_PATH"
      ;;
  esac
}

uninstall_service() {
  stop_service || true
  case "$PLATFORM" in
    macos)
      rm -f "$MACOS_PLIST"
      log "removed $MACOS_PLIST"
      ;;
    linux)
      systemctl --user disable "$SYSTEMD_UNIT" >/dev/null 2>&1 || true
      rm -f "$SYSTEMD_UNIT_PATH"
      systemctl --user daemon-reload
      log "removed $SYSTEMD_UNIT_PATH"
      ;;
  esac
}

start_service() {
  case "$PLATFORM" in
    macos)
      [ -f "$MACOS_PLIST" ] || install_service
      launchctl bootstrap "gui/$(id -u)" "$MACOS_PLIST" 2>/dev/null || launchctl load "$MACOS_PLIST"
      launchctl kickstart -k "gui/$(id -u)/$MACOS_LABEL" 2>/dev/null || true
      ;;
    linux)
      [ -f "$SYSTEMD_UNIT_PATH" ] || install_service
      systemctl --user start "$SYSTEMD_UNIT"
      ;;
  esac
}

stop_service() {
  case "$PLATFORM" in
    macos)
      launchctl bootout "gui/$(id -u)" "$MACOS_PLIST" 2>/dev/null || launchctl unload "$MACOS_PLIST" 2>/dev/null || true
      ;;
    linux)
      systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
      ;;
  esac
}

status_service() {
  case "$PLATFORM" in
    macos)
      if launchctl print "gui/$(id -u)/$MACOS_LABEL" >/dev/null 2>&1; then
        launchctl print "gui/$(id -u)/$MACOS_LABEL"
      else
        printf '%s\n' "$MACOS_LABEL is not running"
        [ -f "$MACOS_PLIST" ] && printf '%s\n' "plist: $MACOS_PLIST"
      fi
      ;;
    linux)
      systemctl --user status "$SYSTEMD_UNIT" --no-pager || true
      ;;
  esac
}

show_logs() {
  mkdir -p "$LOG_DIR"
  touch "$STDOUT_LOG" "$STDERR_LOG"
  if [ "$FOLLOW_LOGS" = "1" ]; then
    tail -n 80 -f "$STDOUT_LOG" "$STDERR_LOG"
  else
    printf '%s\n' "==> $STDOUT_LOG"
    sed -n '1,200p' "$STDOUT_LOG"
    printf '%s\n' "==> $STDERR_LOG"
    sed -n '1,200p' "$STDERR_LOG"
  fi
}

doctor() {
  require_vault
  "$(memory_cmd)" doctor --vault "$AGENT_MEMORY_VAULT" --json
}

run_loop() {
  require_vault
  mkdir -p "$LOG_DIR"
  log "Agent Memory service started"
  log "vault: $AGENT_MEMORY_VAULT"
  log "interval: $INTERVAL_SECONDS seconds"
  while true; do
    if "$(memory_cmd)" doctor --vault "$AGENT_MEMORY_VAULT" --json; then
      log "doctor passed"
    else
      warn "doctor reported issues"
    fi
    sleep "$INTERVAL_SECONDS"
  done
}

COMMAND="${1:-}"
if [ -z "$COMMAND" ]; then
  usage
  exit 1
fi
shift || true

INSTALL_DIR="${AGENT_MEMORY_INSTALL_DIR:-$HOME/.local/share/agent-memory}"
BIN_DIR="${AGENT_MEMORY_BIN_DIR:-$HOME/.local/bin}"
INTERVAL_SECONDS="${AGENT_MEMORY_SERVICE_INTERVAL_SECONDS:-3600}"
FOLLOW_LOGS=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vault)
      [ "$#" -ge 2 ] || fail "--vault requires a path"
      export AGENT_MEMORY_VAULT
      AGENT_MEMORY_VAULT="$(abs_path "$2")"
      shift 2
      ;;
    --interval)
      [ "$#" -ge 2 ] || fail "--interval requires seconds"
      INTERVAL_SECONDS="$2"
      shift 2
      ;;
    --no-follow)
      FOLLOW_LOGS=0
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
PLATFORM="$(platform)"
LOG_DIR="$INSTALL_DIR/logs"
STDOUT_LOG="$LOG_DIR/service.out.log"
STDERR_LOG="$LOG_DIR/service.err.log"
MACOS_PLIST="$HOME/Library/LaunchAgents/$MACOS_LABEL.plist"
SYSTEMD_UNIT_PATH="$HOME/.config/systemd/user/$SYSTEMD_UNIT"

case "$COMMAND" in
  install) install_service ;;
  uninstall) uninstall_service ;;
  start) start_service ;;
  stop) stop_service ;;
  restart)
    stop_service || true
    start_service
    ;;
  status) status_service ;;
  logs) show_logs ;;
  doctor) doctor ;;
  run) run_loop ;;
  -h|--help)
    usage
    ;;
  *)
    fail "unknown command: $COMMAND"
    ;;
esac
