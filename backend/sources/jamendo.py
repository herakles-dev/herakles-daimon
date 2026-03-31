"""Jamendo API client for CC-licensed music discovery and streaming.

API docs: https://developer.jamendo.com/v3.0
Rate limit: 35,000 requests/month for non-commercial apps.
All tracks are Creative Commons licensed.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import aiofiles
import httpx

from security import validate_url_for_ssrf

logger = logging.getLogger(__name__)

JAMENDO_BASE = "https://api.jamendo.com/v3.0"
CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "")

# Valid audioformat values per Jamendo API docs.
# mp31 = CBR 96kbps, mp32 = VBR ~192kbps, ogg = Vorbis
_VALID_FORMATS = frozenset({"mp31", "mp32", "ogg"})

# Valid order values for /tracks endpoint
_VALID_ORDERS = frozenset({
    "popularity_total",
    "popularity_month",
    "popularity_week",
    "releasedate",
    "duration",
    "name",
    "artist_name",
    "album_name",
})


class JamendoClient:
    """Async client for the Jamendo REST API v3.0.

    Usage::

        async with JamendoClient(client_id="YOUR_ID") as client:
            tracks = await client.search_tracks(tags=["ambient"], limit=10)

    The client can also be used without a context manager by calling
    :meth:`close` explicitly when finished.
    """

    def __init__(self, client_id: str = CLIENT_ID) -> None:
        if not client_id:
            logger.warning(
                "JamendoClient created without a client_id — "
                "requests will fail until JAMENDO_CLIENT_ID is set"
            )
        self.client_id = client_id
        self._client: Optional[httpx.AsyncClient] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Context-manager support
    # ─────────────────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "JamendoClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it on first call."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _base_params(self) -> dict:
        """Return the params required on every Jamendo request."""
        return {
            "client_id": self.client_id,
            "format": "json",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def search_tracks(
        self,
        tags: list[str] | None = None,
        genre: str | None = None,
        mood: str | None = None,
        limit: int = 20,
        order: str = "popularity_total",
    ) -> list[dict]:
        """Search tracks by tags, genre, or mood.

        Args:
            tags: Jamendo tags (e.g. ``["chillout", "ambient"]``).  Multiple
                tags are ANDed together.
            genre: Genre filter (e.g. ``"jazz"``, ``"electronic"``).  Passed
                as a fuzzy-tag match so partial strings work.
            mood: Mood filter — Jamendo supports: happy, sad, romantic, etc.
                Combined with ``tags`` when both are supplied.
            limit: Max results (1–200).
            order: Sort order.  One of: ``popularity_total``, ``releasedate``,
                ``duration``, ``name``, ``artist_name``, ``album_name``.

        Returns:
            List of normalised track dicts (see :meth:`_normalize_track`).
        """
        if order not in _VALID_ORDERS:
            logger.warning(
                "Unknown Jamendo order '%s'; falling back to popularity_total", order
            )
            order = "popularity_total"

        client = await self._get_client()
        params = {
            **self._base_params(),
            "limit": min(max(1, limit), 200),
            "include": "musicinfo+stats",
            "audioformat": "mp32",
            "order": order,
        }

        # Build tag string: Jamendo uses "+" as AND separator in the tags param
        combined_tags: list[str] = list(tags or [])
        if mood:
            combined_tags.append(mood)
        if combined_tags:
            params["tags"] = "+".join(combined_tags)
        if genre:
            params["fuzzytags"] = genre

        try:
            resp = await client.get(f"{JAMENDO_BASE}/tracks", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Jamendo /tracks request failed: %s %s", exc.response.status_code, exc
            )
            return []
        except httpx.RequestError as exc:
            logger.error("Jamendo /tracks network error: %s", exc)
            return []

        results = resp.json().get("results", [])
        logger.info(
            "Jamendo search_tracks(tags=%s genre=%s mood=%s) → %d results",
            tags, genre, mood, len(results),
        )
        return [self._normalize_track(t) for t in results]

    async def get_track(self, track_id: str) -> dict | None:
        """Get full metadata for a specific Jamendo track.

        Args:
            track_id: The numeric Jamendo track ID (as string or int).

        Returns:
            Normalised track dict, or ``None`` if not found.
        """
        client = await self._get_client()
        params = {
            **self._base_params(),
            "id": str(track_id),
            "include": "musicinfo+stats",
            "audioformat": "mp32",
        }

        try:
            resp = await client.get(f"{JAMENDO_BASE}/tracks", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Jamendo get_track(%s) failed: %s %s", track_id, exc.response.status_code, exc
            )
            return None
        except httpx.RequestError as exc:
            logger.error("Jamendo get_track(%s) network error: %s", track_id, exc)
            return None

        results = resp.json().get("results", [])
        if not results:
            logger.debug("Jamendo get_track(%s): no results", track_id)
            return None
        return self._normalize_track(results[0])

    async def get_stream_url(
        self, track_id: str, format: str = "mp32"
    ) -> str | None:
        """Return a direct audio stream URL for a track.

        Args:
            track_id: Jamendo track ID.
            format: One of ``mp31`` (96 kbps CBR), ``mp32`` (VBR ~192 kbps),
                or ``ogg`` (Vorbis).  Defaults to ``mp32``.

        Returns:
            Stream URL string, or ``None`` if the track cannot be found.
        """
        if format not in _VALID_FORMATS:
            logger.warning(
                "Unknown Jamendo audio format '%s'; falling back to mp32", format
            )
            format = "mp32"

        track = await self.get_track(track_id)
        return track.get("audio_url") if track else None

    async def download_track(self, track_id: str, output_path: str) -> bool:
        """Download a track's audio file to disk.

        Uses chunked streaming so large files do not fill memory.

        Args:
            track_id: Jamendo track ID.
            output_path: Absolute path to write the MP3 file.

        Returns:
            ``True`` on success, ``False`` on any error.
        """
        track = await self.get_track(track_id)
        if not track:
            logger.warning("download_track: track %s not found", track_id)
            return False

        url = track.get("download_url") or track.get("audio_url")
        if not url:
            logger.warning("download_track: no download URL for track %s", track_id)
            return False

        ssrf_err = validate_url_for_ssrf(url)
        if ssrf_err:
            logger.error("SSRF blocked for track %s: %s", track_id, ssrf_err)
            return False

        client = await self._get_client()
        try:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(output_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(8192):
                        await f.write(chunk)
        except (httpx.HTTPStatusError, httpx.RequestError, OSError) as exc:
            logger.error("download_track(%s) failed: %s", track_id, exc)
            return False

        logger.info("Downloaded Jamendo track %s to %s", track_id, output_path)
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Normalisation
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize_track(self, raw: dict) -> dict:
        """Normalise a Jamendo API response dict to our internal track format.

        The returned dict is guaranteed to have all keys present (values may
        be empty strings/lists/None rather than absent).
        """
        musicinfo = raw.get("musicinfo") or {}
        tags_info = musicinfo.get("tags") or {}

        # Merge all Jamendo tag buckets into a flat list for general use
        all_tags: list[str] = []
        for bucket in ("genres", "instruments", "vartags"):
            all_tags.extend(tags_info.get(bucket) or [])

        return {
            "source": "jamendo",
            "source_id": str(raw.get("id", "")),
            "title": raw.get("name") or "Unknown",
            "artist": raw.get("artist_name") or "Unknown",
            "album": raw.get("album_name") or None,
            "duration_sec": int(raw.get("duration") or 0),
            "audio_url": raw.get("audio") or "",
            "download_url": raw.get("audiodownload") or "",
            "artwork_url": raw.get("image") or "",
            "license": raw.get("license_ccurl") or "CC",
            "genre_tags": [g for g in (tags_info.get("genres") or [])],
            "mood_tags": [t for t in (tags_info.get("vartags") or [])],
            "instrument_tags": [i for i in (tags_info.get("instruments") or [])],
            "all_tags": all_tags,
            # Jamendo BPM descriptor: "slow", "medium", "fast"
            "speed": musicinfo.get("speed") or "",
            "play_count": int((raw.get("stats") or {}).get("rate_listened_total") or 0),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying HTTP client and release connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("JamendoClient HTTP client closed")
