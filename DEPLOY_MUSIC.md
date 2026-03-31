# Herakles Play — Music Feature Deploy Runbook

## Prerequisites
- [ ] JAMENDO_CLIENT_ID obtained from https://developer.jamendo.com
- [ ] Server has 10GB+ free disk space for music storage
- [ ] Current containers are healthy (`docker compose ps`)

## Pre-deploy
1. Add JAMENDO_CLIENT_ID to secrets:
   ```bash
   echo 'export JAMENDO_CLIENT_ID="your-client-id"' >> .env
   source .env
   ```

2. Install frontend dependency (hls.js was added to package.json — rebuild image picks it up):
   ```bash
   cd /home/your-user/herakles-daimon
   npm install hls.js
   ```

## Deploy
3. Rebuild and restart:
   ```bash
   source .env
   docker compose build --no-cache
   docker compose up -d
   ```

4. Verify health:
   ```bash
   docker compose ps                              # All 3 healthy
   curl http://localhost:8150/health               # Backend OK
   curl http://localhost:8150/api/tracks/stats      # Music endpoints work
   curl http://localhost:8150/api/videos/stats      # Video endpoints still work
   ```

5. Run database migration (auto-runs on startup, but verify):
   ```bash
   docker compose exec backend python -c "
   import asyncio, db
   asyncio.run(db.ensure_schema())
   print('Schema OK')
   "
   ```

## Seed Music Library
6. Seed initial tracks:
   ```bash
   # Preview first
   ./scrape music seed --dry-run

   # Full seed (~200+ tracks, takes 10-20 minutes)
   ./scrape music seed

   # Verify
   ./scrape music status
   ```

## Nginx (if not already proxying HLS)
7. Include the HLS nginx config in the your-domain.com server block:
   ```bash
   # Add to /etc/nginx/sites-available/your-domain.com:
   # include /home/your-user/herakles-daimon/nginx-music.conf;
   sudo nginx -t && sudo systemctl reload nginx
   ```

## Post-deploy Verification
8. Test from browser:
   - [ ] Open your-domain.com
   - [ ] Connect to Gemini
   - [ ] Say "play some music" — Gemini should call fetch_track
   - [ ] Verify HLS audio plays
   - [ ] Skip a track (swipe left)
   - [ ] Say "show me a video" — should switch to video mode
   - [ ] Test upload: Library -> Upload -> pick a file

9. Test from Pixel 6a:
   - [ ] Open your-domain.com in Chrome
   - [ ] Install PWA ("Add to Home Screen")
   - [ ] Play music -> lock screen -> verify controls work
   - [ ] Connect Bluetooth -> verify track metadata shows on car display
   - [ ] Test `# your custom media key bindings`, ``, ``

## Rollback
If issues occur:
```bash
# Stop and revert to video-only (music tables are additive, no data loss)
docker compose down
git stash  # or checkout previous known-good commit
docker compose up -d
```

---

## New Files Summary

### Backend
- `backend/music_engine.py` — Music content engine (fetch, skip, log, library, stats)
- `backend/transcoder.py` — FFmpeg HLS transcoding pipeline
- `backend/streaming.py` — HLS streaming FastAPI router
- `backend/sources/` — Jamendo, Openverse, Incompetech API clients
- `backend/scraper/music_pipeline.py` — Music ingestion pipeline
- `backend/scraper/seed_music.py` — Initial seed script
- `backend/migrations/002_music_tables.sql` — Database migration
- `backend/tests/` — Test suite

### Frontend
- `src/components/MusicPlayer.tsx` — Full music player
- `src/components/MusicLibrary.tsx` — Library browser
- `src/components/UploadPanel.tsx` — Upload interface
- `src/components/DrivingMode.tsx` — Driving-safe interface
- `src/components/NowPlayingBar.tsx` — Persistent mini-player
- `src/components/QueueView.tsx` — Queue display
- `src/components/QualityBadge.tsx` — Quality indicator
- `src/components/InstallBanner.tsx` — PWA install prompt
- `src/components/OfflineIndicator.tsx` — Offline banner
- `src/hooks/useMediaSession.ts` — Lock screen/Bluetooth controls
- `src/hooks/useAudioQuality.ts` — Connection-aware quality
- `src/hooks/useOfflineCache.ts` — Service Worker cache management
- `src/hooks/useInstallPrompt.ts` — PWA install detection
- `public/music-sw.js` — Service Worker for offline music

---

## Config Verification Notes (S10.5 audit)

All configs were verified consistent as of 2026-03-27:

| Check | Status |
|-------|--------|
| `docker-compose.yml` — `play_music` volume mounted at `/music` | PASS |
| `docker-compose.yml` — `MUSIC_STORAGE_PATH=/music` env var | PASS |
| `docker-compose.yml` — `MAX_UPLOAD_SIZE_MB=200` matches transcoder default | PASS |
| `backend/Dockerfile` — `ffmpeg` and `chromaprint-tools` installed | PASS |
| `backend/requirements.txt` — `python-multipart` added (required for `UploadFile`) | FIXED |
| `backend/transcoder.py` — reads `MUSIC_STORAGE_PATH` env, defaults `/music` | PASS |
| `backend/streaming.py` — reads `MUSIC_STORAGE_PATH` env, router at `/api/tracks` | PASS |
| `backend/main.py` — `streaming_router` included, `music_engine` imported | PASS |
| `backend/main.py` — music tools in `SERVER_SIDE_TOOLS` set | PASS |
| `backend/main.py` — no duplicate imports or conflicting route definitions | PASS |
| `nginx-music.conf` — proxies all music routes to `:8150` | PASS |
| `package.json` — `hls.js` added as dependency | FIXED |
| `public/manifest.json` — updated with music categories and PWA shortcut | PASS |
| `src/lib/constants.ts` — `fetch_track`, `skip_track`, `queue_track` tools present | PASS |
| `src/lib/types.ts` — `TrackMeta`, `MusicState`, `TrackUpdateMessage` types present | PASS |

### Fixes Applied
1. **`backend/requirements.txt`** — Added `python-multipart>=0.0.9`. FastAPI's `File(...)` and `UploadFile` depend on this package; without it the `/api/tracks/upload` endpoint raises a 422 at runtime.
2. **`package.json`** — Added `hls.js: ^1.5.0` to dependencies. The MusicPlayer component uses HLS.js for adaptive bitrate playback; without it the frontend build would fail or fall back silently.
