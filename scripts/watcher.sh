#!/usr/bin/env bash
# Restart the proxy whenever /health stops answering.
#
# Runs on a Linux host that keeps the proxy up permanently, such as a
# Raspberry Pi, and on Windows through Git Bash. Safe to re-run: a lock file
# keeps one watcher active and a second invocation exits immediately.
#
#   ./scripts/watcher.sh
#
# Stop it with Ctrl+C, or with `kill $(cat "$RUNTIME_DIR/watcher.pid")`.
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="${WORKBUDDY2OPENAI_RUNTIME_DIR:-$REPO_ROOT}"
LOCK="$RUNTIME_DIR/watcher.pid"
LOG="${WORKBUDDY2OPENAI_LOG:-$REPO_ROOT/watcher.log}"
HEALTH_URL="${WORKBUDDY2OPENAI_HEALTH_URL:-http://127.0.0.1:8787/health}"
PORT="${WORKBUDDY2OPENAI_PORT:-8787}"
INTERVAL="${WORKBUDDY2OPENAI_INTERVAL:-60}"

cd "$REPO_ROOT"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

is_watcher() {
  local pid="$1" cmd
  cmd=$(process_command_line "$pid")
  case "$cmd" in
    *watcher.sh*) return 0 ;;
    *) return 1 ;;
  esac
}

# The command line of a pid. /proc covers Linux and covers an MSYS pid on Git
# Bash, but a pid from `netstat -ano` on Windows is a native pid with no /proc
# entry, so Windows falls back to PowerShell CIM.
process_command_line() {
  local pid="$1"
  if [ -r "/proc/$pid/cmdline" ]; then
    tr '\0' ' ' < "/proc/$pid/cmdline"
    return
  fi
  if command -v powershell > /dev/null 2>&1; then
    powershell -NoProfile -Command \
      "(Get-CimInstance Win32_Process -Filter \"ProcessId=$pid\").CommandLine" 2>/dev/null | tr -d '\r'
    return
  fi
  ps -p "$pid" -o args= 2>/dev/null
}

# `curl -o /dev/null` exits 23 when curl cannot open the /dev/null pseudo-file,
# which read as "down" on every tick. A bash-level redirect keeps curl out of
# the file handling, so the exit code reflects the HTTP result alone.
health_ok() {
  curl -s -m 3 -f "$HEALTH_URL" > /dev/null 2>&1
}

pid_on_port() {
  local pid
  if command -v ss > /dev/null 2>&1; then
    pid=$(ss -lptn "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1)
    [ -n "$pid" ] && { echo "$pid"; return; }
  fi
  if command -v lsof > /dev/null 2>&1; then
    pid=$(lsof -ti tcp:"$PORT" -s TCP:LISTEN 2>/dev/null | head -1)
    [ -n "$pid" ] && { echo "$pid"; return; }
  fi
  if command -v netstat > /dev/null 2>&1; then
    pid=$(netstat -ano 2>/dev/null | grep ":$PORT" | grep -i LISTENING | awk '{print $NF}' | sort -u | head -1)
    [ -n "$pid" ] && echo "$pid"
  fi
}

# Kill only the converter process holding the port, never a blanket python
# kill. Without this the restart fails with Errno 10048 (address already in
# use) because the previous process still owns the socket.
kill_port_holder() {
  local pid cmd
  pid=$(pid_on_port)
  [ -z "$pid" ] && return 0
  cmd=$(process_command_line "$pid")
  if echo "$cmd" | grep -q "workbuddy2openai"; then
    kill "$pid" 2>/dev/null || true
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || taskkill /F /PID "$pid" > /dev/null 2>&1 || true
    fi
    log "killed stale converter pid $pid"
  fi
}

# Start the proxy detached, so it outlives the watcher's own process group.
start_proxy() {
  if command -v setsid > /dev/null 2>&1; then
    ( cd "$REPO_ROOT" && setsid bash scripts/start.sh >> "$LOG" 2>&1 < /dev/null & )
  else
    ( cd "$REPO_ROOT" && bash scripts/start.sh >> "$LOG" 2>&1 < /dev/null & )
  fi
}

# Honour the lock only when the recorded pid is a live watcher.sh. A stale lock
# holding a recycled pid passes `kill -0` for an unrelated process, which used
# to make every new watcher exit silently, leaving nobody supervising.
if [ -f "$LOCK" ]; then
  lock_pid=$(tr -d '[:space:]' < "$LOCK")
  if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null && is_watcher "$lock_pid"; then
    echo "watcher already running (pid $lock_pid)" >&2
    exit 0
  fi
  rm -f "$LOCK"
fi

echo $$ > "$LOCK"
log "watcher started (pid $$, repo $REPO_ROOT)"

cleanup() {
  [ "$(cat "$LOCK" 2>/dev/null)" = "$$" ] && rm -f "$LOCK"
  log "watcher stopped (pid $$)"
  exit 0
}
trap cleanup INT TERM

while true; do
  if ! health_ok; then
    log "proxy down - restarting"
    kill_port_holder
    sleep 1
    start_proxy
    sleep 5
    if health_ok; then
      log "proxy back up"
    else
      log "restart attempt failed - will retry next tick"
    fi
  fi
  sleep "$INTERVAL"
done
