"""Integration tests for the FastAPI application endpoints.

These tests use httpx AsyncClient with an ASGITransport pointed at the real
FastAPI app object.  They require a running PostgreSQL with the play schema
(tables: videos, video_tags, tracks, track_tags, playback_log,
user_preferences, media_embeddings).

The entire module is skipped automatically when the app cannot be imported
(e.g., missing DB or google-genai package in a stripped CI environment).
Run against the Dockerised stack with:
    docker compose exec backend python -m pytest tests/ -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import httpx
    import pytest_asyncio
    from httpx import AsyncClient, ASGITransport
    from main import app
    HAS_APP = True
except Exception:
    HAS_APP = False

pytestmark = pytest.mark.skipif(
    not HAS_APP,
    reason="App requires running PostgreSQL and google-genai package",
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def client():
    """Async HTTPX client wired to the FastAPI app via ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─────────────────────────────────────────────────────────────────────────────
# Health — platform-level smoke test
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_body_has_status_ok(self, client):
        resp = await client.get("/health")
        assert resp.json().get("status") == "ok"

    @pytest.mark.asyncio
    async def test_health_body_has_model_key(self, client):
        resp = await client.get("/health")
        assert "model" in resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# Video endpoints — regression: must not break after music additions
# ─────────────────────────────────────────────────────────────────────────────

class TestVideoRegression:
    """The video feature must continue to work after music was added."""

    @pytest.mark.asyncio
    async def test_video_stats_returns_200(self, client):
        resp = await client.get("/api/videos/stats")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_video_stats_has_total_videos_key(self, client):
        resp = await client.get("/api/videos/stats")
        data = resp.json()
        assert "total_videos" in data

    @pytest.mark.asyncio
    async def test_video_stats_has_vibe_distribution(self, client):
        resp = await client.get("/api/videos/stats")
        data = resp.json()
        assert "vibe_distribution" in data

    @pytest.mark.asyncio
    async def test_video_stats_total_videos_is_int(self, client):
        resp = await client.get("/api/videos/stats")
        total = resp.json().get("total_videos", None)
        assert isinstance(total, int)


# ─────────────────────────────────────────────────────────────────────────────
# Track stats endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestTrackStats:
    @pytest.mark.asyncio
    async def test_track_stats_returns_200(self, client):
        resp = await client.get("/api/tracks/stats")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_track_stats_has_total_tracks_key(self, client):
        data = (await client.get("/api/tracks/stats")).json()
        assert "total_tracks" in data

    @pytest.mark.asyncio
    async def test_track_stats_has_by_status_key(self, client):
        data = (await client.get("/api/tracks/stats")).json()
        assert "by_status" in data

    @pytest.mark.asyncio
    async def test_track_stats_has_by_source_key(self, client):
        data = (await client.get("/api/tracks/stats")).json()
        assert "by_source" in data

    @pytest.mark.asyncio
    async def test_track_stats_total_is_int(self, client):
        data = (await client.get("/api/tracks/stats")).json()
        assert isinstance(data["total_tracks"], int)


# ─────────────────────────────────────────────────────────────────────────────
# Track library endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestTrackLibrary:
    @pytest.mark.asyncio
    async def test_library_returns_200(self, client):
        resp = await client.get("/api/tracks/library")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_library_has_tracks_key(self, client):
        data = (await client.get("/api/tracks/library")).json()
        assert "tracks" in data

    @pytest.mark.asyncio
    async def test_library_has_total_key(self, client):
        data = (await client.get("/api/tracks/library")).json()
        assert "total" in data

    @pytest.mark.asyncio
    async def test_library_has_limit_key(self, client):
        data = (await client.get("/api/tracks/library")).json()
        assert "limit" in data

    @pytest.mark.asyncio
    async def test_library_tracks_is_list(self, client):
        data = (await client.get("/api/tracks/library")).json()
        assert isinstance(data["tracks"], list)

    @pytest.mark.asyncio
    async def test_library_default_limit_respected(self, client):
        """Default limit is 50; response must not exceed it."""
        data = (await client.get("/api/tracks/library")).json()
        assert len(data["tracks"]) <= 50

    @pytest.mark.asyncio
    async def test_library_custom_limit(self, client):
        data = (await client.get("/api/tracks/library?limit=5")).json()
        assert data["limit"] == 5
        assert len(data["tracks"]) <= 5

    @pytest.mark.asyncio
    async def test_library_sort_by_title(self, client):
        resp = await client.get("/api/tracks/library?sort_by=title")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_library_sort_by_energy(self, client):
        resp = await client.get("/api/tracks/library?sort_by=energy")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_library_filter_by_source(self, client):
        resp = await client.get("/api/tracks/library?source=upload")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_library_search_query(self, client):
        resp = await client.get("/api/tracks/library?search=test")
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Single track endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTrack:
    @pytest.mark.asyncio
    async def test_nonexistent_track_returns_404(self, client):
        resp = await client.get("/api/tracks/99999999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_404_body_has_detail_key(self, client):
        data = (await client.get("/api/tracks/99999999")).json()
        assert "detail" in data


# ─────────────────────────────────────────────────────────────────────────────
# Upload endpoint — validation tests (no real file needed for 422)
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadEndpoint:
    @pytest.mark.asyncio
    async def test_upload_missing_file_returns_422(self, client):
        """POST with no multipart body → 422 Unprocessable Entity."""
        resp = await client.post("/api/tracks/upload")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_bad_extension_returns_400(self, client):
        """Uploading a .exe file must be rejected as 400."""
        resp = await client.post(
            "/api/tracks/upload",
            files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_bad_extension_error_message(self, client):
        """Error detail must mention 'Unsupported' for a bad extension."""
        resp = await client.post(
            "/api/tracks/upload",
            files={"file": ("bad.xyz", b"data", "audio/mpeg")},
        )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json().get("detail", "")


# ─────────────────────────────────────────────────────────────────────────────
# Delete endpoint — access control
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteTrack:
    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client):
        resp = await client.delete("/api/tracks/99999999")
        assert resp.status_code == 404
