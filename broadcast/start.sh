#!/usr/bin/env bash
# Daimon Live — Start broadcast pipeline
# Launches: Xvfb → PulseAudio → Google Chrome → FFmpeg → YouTube RTMP
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/muse-live.env"
PIDFILE="${SCRIPT_DIR}/.pids"

# Load config
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
fi

# Required
: "${YOUTUBE_STREAM_KEY:?Set YOUTUBE_STREAM_KEY in muse-live.env}"
BROADCAST_URL="${BROADCAST_URL:-http://localhost:8151/broadcast}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
RESOLUTION="${RESOLUTION:-1280x720}"
FRAMERATE="${FRAMERATE:-30}"
VIDEO_BITRATE="${VIDEO_BITRATE:-4000k}"
AUDIO_BITRATE="${AUDIO_BITRATE:-128k}"
RTMP_URL="rtmp://a.rtmp.youtube.com/live2/${YOUTUBE_STREAM_KEY}"

# Compute bufsize = 2x bitrate (YouTube recommendation)
BITRATE_NUM=$(echo "${VIDEO_BITRATE}" | sed 's/[kK]$//')
BUFSIZE="$((BITRATE_NUM * 2))k"

export DISPLAY=":${DISPLAY_NUM}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

cleanup() {
    log "Stopping broadcast pipeline..."
    if [ -f "$PIDFILE" ]; then
        while read -r pid name; do
            if kill -0 "$pid" 2>/dev/null; then
                log "  Stopping $name (PID $pid)"
                kill "$pid" 2>/dev/null || true
            fi
        done < "$PIDFILE"
        rm -f "$PIDFILE"
    fi
    # Kill any leftover processes — use correct binary names
    pkill -f "Xvfb :${DISPLAY_NUM}" 2>/dev/null || true
    # Chrome forks many processes — kill the process tree
    pkill -f "google-chrome.*broadcast" 2>/dev/null || true
    pkill -f "chrome.*broadcast" 2>/dev/null || true
    sleep 2
    # Force-kill any remaining Chrome renderer/GPU processes
    pkill -9 -f "google-chrome" -u "$(whoami)" 2>/dev/null || true
    pkill -9 -f "chrome" -u "$(whoami)" 2>/dev/null || true
    log "Broadcast stopped."
}
trap cleanup EXIT

# ─── 0. Wait for backend + frontend containers to be healthy ───────────────
log "Waiting for backend and frontend..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8150/health > /dev/null 2>&1; then
        log "  Backend ready (${i}s)"
        break
    fi
    if [ "$i" -eq 60 ]; then
        log "ERROR: Backend not ready after 60s"
        exit 1
    fi
    sleep 1
done
for i in $(seq 1 30); do
    if curl -sf http://localhost:8151/broadcast > /dev/null 2>&1; then
        log "  Frontend ready (${i}s)"
        break
    fi
    if [ "$i" -eq 30 ]; then
        log "WARNING: Frontend not confirmed ready — continuing anyway"
    fi
    sleep 1
done

# ─── 1. Virtual framebuffer ─────────────────────────────────────────────────
log "Starting Xvfb :${DISPLAY_NUM} at ${RESOLUTION}..."
Xvfb ":${DISPLAY_NUM}" -screen 0 "${RESOLUTION}x24" -nolisten tcp &
XVFB_PID=$!
echo "$XVFB_PID xvfb" > "$PIDFILE"
sleep 1

if ! kill -0 $XVFB_PID 2>/dev/null; then
    log "ERROR: Xvfb failed to start"
    exit 1
fi
log "  Xvfb running (PID $XVFB_PID)"

# ─── 2. PulseAudio virtual sink ─────────────────────────────────────────────
log "Starting PulseAudio..."
pulseaudio --kill 2>/dev/null || true
sleep 0.5

pulseaudio --start --exit-idle-time=-1 \
    --load="module-null-sink sink_name=broadcast rate=48000 sink_properties=device.description=BroadcastSink" \
    --load="module-virtual-source source_name=broadcast_mic master=broadcast.monitor" \
    2>/dev/null || true
sleep 1

pactl set-default-sink broadcast 2>/dev/null || true
pactl set-default-source broadcast_mic 2>/dev/null || true

# Verify null-sink was created
if ! pactl list sinks short 2>/dev/null | grep -q broadcast; then
    log "ERROR: broadcast null-sink not created — audio capture will fail"
    exit 1
fi

PA_PID=$(pgrep -u "$(whoami)" pulseaudio | head -1 || echo "")
if [ -n "$PA_PID" ]; then
    echo "$PA_PID pulseaudio" >> "$PIDFILE"
    log "  PulseAudio running (PID $PA_PID, sink verified)"
else
    log "WARNING: PulseAudio may not be running"
fi

# ─── 3. Google Chrome browser ──────────────────────────────────────────────
log "Launching Chrome → ${BROADCAST_URL}..."

