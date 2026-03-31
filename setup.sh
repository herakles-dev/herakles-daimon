#!/usr/bin/env bash
set -euo pipefail

# Herakles Daimon — First-time setup
# Usage: ./setup.sh

echo "=== Herakles Daimon Setup ==="
echo ""

# ── Prerequisites ─────────────────────────────────────────────────────────────

check_cmd() {
  command -v "$1" >/dev/null 2>&1
}

if ! check_cmd docker; then
  echo "Error: Docker is required but not installed."
  echo "  Install: https://docs.docker.com/engine/install/"
  exit 1
fi

# Prefer 'docker compose' (v2) over 'docker-compose' (v1)
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif check_cmd docker-compose; then
  COMPOSE="docker-compose"
else
  echo "Error: Docker Compose is required but not installed."
  echo "  Install: https://docs.docker.com/compose/install/"
  exit 1
fi

if ! check_cmd node; then
  echo "Warning: Node.js not found. Required for local frontend development."
  echo "  Install: https://nodejs.org (v18+)"
  echo "  (Not required if you only use Docker.)"
else
  NODE_MAJOR=$(node -e "process.stdout.write(process.version.slice(1).split('.')[0])")
  if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "Warning: Node.js v18+ required, found $(node --version)."
    echo "  Upgrade: https://nodejs.org"
  fi
fi

echo "Prerequisites: OK"
echo ""

# ── Environment Files ──────────────────────────────────────────────────────────

if [ ! -f .env ]; then
  if [ ! -f .env.example ]; then
    echo "Error: .env.example not found. Make sure you cloned the full repo."
    exit 1
  fi
  cp .env.example .env
  echo "Created .env from .env.example"
  echo ""
  echo "  IMPORTANT: Edit .env before continuing."
  echo "  Required values:"
  echo "    GEMINI_API_KEY   — get one at https://aistudio.google.com/app/apikey"
  echo "    POSTGRES_PASSWORD — choose a strong random string"
  echo "    DATABASE_URL     — update with your POSTGRES_PASSWORD"
  echo ""
  read -r -p "Press Enter after editing .env to continue, or Ctrl+C to exit... "
  echo ""
fi

# Verify the two required secrets are not still at their placeholder values
GEMINI_KEY=$(grep -E '^GEMINI_API_KEY=' .env | cut -d= -f2- | tr -d '"')
if [ -z "$GEMINI_KEY" ] || [ "$GEMINI_KEY" = "your-gemini-api-key" ]; then
  echo "Error: GEMINI_API_KEY is not set in .env."
  echo "  Get one at https://aistudio.google.com/app/apikey"
  exit 1
fi

PG_PASS=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | tr -d '"')
if [ -z "$PG_PASS" ] || [ "$PG_PASS" = "change-me-to-a-random-string" ]; then
  echo "Error: POSTGRES_PASSWORD is not set in .env."
  echo "  Choose a strong random string and also update DATABASE_URL."
  exit 1
fi

if [ -f broadcast/muse-live.env.example ] && [ ! -f broadcast/muse-live.env ]; then
  cp broadcast/muse-live.env.example broadcast/muse-live.env
  chmod 600 broadcast/muse-live.env
  echo "Created broadcast/muse-live.env from example (optional — only needed for livestream)"
fi

echo "Environment: OK"
echo ""

# ── Docker Build ───────────────────────────────────────────────────────────────

echo "Building Docker images (this takes a few minutes on first run)..."
$COMPOSE build

echo ""
echo "Starting services..."
$COMPOSE up -d

# ── Health Check ───────────────────────────────────────────────────────────────

echo ""
echo "Waiting for backend to be ready..."
BACKEND_PORT=$(grep -E '^BACKEND_PORT=' .env | cut -d= -f2- | tr -d '"')
BACKEND_PORT="${BACKEND_PORT:-8150}"

MAX_WAIT=60
WAITED=0
until curl -sf "http://localhost:${BACKEND_PORT}/health" >/dev/null 2>&1; do
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    echo ""
    echo "Warning: Backend did not respond after ${MAX_WAIT}s."
    echo "  Check logs: $COMPOSE logs backend"
    break
  fi
  printf "."
  sleep 2
  WAITED=$((WAITED + 2))
done

if curl -sf "http://localhost:${BACKEND_PORT}/health" >/dev/null 2>&1; then
  echo ""
  echo "Backend: OK (http://localhost:${BACKEND_PORT}/health)"
fi

# ── Done ───────────────────────────────────────────────────────────────────────

FRONTEND_PORT=$(grep -E '^FRONTEND_PORT=' .env | cut -d= -f2- | tr -d '"')
FRONTEND_PORT="${FRONTEND_PORT:-8151}"

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Open http://localhost:${FRONTEND_PORT} in your browser"
echo "  2. Click 'Start Session' and speak to Muse"
echo "  3. Seed content (optional):"
echo "       ./scrape discover 'https://www.youtube.com/@Fireship' --max 10"
echo "       ./scrape ingest"
echo "       docker compose exec backend python -m scraper.music_pipeline"
echo ""
echo "Useful commands:"
echo "  $COMPOSE ps                  # Check service status"
echo "  $COMPOSE logs -f backend     # Follow backend logs"
echo "  $COMPOSE restart backend     # Apply Python changes"
echo ""
echo "Using Claude Code? CLAUDE.md has all the context — just open the project."
