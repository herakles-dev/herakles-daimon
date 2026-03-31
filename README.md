# Herakles Daimon

AI-curated, mood-responsive media platform built with the Gemini Live API. An autonomous AI DJ and video host that talks to you, reads your mood, and picks the next video or music track — in real time, by voice.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Features

- **Voice-first AI host** — Gemini Multimodal Live API over WebSocket; the AI listens, speaks, and reacts to your mood in real time
- **Dual modes** — Muse (personal mode) and Daimon (always-on YouTube livestream)
- **14 server-side tools** — Gemini calls tools to fetch tracks, discover videos, skip content, queue up next picks, search YouTube, and read comments; the browser never touches the database
- **YouTube channel seeds** — add your subscriptions to `backend/scraper/seeds/channels.yml`; 11 categories supported (music, tech, comedy, science, journalism, and more)
- **HLS music streaming** — adaptive bitrate (128k + 64k AAC), waveform visualization, album art, gapless playback via hls.js
- **Voice effects chain** — EQ, reverb, delay, compression, pitch shift, all processed client-side in Web Audio API; 5 presets + full manual control
- **Preference learning** — skip and completion events feed a `playback_log`; Gemini Flash tags content; pgvector powers similarity search
- **Background playback** — music keeps playing with screen off via REST fallback when the WebSocket is dead
- **Bluetooth routing** — AudioContext auto-reroutes on device change; Media Session API for lock screen controls and A2DP metadata
- **PWA** — installable, offline-capable service worker, mobile landscape layout
- **Broadcast mode** — always-on YouTube livestream with Daimon as AI DJ; Super Chat tiers, chat interaction, time-aware behavior

---

## Screenshot

