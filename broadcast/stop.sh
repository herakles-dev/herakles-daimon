#!/usr/bin/env bash
# Daimon Live — Stop broadcast pipeline
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="${SCRIPT_DIR}/.pids"
DISPLAY_NUM="${DISPLAY_NUM:-99}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

if [ -f "$PIDFILE" ]; then
    log "Stopping processes from pidfile..."
    while read -r pid name; do
        if kill -0 "$pid" 2>/dev/null; then
            log "  Stopping $name (PID $pid)"
            kill "$pid" 2>/dev/null || true
        else
            log "  $name (PID $pid) already stopped"
        fi
    done < "$PIDFILE"
    rm -f "$PIDFILE"
else
    log "No pidfile found — killing by pattern..."
fi

# Belt and suspenders — correct process names
pkill -f "Xvfb :${DISPLAY_NUM}" 2>/dev/null || true
pkill -f "google-chrome.*broadcast" 2>/dev/null || true
pkill -f "chrome.*broadcast" 2>/dev/null || true
pkill -f "ffmpeg.*x11grab.*:${DISPLAY_NUM}" 2>/dev/null || true
pulseaudio --kill 2>/dev/null || true

# Grace period then force-kill any remaining Chrome processes
sleep 2
pkill -9 -f "google-chrome" -u "$(whoami)" 2>/dev/null || true
pkill -9 -f "chrome" -u "$(whoami)" 2>/dev/null || true

# Clean up Chrome temp data
rm -rf /tmp/muse-live-chrome 2>/dev/null || true

log "Broadcast pipeline stopped."
