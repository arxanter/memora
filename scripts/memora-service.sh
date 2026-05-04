#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="memora"
MACOS_LABEL="com.memora.service"
SYSTEMD_UNIT="memora.service"

usage() {
  cat <<'USAGE'
Usage: memora-service <command> [options]

Manage the local Memora maintenance service.

Commands:
  install       Install the user-level launchd/systemd service.
  uninstall     Remove the user-level service.
  start         Start the service.
  stop          Stop the service.
  restart       Restart the service.
  status        Show service status.
  logs          Follow or print service logs.
  doctor        Run memora doctor against the configured vault.
  watch         Poll durable vault files and refresh the index when needed.
  run           Internal service loop used by launchd/systemd.

Options:
  --vault PATH      Override MEMORA_VAULT for this invocation.
  --interval SEC    Service doctor interval for install/run.
  --watch-interval SEC
                  Freshness polling interval for install/run/watch.
  --watch-debounce SEC
                  Quiet period before freshness-triggered reindex.
  --no-watch       Disable freshness polling in run/watch.
  --watch-clean    Run freshness-triggered reindex with --clean.
  --no-follow       For logs: print current logs instead of following.
  -h, --help        Show this help.

Environment:
  MEMORA_VAULT                     Vault path.
  MEMORA_BIN_DIR                   Wrapper directory. Default: ~/.local/bin
  MEMORA_INSTALL_DIR               Install state directory. Default: ~/.local/share/memora
  MEMORA_SERVICE_INTERVAL_SECONDS  Doctor interval. Default: 3600
  MEMORA_FRESHNESS_ENABLED         Enable freshness polling. Default: 1
  MEMORA_FRESHNESS_INTERVAL_SECONDS
                                          Freshness poll interval. Default: 30
  MEMORA_FRESHNESS_DEBOUNCE_SECONDS
                                          Freshness debounce. Default: 2
  MEMORA_FRESHNESS_CLEAN           Use clean reindex for freshness. Default: 0
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

memora_cmd() {
  if command -v memora >/dev/null 2>&1; then
    command -v memora
  elif [ -x "$BIN_DIR/memora" ]; then
    printf '%s\n' "$BIN_DIR/memora"
  else
    fail "memora command not found. Run scripts/install.sh first or add wrappers to PATH."
  fi
}

service_cmd() {
  if command -v memora-service >/dev/null 2>&1; then
    command -v memora-service
  elif [ -x "$BIN_DIR/memora-service" ]; then
    printf '%s\n' "$BIN_DIR/memora-service"
  else
    abs_path "$0"
  fi
}

require_vault() {
  if [ -z "${MEMORA_VAULT:-}" ]; then
    fail "MEMORA_VAULT is not set. Pass --vault or reinstall with scripts/install.sh --vault PATH."
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
    <key>MEMORA_VAULT</key>
    <string>$MEMORA_VAULT</string>
    <key>MEMORA_INSTALL_DIR</key>
    <string>$INSTALL_DIR</string>
    <key>MEMORA_BIN_DIR</key>
    <string>$BIN_DIR</string>
    <key>MEMORA_SERVICE_INTERVAL_SECONDS</key>
    <string>$INTERVAL_SECONDS</string>
    <key>MEMORA_FRESHNESS_ENABLED</key>
    <string>$WATCH_ENABLED</string>
    <key>MEMORA_FRESHNESS_INTERVAL_SECONDS</key>
    <string>$WATCH_INTERVAL_SECONDS</string>
    <key>MEMORA_FRESHNESS_DEBOUNCE_SECONDS</key>
    <string>$WATCH_DEBOUNCE_SECONDS</string>
    <key>MEMORA_FRESHNESS_CLEAN</key>
    <string>$WATCH_CLEAN</string>
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
Description=Memora local maintenance service
Documentation=file://$INSTALL_DIR

[Service]
Type=simple
ExecStart=$service run
Restart=always
RestartSec=5
Environment=MEMORA_VAULT=$MEMORA_VAULT
Environment=MEMORA_INSTALL_DIR=$INSTALL_DIR
Environment=MEMORA_BIN_DIR=$BIN_DIR
Environment=MEMORA_SERVICE_INTERVAL_SECONDS=$INTERVAL_SECONDS
Environment=MEMORA_FRESHNESS_ENABLED=$WATCH_ENABLED
Environment=MEMORA_FRESHNESS_INTERVAL_SECONDS=$WATCH_INTERVAL_SECONDS
Environment=MEMORA_FRESHNESS_DEBOUNCE_SECONDS=$WATCH_DEBOUNCE_SECONDS
Environment=MEMORA_FRESHNESS_CLEAN=$WATCH_CLEAN
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
  "$(memora_cmd)" doctor --vault "$MEMORA_VAULT" --json
}

refresh_index_if_needed() {
  require_vault
  if [ "$WATCH_ENABLED" != "1" ]; then
    return 0
  fi

  local args
  args=(refresh-index --vault "$MEMORA_VAULT" --debounce "$WATCH_DEBOUNCE_SECONDS" --json)
  if [ "$WATCH_CLEAN" = "1" ]; then
    args+=(--clean)
  else
    args+=(--no-clean)
  fi

  if "$(memora_cmd)" "${args[@]}"; then
    log "freshness check passed"
  else
    warn "freshness check reported issues"
  fi
}

run_doctor_once() {
  if "$(memora_cmd)" doctor --vault "$MEMORA_VAULT" --json; then
    log "doctor passed"
  else
    warn "doctor reported issues"
  fi
}

run_loop() {
  require_vault
  mkdir -p "$LOG_DIR"
  log "Memora service started"
  log "vault: $MEMORA_VAULT"
  log "doctor interval: $INTERVAL_SECONDS seconds"
  log "freshness enabled: $WATCH_ENABLED"
  log "freshness interval: $WATCH_INTERVAL_SECONDS seconds"
  log "freshness debounce: $WATCH_DEBOUNCE_SECONDS seconds"
  log "freshness clean: $WATCH_CLEAN"
  local next_doctor
  next_doctor=0
  while true; do
    if [ "$WATCH_ENABLED" = "1" ]; then
      refresh_index_if_needed
    fi

    local now
    now="$(date +%s)"
    if [ "$now" -ge "$next_doctor" ]; then
      run_doctor_once
      next_doctor=$((now + INTERVAL_SECONDS))
    fi

    if [ "$WATCH_ENABLED" = "1" ]; then
      sleep "$WATCH_INTERVAL_SECONDS"
    else
      sleep "$INTERVAL_SECONDS"
    fi
  done
}

watch_loop() {
  require_vault
  mkdir -p "$LOG_DIR"
  WATCH_ENABLED=1
  log "Memora freshness watcher started"
  log "vault: $MEMORA_VAULT"
  log "freshness interval: $WATCH_INTERVAL_SECONDS seconds"
  log "freshness debounce: $WATCH_DEBOUNCE_SECONDS seconds"
  log "freshness clean: $WATCH_CLEAN"
  while true; do
    refresh_index_if_needed
    sleep "$WATCH_INTERVAL_SECONDS"
  done
}

COMMAND="${1:-}"
if [ -z "$COMMAND" ]; then
  usage
  exit 1
fi
shift || true

INSTALL_DIR="${MEMORA_INSTALL_DIR:-$HOME/.local/share/memora}"
BIN_DIR="${MEMORA_BIN_DIR:-$HOME/.local/bin}"
INTERVAL_SECONDS="${MEMORA_SERVICE_INTERVAL_SECONDS:-3600}"
WATCH_ENABLED="${MEMORA_FRESHNESS_ENABLED:-1}"
WATCH_INTERVAL_SECONDS="${MEMORA_FRESHNESS_INTERVAL_SECONDS:-30}"
WATCH_DEBOUNCE_SECONDS="${MEMORA_FRESHNESS_DEBOUNCE_SECONDS:-2}"
WATCH_CLEAN="${MEMORA_FRESHNESS_CLEAN:-0}"
FOLLOW_LOGS=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vault)
      [ "$#" -ge 2 ] || fail "--vault requires a path"
      export MEMORA_VAULT
      MEMORA_VAULT="$(abs_path "$2")"
      shift 2
      ;;
    --interval)
      [ "$#" -ge 2 ] || fail "--interval requires seconds"
      INTERVAL_SECONDS="$2"
      shift 2
      ;;
    --watch-interval)
      [ "$#" -ge 2 ] || fail "--watch-interval requires seconds"
      WATCH_INTERVAL_SECONDS="$2"
      shift 2
      ;;
    --watch-debounce)
      [ "$#" -ge 2 ] || fail "--watch-debounce requires seconds"
      WATCH_DEBOUNCE_SECONDS="$2"
      shift 2
      ;;
    --no-watch)
      WATCH_ENABLED=0
      shift
      ;;
    --watch-clean)
      WATCH_CLEAN=1
      shift
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
  watch) watch_loop ;;
  run) run_loop ;;
  -h|--help)
    usage
    ;;
  *)
    fail "unknown command: $COMMAND"
    ;;
esac
