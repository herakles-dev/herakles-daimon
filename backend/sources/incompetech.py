"""Incompetech (Kevin MacLeod) catalog loader.

Kevin MacLeod's ~2,000 royalty-free orchestral, ambient, and cinematic tracks.
License: CC BY 4.0 — attribution is required in any production use.

The official JSON catalog endpoint is not always stable; this module tries
the endpoint first and falls back gracefully to the local cache or an empty
catalog so the rest of the pipeline is never blocked.

Cache location: /music/cache/incompetech/catalog.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import aiofiles
import httpx

from security import validate_url_for_ssrf

logger = logging.getLogger(__name__)

CATALOG_URL = "https://incompetech.com/music/royalty-free/pieces.json"
CACHE_PATH = Path("/music/cache/incompetech/catalog.json")

# ---------------------------------------------------------------------------
# Mood / feel mapping
# ---------------------------------------------------------------------------

# Maps Incompetech "feel" field values → our normalised mood_tags list.
# Keys are matched case-insensitively as substrings of the feel field.
FEEL_TO_MOOD: dict[str, list[str]] = {
    "Dark":          ["dark", "mysterious", "brooding"],
    "Happy":         ["happy", "upbeat", "cheerful"],
    "Sad":           ["melancholic", "sad", "emotional"],
    "Epic":          ["epic", "cinematic", "powerful"],
    "Calm":          ["calm", "peaceful", "relaxing"],
    "Angry":         ["intense", "aggressive", "driving"],
    "Romantic":      ["romantic", "tender", "intimate"],
    "Suspenseful":   ["suspenseful", "tense", "thriller"],
    "Funny":         ["playful", "quirky", "lighthearted"],
    "Mysterious":    ["mysterious", "ambient", "ethereal"],
    "Inspirational": ["inspirational", "uplifting", "motivational"],
    "Grooving":      ["groovy", "funky", "rhythmic"],
}


class IncompetechLoader:
    """Async loader for the Kevin MacLeod (Incompetech) music catalog.

    The catalog is fetched once from the upstream JSON endpoint and cached
    to ``CACHE_PATH``.  Subsequent calls to :meth:`load_catalog` use the
    cached file unless ``force_refresh=True`` is passed.

    Usage::

        loader = IncompetechLoader()
        tracks = await loader.load_catalog()
        ambient = await loader.search_by_feel("Calm", limit=5)

    Because the upstream endpoint can be unreliable, all network failures
    are logged and the method returns whatever cached data is available
    (or an empty list if no cache exists).
    """

    def __init__(self) -> None:
        self._catalog: list[dict] | None = None
        self._client: Optional[httpx.AsyncClient] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Context-manager support
    # ─────────────────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "IncompetechLoader":
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

    def _load_from_cache(self) -> list[dict] | None:
        """Try to load the catalog from the local cache file.

        Returns the normalised list on success, or ``None`` if the cache
        does not exist or is corrupted.
        """
        if not CACHE_PATH.exists():
            return None
        try:
            raw: list = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            tracks = [self._normalize(t) for t in raw]
            logger.info(
                "Incompetech: loaded %d tracks from cache (%s)", len(tracks), CACHE_PATH
            )
            return tracks
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Incompetech cache corrupted (%s) — will fetch fresh", exc)
            return None

    def _save_to_cache(self, raw: list) -> None:
        """Persist the raw catalog JSON to disk, creating parent dirs."""
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.debug("Incompetech catalog cached to %s", CACHE_PATH)
        except OSError as exc:
            logger.warning("Could not write Incompetech cache: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def load_catalog(self, force_refresh: bool = False) -> list[dict]:
        """Load the full Incompetech catalog, using the local cache when possible.

        Fetch order:
        1. In-memory cache (if already loaded and ``force_refresh`` is False)
        2. Disk cache at ``CACHE_PATH`` (if present and ``force_refresh`` is False)
        3. Live fetch from ``CATALOG_URL``

        On any network / parse error the method returns whatever data is
        available (disk cache, then empty list) rather than raising.

        Args:
            force_refresh: Skip in-memory and disk caches and re-fetch from
                the upstream endpoint.

        Returns:
            List of normalised track dicts.
        """
        if self._catalog is not None and not force_refresh:
            return self._catalog

        # Try disk cache first (unless forced)
        if not force_refresh:
            cached = self._load_from_cache()
            if cached is not None:
                self._catalog = cached
                return self._catalog

        # Fetch from the upstream endpoint
        client = await self._get_client()
        raw: list | None = None
        try:
            resp = await client.get(CATALOG_URL)
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raise ValueError(f"Expected JSON array, got {type(raw).__name__}")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "Incompetech catalog fetch failed (%s) — "
                "falling back to disk cache or empty catalog",
                exc,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "Incompetech catalog JSON parse error (%s) — "
                "falling back to disk cache or empty catalog",
                exc,
            )

        if raw is not None:
            self._save_to_cache(raw)
            self._catalog = [self._normalize(t) for t in raw]
            logger.info(
                "Incompetech: fetched %d tracks from upstream", len(self._catalog)
            )
            return self._catalog

        # Network failed — try disk cache as last resort even on force_refresh
        if force_refresh:
            cached = self._load_from_cache()
            if cached is not None:
                self._catalog = cached
                return self._catalog

        logger.warning(
            "Incompetech catalog unavailable — returning empty list. "
            "You can manually place a catalog.json at %s",
            CACHE_PATH,
        )
        self._catalog = []
        return self._catalog

    async def search_by_feel(self, feel: str, limit: int = 20) -> list[dict]:
        """Return tracks whose feel/mood matches the given string.

        Matching is case-insensitive substring search against both the raw
        ``feel`` field and the derived ``mood_tags`` list.

        Args:
            feel: Feel descriptor, e.g. ``"Calm"``, ``"Epic"``, ``"Grooving"``.
            limit: Maximum number of results to return.

        Returns:
            Up to ``limit`` normalised track dicts.
        """
        catalog = await self.load_catalog()
        feel_lower = feel.lower()
        matches = [
            t for t in catalog
            if feel_lower in (t.get("feel") or "").lower()
            or feel_lower in " ".join(t.get("mood_tags") or []).lower()
        ]
        logger.debug(
            "Incompetech search_by_feel(%r) → %d/%d matches", feel, len(matches), len(catalog)
        )
        return matches[:limit]

    async def search_by_genre(self, genre: str, limit: int = 20) -> list[dict]:
        """Return tracks whose genre_tags contain the given genre string.

        Matching is case-insensitive substring search.

        Args:
            genre: Genre name, e.g. ``"jazz"``, ``"ambient"``, ``"orchestral"``.
            limit: Maximum number of results to return.

        Returns:
            Up to ``limit`` normalised track dicts.
        """
        catalog = await self.load_catalog()
        genre_lower = genre.lower()
        matches = [
            t for t in catalog
            if genre_lower in " ".join(t.get("genre_tags") or []).lower()
        ]
        logger.debug(
            "Incompetech search_by_genre(%r) → %d/%d matches", genre, len(matches), len(catalog)
        )
        return matches[:limit]

    async def download_track(self, track: dict, output_path: str) -> bool:
        """Download an Incompetech track's MP3 to disk.

        Uses chunked streaming so large files do not fill memory.

        Args:
            track: A normalised track dict as returned by :meth:`load_catalog`.
            output_path: Absolute path to write the MP3 file.

        Returns:
            ``True`` on success, ``False`` on any error.
        """
        url = track.get("download_url") or track.get("audio_url")
        if not url:
            logger.warning(
                "download_track: no URL for track '%s'", track.get("title", "?")
            )
            return False

        ssrf_err = validate_url_for_ssrf(url)
        if ssrf_err:
            logger.error(
                "SSRF blocked for track '%s': %s", track.get("title", "?"), ssrf_err
            )
            return False

        client = await self._get_client()
        try:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(output_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(8192):
                        await f.write(chunk)
        except (httpx.HTTPStatusError, httpx.RequestError, OSError) as exc:
            logger.error(
                "download_track('%s') failed: %s", track.get("title", "?"), exc
            )
            return False

        logger.info(
            "Downloaded Incompetech track '%s' to %s", track.get("title", "?"), output_path
        )
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # Normalisation
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize(self, raw: dict) -> dict:
        """Normalise an Incompetech catalog entry to our internal track format.

        The catalog uses varying key capitalisation (``name`` / ``Name``,
        ``genre`` / ``Genre``, etc.) so we probe both variants.
        """
        # Key lookup helper — tries lowercase key first, then Title-case
        def get(key: str) -> str:
            return str(raw.get(key) or raw.get(key.capitalize()) or "")

        feel = get("feel")
        genre = get("genre")
        title = (
            raw.get("name")
            or raw.get("Name")
            or raw.get("title")
            or raw.get("Title")
            or "Unknown"
        )

        # Derive mood tags from the feel field using the mapping table
        mood_tags: list[str] = []
        for feel_key, tags in FEEL_TO_MOOD.items():
            if feel_key.lower() in feel.lower():
                mood_tags.extend(tags)
        # If no mapping matched but feel is non-empty, use it verbatim
        if not mood_tags and feel:
            mood_tags = [feel.lower()]

        genre_tags = [
            g.strip().lower()
            for g in genre.split(",")
            if g.strip()
        ] if genre else []

        # Build a stable source_id from the title (slug form)
        source_id = title.lower().replace(" ", "-")

        attribution = (
            f'"{title}" Kevin MacLeod (incompetech.com) '
            "Licensed under Creative Commons: By Attribution 4.0 License "
            "http://creativecommons.org/licenses/by/4.0/"
        )

        return {
            "source": "incompetech",
            "source_id": source_id,
            "title": title,
            "artist": "Kevin MacLeod",
            "album": None,
            "duration_sec": self._parse_duration(get("length")),
            "audio_url": self._build_download_url(raw),
            "download_url": self._build_download_url(raw),
            "artwork_url": None,
            "license": "CC BY 4.0",
            "attribution": attribution,
            # Raw feel descriptor kept for direct filtering
            "feel": feel,
            "genre_tags": genre_tags,
            "mood_tags": mood_tags,
            # BPM descriptor if present: "Slow", "Medium", "Fast"
            "tempo": get("tempo"),
        }

    @staticmethod
    def _build_download_url(raw: dict) -> str:
        """Construct the Incompetech MP3 download URL from the catalog entry."""
        # Try explicit mp3/url fields first
        url = raw.get("mp3") or raw.get("url") or ""
        if url:
            return url
        # Construct from filename field
        filename = raw.get("filename") or raw.get("Filename") or ""
        if filename:
            from urllib.parse import quote
            fname = filename.strip().rstrip("\r\n")
            return f"https://incompetech.com/music/royalty-free/mp3-royaltyfree/{quote(fname)}"
        # Last resort: construct from title
        title = raw.get("title") or raw.get("Title") or raw.get("name") or raw.get("Name") or ""
        if title:
            from urllib.parse import quote
            fname = title.strip().rstrip("\r\n") + ".mp3"
            return f"https://incompetech.com/music/royalty-free/mp3-royaltyfree/{quote(fname)}"
        return ""

    @staticmethod
    def _parse_duration(length_str: str) -> int:
        """Parse a duration string like ``"3:45"`` or ``"1:02:30"`` to seconds.

        Returns ``0`` on any parse failure rather than raising.
        """
        if not length_str or not isinstance(length_str, str):
            return 0
        parts = length_str.strip().split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            return int(parts[0])
        except ValueError:
            return 0

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying HTTP client and release connections."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            logger.debug("IncompetechLoader HTTP client closed")
