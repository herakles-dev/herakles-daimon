"""Openverse API client — unified CC-licensed audio search.

Aggregates results from Jamendo, Freesound, Wikimedia Commons, and others
under a single search interface.

API docs: https://api.openverse.org/v1/
No API key required for basic (unauthenticated) use; rate-limited to
~100 req/day unauthenticated.  Set OPENVERSE_ACCESS_TOKEN for higher limits.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENVERSE_BASE = "https://api.openverse.org/v1"

# Default license filter covers all permissive CC licenses + public domain
_DEFAULT_LICENSE_TYPES = "by,by-sa,by-nc,by-nc-sa,cc0,pdm"

# Known Openverse audio sources (non-exhaustive — used for validation warnings)
_KNOWN_SOURCES = frozenset({
    "jamendo",
    "freesound",
    "wikimedia_audio",
    "ccmixter",
    "soundcloud",
})


class OpenverseClient:
    """Async client for the Openverse v1 audio API.

    Usage::

        async with OpenverseClient() as client:
            results = await client.search_audio("relaxing ambient", limit=10)

    An optional ``access_token`` (or the ``OPENVERSE_ACCESS_TOKEN`` env var)
    enables authenticated requests with a higher rate limit.
    """

    def __init__(
        self,
        access_token: str | None = None,
    ) -> None:
        self._access_token: str = (
            access_token or os.getenv("OPENVERSE_ACCESS_TOKEN", "")
        )
        self._client: Optional[httpx.AsyncClient] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Context-manager support
    # ─────────────────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "OpenverseClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it on first call."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self._access_token:
                headers["Authorization"] = f"Bearer {self._access_token}"
            self._client = httpx.AsyncClient(timeout=30.0, headers=headers)
        return self._client

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def search_audio(
        self,
        query: str,
        license_type: str = _DEFAULT_LICENSE_TYPES,
        source: str | None = None,
        limit: int = 20,
        page: int = 1,
    ) -> list[dict]:
        """Search for CC-licensed audio across all Openverse sources.

        Args:
            query: Free-text search query (mood, genre, keyword, artist name).
            license_type: Comma-separated CC license type slugs.  Defaults to
                all permissive licenses + public domain.
            source: Restrict results to a specific upstream source, e.g.
                ``"jamendo"``, ``"freesound"``, ``"wikimedia_audio"``.
            limit: Number of results per page (1–500).
            page: Page number for pagination (1-based).

        Returns:
            List of normalised track dicts (see :meth:`_normalize`).
        """
        if source and source not in _KNOWN_SOURCES:
            logger.warning(
                "Openverse source '%s' is not in the known-sources list — "
                "proceeding anyway, but results may be empty",
                source,
            )

        client = await self._get_client()
        params: dict = {
            "q": query,
            "license_type": license_type,
            "page_size": min(max(1, limit), 500),
            "page": max(1, page),
        }
        if source:
            params["source"] = source

        try:
            resp = await client.get(f"{OPENVERSE_BASE}/audio/", params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Openverse /audio/ request failed: %s %s",
                exc.response.status_code, exc,
            )
            return []
        except httpx.RequestError as exc:
            logger.error("Openverse /audio/ network error: %s", exc)
            return []

        data = resp.json()
        results = data.get("results", [])
        logger.info(
            "Openverse search_audio(query=%r source=%s) → %d/%d results (page %d)",
            query, source, len(results), data.get("count", "?"), page,
        )
        return [self._normalize(r) for r in results]

    async def get_audio(self, audio_id: str) -> dict | None:
        """Get full metadata for a specific Openverse audio item.

        Args:
            audio_id: The Openverse UUID for the audio item.

        Returns:
            Normalised track dict, or ``None`` if not found or on error.
        """
        client = await self._get_client()
        try:
            resp = await client.get(f"{OPENVERSE_BASE}/audio/{audio_id}/")
            if resp.status_code == 404:
                logger.debug("Openverse get_audio(%s): not found", audio_id)
                return None
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Openverse get_audio(%s) failed: %s %s",
                audio_id, exc.response.status_code, exc,
            )
            return None
        except httpx.RequestError as exc:
            logger.error("Openverse get_audio(%s) network error: %s", audio_id, exc)
            return None

        return self._normalize(resp.json())

    # ─────────────────────────────────────────────────────────────────────────
    # Normalisation
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize(self, raw: dict) -> dict:
        """Normalise an Openverse API response dict to our internal format.

        ``duration`` in the Openverse API is milliseconds (integer).
        ``tags`` is a list of ``{"name": str}`` objects.
        """
        tags: list[str] = [
            t["name"]
            for t in (raw.get("tags") or [])
            if isinstance(t, dict) and t.get("name")
        ]

        # Openverse duration is in milliseconds; convert to whole seconds
        raw_duration = raw.get("duration")
        duration_sec: int | None = None
        if raw_duration is not None:
            try:
                duration_sec = int(raw_duration) // 1000
            except (TypeError, ValueError):
                pass

        return {
            "source": "openverse",
            "source_id": str(raw.get("id") or ""),
            "title": raw.get("title") or "Unknown",
            "artist": raw.get("creator") or "Unknown",
            "album": None,
            "duration_sec": duration_sec,
            "audio_url": raw.get("url") or "",
            "download_url": raw.get("url") or "",
            "artwork_url": raw.get("thumbnail") or "",
            "license": raw.get("license") or "",
            "license_version": raw.get("license_version") or "",
            "attribution": raw.get("attribution") or "",
            # The upstream provider (jamendo, freesound, etc.)
            "upstream_source": raw.get("source") or "",
            # Openverse audio does not carry structured genre fields —
            # use the full tag list as mood/genre hints instead
            "genre_tags": [],
            "mood_tags": tags,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying HTTP client and release connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("OpenverseClient HTTP client closed")
