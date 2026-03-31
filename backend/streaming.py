"""HLS streaming and artwork serving for music tracks.

Serves transcoded HLS playlists and segments from the /music/hls/ directory.
In production, nginx serves these directly for better performance.
These routes serve as development fallback and API-based access.
"""
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracks", tags=["streaming"])

MUSIC_ROOT = Path(os.getenv("MUSIC_STORAGE_PATH", "/music"))
HLS_DIR = MUSIC_ROOT / "hls"
ARTWORK_DIR = MUSIC_ROOT / "artwork"


@router.get("/{track_id}/stream.m3u8")
async def stream_master(track_id: int, quality: str = None):
    """Serve HLS master playlist (or specific quality variant).

    The on-disk master.m3u8 uses bare filenames (128k.m3u8) which resolve
    relative to the master's URL path. Since the master is served at
    /api/tracks/{id}/stream.m3u8, relative resolution yields
    /api/tracks/{id}/128k.m3u8 (wrong). We rewrite to stream/128k.m3u8
    so they resolve to /api/tracks/{id}/stream/128k.m3u8 (correct).
    """
    track_dir = HLS_DIR / str(track_id)

    if quality and quality in ("128k", "64k"):
        playlist = track_dir / f"{quality}.m3u8"
        if not playlist.exists():
            raise HTTPException(status_code=404, detail="Track not found or not yet transcoded")
        return FileResponse(
            playlist,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGINS", "http://localhost:8151").split(",")[0]},
        )

    master = track_dir / "master.m3u8"
    if not master.exists():
        raise HTTPException(status_code=404, detail="Track not found or not yet transcoded")

    # Rewrite variant URLs: "128k.m3u8" → "stream/128k.m3u8"
    content = master.read_text(encoding="utf-8")
    content = content.replace("128k.m3u8", "stream/128k.m3u8").replace("64k.m3u8", "stream/64k.m3u8")

    from starlette.responses import Response
    return Response(
        content=content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGINS", "http://localhost:8151").split(",")[0]},
    )


# Variant playlists MUST be registered before the catch-all segment route,
# otherwise /{track_id}/stream/{segment} matches "128k.m3u8" first.
@router.get("/{track_id}/stream/{variant}.m3u8")
async def stream_variant_playlist(track_id: int, variant: str):
    """Serve a specific quality variant playlist."""
    if variant not in ("128k", "64k"):
        raise HTTPException(status_code=400, detail="Invalid variant")

    track_dir = HLS_DIR / str(track_id)
    playlist = (track_dir / f"{variant}.m3u8").resolve()

    if not playlist.is_relative_to(track_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid playlist path")
    if not playlist.exists():
        raise HTTPException(status_code=404, detail="Variant not found")

    return FileResponse(
        playlist,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGINS", "http://localhost:8151").split(",")[0],
        },
    )


@router.get("/{track_id}/stream/{segment}")
async def stream_segment(track_id: int, segment: str):
    """Serve an individual HLS segment (.aac file).

    Segments are immutable — aggressive caching is safe.
    """
    track_dir = HLS_DIR / str(track_id)
    segment_path = (track_dir / segment).resolve()

    if not segment_path.is_relative_to(track_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid segment path")
    if segment_path.suffix != ".aac":
        raise HTTPException(status_code=400, detail="Invalid segment type")
    if not segment_path.exists():
        raise HTTPException(status_code=404, detail="Segment not found")

    # FFmpeg HLS muxer wraps AAC in MPEG-TS containers (0x47 sync byte),
    # even when segments have .aac extension. Serve as video/MP2T so
    # Safari's native HLS player accepts them.
    return FileResponse(
        segment_path,
        media_type="video/MP2T",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGINS", "http://localhost:8151").split(",")[0],
        },
    )


@router.get("/{track_id}/artwork")
async def track_artwork(track_id: int):
    """Serve album artwork for a track."""
    artwork = ARTWORK_DIR / f"{track_id}.webp"

    if not artwork.exists():
        artwork_jpg = ARTWORK_DIR / f"{track_id}.jpg"
        if artwork_jpg.exists():
            return FileResponse(
                artwork_jpg,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        raise HTTPException(status_code=404, detail="No artwork available")

    return FileResponse(
        artwork,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=86400"},
    )