*Add a screenshot here.*

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Gemini API key — get one free at [aistudio.google.com](https://aistudio.google.com/app/apikey)
- Node.js 18+ (only needed for local frontend development, not for Docker)

### Run

```bash
git clone https://github.com/herakles-dev/herakles-daimon.git
cd herakles-daimon
./setup.sh
```

`setup.sh` will:

1. Check for Docker and Docker Compose
2. Copy `.env.example` to `.env` and prompt you to fill in required values
3. Build all Docker images
4. Start the 3-service stack
5. Wait for the backend health check to pass
6. Print the URL

Open `http://localhost:8151`, click "Start Session", and speak.

See [CLAUDE.md](CLAUDE.md) for detailed development commands and architecture.

---

## Architecture

```
Browser (Next.js 15, React 19)
  useGeminiLive ─── main hook: WS + audio pipeline + tool execution + reconnect memory
  useAudioCapture ─ mic → PCM16 16kHz → base64 → WebSocket (AudioWorklet)
  useAudioPlayback  base64 → PCM16 → VoiceEffectsChain → AudioContext → speakers
  VideoPlayer ───── YouTube IFrame Player API (reuses player per session, no black flash)
  MusicPlayer ───── hls.js HLS + waveform + album art
  GeminiOverlay ─── HUD: orb, mic toggle, transcript, settings, "up next" pill

FastAPI Backend (:8150)
  /ws/gemini ─────── bidirectional proxy to Gemini Multimodal Live API
  tool interception ─ 14 tools executed server-side; browser gets lightweight notification
  video_discovery ── YouTube Data API search (OAuth) + yt-dlp fallback
  music_engine ───── tag + vector + harmonic track search
  content_engine ─── video fetch, skip logging, preference learning
  streaming ──────── HLS master/variant playlists + MPEG-TS segments
  transcoder ──────── FFmpeg 128k + 64k AAC adaptive bitrate

PostgreSQL 16 + pgvector (:5432 internal)
  videos + video_tags + video_embeddings (768-dim)
  tracks + track_tags + media_embeddings
  user_preferences + playback_log
```

The browser never calls Gemini or the database directly. All Gemini traffic routes through the backend proxy, which holds the API key. Tool calls are intercepted at the proxy layer — Gemini issues a function call, the backend executes it, sends the result back to Gemini, and pushes a lightweight `videoUpdate` or `trackUpdate` message to the browser.

---

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Key settings:

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Gemini API key from aistudio.google.com |
| `POSTGRES_PASSWORD` | Yes | Database password (also update `DATABASE_URL`) |
| `DATABASE_URL` | Yes | Full asyncpg connection string |
| `NEXT_PUBLIC_WS_URL` | No | WebSocket URL for browser (`ws://localhost:8150/ws/gemini` by default) |
| `NEXT_PUBLIC_APP_ORIGIN` | No | YouTube iframe origin (`http://localhost:8151` by default) |
| `JAMENDO_CLIENT_ID` | No | Jamendo API key for CC-licensed music discovery |
| `GEMINI_MODEL` | No | Gemini Live model (`gemini-3.1-flash-live-preview` by default) |

See `.env.example` for the full reference, including broadcast, OAuth, and proxy settings.

---

## Seeding Content

The database starts empty. Seed it with videos and music before your first session:

```bash
# Discover videos from a YouTube channel
./scrape discover "https://www.youtube.com/@Fireship" --max 10
./scrape ingest

# Discover CC-licensed music from Jamendo (requires JAMENDO_CLIENT_ID)
docker compose exec backend python -m scraper.music_pipeline

# Tag all content with Gemini Flash (mood, energy, genre, etc.)
docker compose exec backend python -m scraper.retag

# Generate audio/video embeddings for similarity search
docker compose exec backend python -m scraper.embed
```

Once Muse has a session, it discovers new videos automatically via the `discover_videos` tool when you ask for something.

---

## Gemini Tools

Gemini calls these tools during a session. All are executed server-side.

| Tool | What it does |
|------|-------------|
| `fetch_video` | Get next video from DB (auto-discovers if empty) |
| `skip_video` | Log skip; auto-ban vibe after 3 skips in 24h |
| `discover_videos` | YouTube search (primary), channel browse, or category browse |
| `get_video_comments` | Fetch top YouTube comments |
| `list_channels` | List seeded channels and categories |
| `fetch_track` | Get next music track by mood, energy, genre, harmonic key |
| `skip_track` | Log skip; auto-ban vibe after 3 skips in 24h |
| `queue_track` | Add a specific track to the playback queue |
| `queue_video` | Add a video to the playback queue (Muse builds sets ahead) |
| `discover_music` | Search Jamendo for new CC tracks, download + transcode |
| `find_similar` | Find tracks similar to the current one via audio embeddings |
| `change_ui_state` | Switch fullscreen/split/overlay/music/library/driving modes |
| `update_user_profile` | Add/remove interest tags from user preferences |
| `log_playback` | Record completed playback for preference learning |

To add a new tool: add its schema to `src/lib/constants.ts`, register it in `SERVER_SIDE_TOOLS` in `backend/main.py`, and implement the handler in `_execute_server_tool()`. See [CLAUDE.md](CLAUDE.md) for step-by-step instructions.

---

## Voice Effects

Client-side Web Audio effects chain applied to Gemini's voice output. Processed entirely in the browser; the backend is not involved.

**Signal graph**: `BufferSourceNode (detune) → EQ (low/mid/high shelf) → reverb (ConvolverNode) → delay (feedback loop) → compressor → masterGain → speakers`

**Presets**: Clean, Radio (mid boost + compression), Cathedral (large reverb + delay), Warm (low boost + compression), Robot (mid scoop + heavy compression + delay).

**Bypass** uses gain crossfade — no node disconnect/reconnect during playback (prevents pops).

**Barge-in** calls `effectsChain.flush()` to hard-cut delay feedback before stopping sources, preventing reverb/delay tails from bleeding into the next response.

---

## Broadcast Mode (Daimon)

An always-on YouTube livestream where Daimon is the AI DJ. Requires a YouTube stream key and a Linux host with Xvfb and FFmpeg.

```
Xvfb :99 (1280x720) → Google Chrome (/broadcast) → FFmpeg → YouTube RTMP
YouTube Data API (OAuth) → chat_bridge.py → Daimon responds on stream
```

**Quick start:**

```bash
cp broadcast/muse-live.env.example broadcast/muse-live.env
chmod 600 broadcast/muse-live.env
# Edit muse-live.env: set YOUTUBE_STREAM_KEY and BROADCAST_SECRET
sudo systemctl start muse-live    # If using the provided systemd unit
# Or: bash broadcast/start.sh
```

**Key files:**

| File | Purpose |
|------|---------|
| `broadcast/start.sh` | Launch: Xvfb → PulseAudio → Chrome → FFmpeg RTMP loop |
| `broadcast/stop.sh` | Clean teardown |
| `broadcast/muse-live.env.example` | Config template (stream key, resolution, secret) |
| `broadcast/muse-live.service` | Systemd unit for auto-start |
| `backend/broadcast/chat_bridge.py` | YouTube chat reader; YouTube Data API + innertube fallback |
| `src/app/broadcast/page.tsx` | Daimon HUD: 1280x720 canvas, captions, now-playing card |

**Super Chat tiers:** $1 shoutout → $5 conversation → $10 video request → $25 full VIP segment.

**Daimon voice presets** (select via `?voice=name` URL param):

| Preset | Character |
|--------|-----------|
| `radio` (default) | Late night FM |
| `ghost` | Slapback delay, robotic |
| `interdimensional` | Medium reverb, spatial |
| `oracle` | Large reverb, bright |
| `void` | No reverb, intimate |
| `original` | Deep bass preset |
| `clean` | Raw voice, no processing |

---

## Channel Categories

Add your YouTube subscriptions to `backend/scraper/seeds/channels.yml`. Supported categories:

| Category | Count | Examples |
|----------|-------|---------|
| music | 44 | STS9, Skrillex, WAKAAN, Tipper, Bonobo |
| comedy | 18 | Josh Johnson, YMH Studios, Channel 5, Aunty Donna |
| tech | 17 | Low Level Learning, Tsoding, LiveOverflow, George Hotz |
| journalism | 12 | Last Podcast on the Left, bald and bankrupt, VICE |
| science | 11 | NASA, Reed Timmer, Smarter Every Day, ElectroBOOM |
| music-production | 9 | Mr. Bill, Andrew Huang, Venus Theory, Kenny Beats |
| creativity | 9 | struthless, Exurb1a, Duncan Trussell |
| making | 8 | Adam Savage, Stuff Made Here, Michael Reeves |
| action-sports | 7 | Powell Peralta, Thrasher, Vans |
| outdoors | 6 | Matthew Posa, Kraig Adams |
| finance | 2 | Benjamin Cowen, glassnode |

See `backend/scraper/seeds/channels.yml` for the format and examples.

---

## Development

```bash
# Backend changes (Python)
docker compose restart backend

# Frontend changes (Next.js) — either:
docker compose restart frontend
# Or run locally for fast iteration:
npm run dev   # http://localhost:3000

# Run tests
cd backend && pytest

# Lint frontend
npm run lint
```

See [CLAUDE.md](CLAUDE.md) for the full development reference.

---

## Using with Claude Code

This project includes a `CLAUDE.md` that gives Claude Code full context: architecture, commands, tool patterns, and rules.

```bash
claude    # Start Claude Code — it reads CLAUDE.md automatically
```

---

## Security Considerations

This project is designed for **self-hosted, single-user or trusted-network deployments**. If you expose it to the public internet, be aware of the following:

- **WebSocket authentication**: The `/ws/gemini` endpoint has no built-in authentication. Anyone who can reach it can open a Gemini session and consume your API quota. In production, place it behind a reverse proxy with authentication (e.g., OAuth2 Proxy, Caddy Security, or basic auth).
- **Upload endpoints**: `/api/tracks/upload` endpoints have no authentication. Gate them behind your reverse proxy or add an API key check.
- **Rate limiting**: No built-in rate limiting. Use your reverse proxy or add `slowapi` to the FastAPI app.
- **YouTube API usage**: This project uses the YouTube Data API (with OAuth) for video search and chat. Users are responsible for complying with YouTube's Terms of Service and configuring proper API credentials.
- **Content filtering**: YouTube searches use `safeSearch=moderate`. Adjust in `backend/video_discovery.py` if you need stricter filtering.
- **Data collection**: The backend stores playback history (what was played, skip/complete status, duration) in a `playback_log` table for preference learning. The broadcast feature processes YouTube viewer display names from live chat. Deployers in the EU are responsible for GDPR compliance including privacy notices and data retention policies.

For hardened deployments, see the security section in [CLAUDE.md](CLAUDE.md).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE)