# Optional HTTP/SOCKS proxy for Chrome — useful for routing traffic through a VPN.
# Set HTTP_PROXY_URL in muse-live.env to enable (e.g. "http://your-proxy-host:1080").
PROXY_ARGS=""
HTTP_PROXY_URL="${HTTP_PROXY_URL:-}"
if [ -n "$HTTP_PROXY_URL" ]; then
    if curl -sf --proxy "$HTTP_PROXY_URL" --connect-timeout 3 https://www.youtube.com -o /dev/null 2>/dev/null; then
        PROXY_ARGS="--proxy-server=${HTTP_PROXY_URL} --proxy-bypass-list=localhost,127.0.0.1"
        log "  Proxy available — routing Chrome through ${HTTP_PROXY_URL}"
    else
        log "  WARNING: Configured proxy unreachable — Chrome connecting directly"
    fi
else
    log "  No proxy configured — Chrome connecting directly"
fi

google-chrome \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --no-first-run \
    --no-default-browser-check \
    --autoplay-policy=no-user-gesture-required \
    --window-size="${RESOLUTION%%x*},${RESOLUTION##*x}" \
    --window-position=0,0 \
    --kiosk \
    --disable-infobars \
    --disable-notifications \
    --use-fake-ui-for-media-stream \
    --disable-features=TrackingProtection3pcd,ThirdPartyCookieBlocking \
    ${PROXY_ARGS} \
    "${BROADCAST_URL}" &
CHROME_PID=$!
echo "$CHROME_PID chrome" >> "$PIDFILE"
log "  Chrome running (PID $CHROME_PID)"

# Wait for page to render (Chrome needs time to load JS + connect WS)
log "  Waiting for page render..."
sleep 12

# Hide mouse cursor by moving it off-screen (no unclutter dependency needed)
xdotool mousemove 9999 9999 2>/dev/null || true
log "  Cursor hidden"

# ─── 4. FFmpeg → YouTube RTMP (with reconnect loop) ───────────────────────
log "Starting FFmpeg stream to YouTube..."

# FFmpeg runs in a retry loop — network blips don't kill the whole pipeline
FFMPEG_PID=""
ffmpeg_stream() {
    while kill -0 $XVFB_PID 2>/dev/null && kill -0 $CHROME_PID 2>/dev/null; do
        # Audio filter chain.
        # aresample: async resampling smooths PulseAudio→FFmpeg clock drift.
        #
        # Optional: set AUDIO_SLOWDOWN_PERCENT in muse-live.env (e.g. 4) to
        # slow audio by that percentage — deepens pitch for a heavier sound.
        # Leave unset or 0 for unmodified audio.
        if [ -n "${AUDIO_SLOWDOWN_PERCENT:-}" ] && [ "${AUDIO_SLOWDOWN_PERCENT}" != "0" ]; then
            ADJUSTED_RATE=$(python3 -c "print(int(48000 * (1 - ${AUDIO_SLOWDOWN_PERCENT} / 100)))")
            AUDIO_FILTER="aresample=async=1000:first_pts=0,asetrate=${ADJUSTED_RATE},aresample=48000,aformat=channel_layouts=stereo"
        else
            AUDIO_FILTER='aresample=async=1000:first_pts=0,aformat=channel_layouts=stereo'
        fi

        ffmpeg -y \
            -hide_banner -loglevel warning \
            -thread_queue_size 4096 \
            -f x11grab -video_size "${RESOLUTION}" -framerate "${FRAMERATE}" -i ":${DISPLAY_NUM}" \
            -thread_queue_size 4096 \
            -f pulse -ac 2 -i broadcast.monitor \
            -c:v libx264 -preset veryfast -tune zerolatency \
            -b:v "${VIDEO_BITRATE}" -maxrate "${VIDEO_BITRATE}" -bufsize "${BUFSIZE}" \
            -pix_fmt yuv420p \
            -g $((FRAMERATE * 2)) \
            -af "${AUDIO_FILTER}" \
            -c:a aac -b:a "${AUDIO_BITRATE}" -ar 48000 \
            -f flv "${RTMP_URL}" &
        FFMPEG_PID=$!
        echo "$FFMPEG_PID ffmpeg" >> "$PIDFILE"
        log "  FFmpeg streaming (PID $FFMPEG_PID)"

        # Wait for this FFmpeg instance to exit
        wait $FFMPEG_PID 2>/dev/null || true

        # If Xvfb or Chrome died, exit the loop (full restart needed)
        if ! kill -0 $XVFB_PID 2>/dev/null || ! kill -0 $CHROME_PID 2>/dev/null; then
            log "Xvfb or Chrome died — exiting FFmpeg loop"
            break
        fi

        log "FFmpeg exited — RTMP reconnect in 10s..."
        sleep 10
    done
}

ffmpeg_stream &
STREAM_PID=$!

log ""
log "======================================"
log "  DAIMON LIVE is broadcasting!"
log "  Display: :${DISPLAY_NUM} (${RESOLUTION})"
log "  Stream:  YouTube RTMP"
log "  URL:     ${BROADCAST_URL}"
log "  Bufsize: ${BUFSIZE}"
log "======================================"
log ""

# Supervision loop — check all processes every 5s
while true; do
    if ! kill -0 $XVFB_PID 2>/dev/null; then log "Xvfb died"; break; fi
    if [ -n "$PA_PID" ] && ! kill -0 "$PA_PID" 2>/dev/null; then log "PulseAudio died"; break; fi
    if ! kill -0 $CHROME_PID 2>/dev/null; then log "Chrome died"; break; fi
    if ! kill -0 $STREAM_PID 2>/dev/null; then log "Stream loop exited"; break; fi
    sleep 5
done

log "A process exited — shutting down pipeline"
