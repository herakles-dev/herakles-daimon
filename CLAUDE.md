# Herakles Daimon

**Version:** 0.1.0 | **Frontend:** localhost:8151 | **Backend:** localhost:8150
**Stack:** Next.js 15 + React 19 + TypeScript + FastAPI + PostgreSQL + pgvector + Gemini Live API

## What

AI-curated, mood-responsive media platform. A backend WebSocket proxy bridges the browser to Gemini Multimodal Live API — the AI host talks to users in real time, reads their mood via voice, and calls 14 server-side tools to serve music and video. Two modes: **Muse** (personal, `/`) and **Daimon** (YouTube broadcast, `/broadcast`).

## Quick Start

```bash
./setup.sh                              # First-time setup
docker compose up -d                    # Start all 3 services
docker compose logs -f backend          # Follow logs
curl http://localhost:8150/health       # Verify backend
```

Open http://localhost:8151, click "Start Session", and speak to Muse.

## Commands

### Docker
```bash
docker compose up -d --build            # Build + start
docker compose ps                       # Check status
docker compose logs -f backend          # Backend logs
docker compose restart backend          # Apply Python changes
docker compose exec backend bash        # Shell into backend
```

### Frontend (dev mode)
```bash
npm install                             # Install deps
npm run dev                             # Dev server with Turbopack (http://localhost:3000)
npm run build                           # Production build
npm run lint                            # ESLint
```

### Backend (local, no Docker)
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8150
```

### Testing
```bash
cd backend
pytest                                  # All tests
pytest tests/test_music_engine.py       # Single file
pytest -v --tb=short                    # Verbose
bash tests/run_tests.sh                 # Full test suite script
```

### Content Seeding
```bash
./scrape discover "https://www.youtube.com/@Fireship" --max 10   # Discover channel
./scrape ingest                                                    # Store to DB
docker compose exec backend python -m scraper.music_pipeline      # Music (Jamendo)
docker compose exec backend python -m scraper.retag               # Tag with Gemini Flash
docker compose exec backend python -m scraper.embed               # Generate embeddings
```

## Architecture

```
Browser (Next.js 15)
  ├── useGeminiLive          Main hook: WS + audio + tools + music state + reconnect memory
  ├── useAudioCapture        Mic → PCM16 16kHz → base64 → WS (AudioWorklet)
  ├── useAudioPlayback       base64 → PCM16 → VoiceEffectsChain → AudioContext → speakers
  ├── VideoPlayer            YouTube IFrame Player API (reuses player, no black flash)
  ├── MusicPlayer            hls.js HLS streaming + waveform + album art
  └── GeminiOverlay          HUD: orb, mic toggle, transcript, settings

FastAPI Backend (:8150)
  ├── /ws/gemini             Bidirectional proxy to Gemini Multimodal Live API
  ├── _execute_server_tool() Intercepts 14 Gemini tool calls; browser never touches DB
  ├── video_discovery.py     YouTube Data API search + yt-dlp fallback
  ├── music_engine.py        Tag + vector + harmonic search for tracks
  ├── content_engine.py      Video fetch, skip logging, preference learning
  ├── streaming.py           HLS playlists + MPEG-TS segments
  └── transcoder.py          FFmpeg → 128k + 64k AAC adaptive bitrate

PostgreSQL + pgvector (:5432 internal)
  ├── videos + video_tags + video_embeddings (768-dim)
  ├── tracks + track_tags + media_embeddings
  └── playback_log (skip/complete tracking for preference learning)
```

## Key Files

| File | Purpose |
|------|---------|
| `backend/main.py` | WS proxy, all 14 tool handlers, `_inject_text()`, `_sanitize_for_gemini()`, REST API |
| `backend/schema.sql` | All tables + pgvector HNSW indexes (auto-applied on first startup) |
| `src/hooks/useGeminiLive.ts` | Main brain: WS + audio pipeline + tool execution + music state |
| `src/lib/gemini-ws.ts` | WebSocket manager: connect, setup message, reconnect, send/interrupt |
| `src/lib/constants.ts` | Tool schemas, voice list, default settings, DAIMON_PRESETS (7 presets) |
| `src/lib/types.ts` | All TypeScript types: WS protocol, app state, MuseSettings, VoiceEffects |
| `src/lib/voice-effects.ts` | VoiceEffectsChain: EQ → reverb → delay → compressor, bypass crossfade |
| `src/context/GeminiProvider.tsx` | React context exposing all hook state + actions to component tree |
| `src/app/page.tsx` | Main layout: routes contentMode + uiMode, mounts panels |
| `backend/scraper/seeds/channels.yml` | YouTube channel seeds (user-configured, 11 categories) |
| `GEMINI_VOICE.md` | Full Gemini Live API reference |

## Adding a New Gemini Tool

1. Add tool schema to `TOOL_DECLARATIONS` in `src/lib/constants.ts`
2. Add tool name to `SERVER_SIDE_TOOLS` set in `backend/main.py`
3. Add handler in `_execute_server_tool()` in `backend/main.py` — return a dict
4. For async tools: return immediately, run work in `asyncio.create_task()`, inject results via `_inject_text()`, then clear `_turn_complete` gate
5. For broadcast-only exclusion: add name to `NON_BROADCAST_TOOLS` set

## Adding a New UI Component

1. Create in `src/components/`
2. Wire state via `src/context/GeminiProvider.tsx`
3. Mount in `src/app/page.tsx` based on `contentMode` or `uiMode`
4. Broadcast-only components: add to `src/app/broadcast/page.tsx`

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | Yes | — | Gemini API key (aistudio.google.com) |
| `POSTGRES_PASSWORD` | Yes | — | PostgreSQL password |
| `DATABASE_URL` | Yes | — | Full asyncpg connection URL |
| `GEMINI_MODEL` | No | `gemini-3.1-flash-live-preview` | Gemini Live model |
| `NEXT_PUBLIC_WS_URL` | No | `ws://localhost:8150/ws/gemini` | WS URL for browser |
| `NEXT_PUBLIC_APP_ORIGIN` | No | `http://localhost:8151` | YouTube iframe origin |
| `CORS_ORIGINS` | No | `http://localhost:8151` | Allowed CORS origins |
| `JAMENDO_CLIENT_ID` | No | — | Jamendo API client ID (music discovery) |
| `MAX_UPLOAD_SIZE_MB` | No | `200` | Max single file upload size |
| `BROADCAST_SECRET` | No | — | Auth secret for broadcast API endpoints |
| `SOCKS_PROXY` | No | — | HTTP proxy for yt-dlp fallback |

See `.env.example` for the full reference including broadcast and OAuth settings.

## Critical Rules

- **Never expose `GEMINI_API_KEY` to the browser** — all Gemini traffic goes through the backend proxy
- **Audio format**: PCM16, 16kHz mono, base64-encoded over WebSocket
- **Always use hls.js** for HLS playback — never native HLS (Brave/Chrome native demuxer is broken)
- **All DB queries via asyncpg parameterized queries** — never string interpolation
- **Validate all user settings on the backend** — allowlists for enums, clamps for numbers
- **Sanitize external text** before Gemini injection via `_sanitize_for_gemini()`
- **Use `_inject_text()`** for all Gemini text injection — never raw `send_realtime_input(text=...)` alone
- **Voice effects are client-side only** — never send `VoiceEffectsConfig` to the backend
- **Read files before editing** them

## Known Issues

- Gemini Live sessions die with 1008 abort periodically — auto-reconnect handles it
- Chrome memory growth over days in broadcast mode — weekly restart recommended
- Innertube chat API can break when YouTube changes response format
