"""Music ingestion pipeline: discover → download → metadata → fingerprint → tag → embed → transcode.

Parallel to the video pipeline (pipeline.py), handles CC-licensed music from
Jamendo, Openverse, and Incompetech sources.

Can be run standalone:
    python -m scraper.music_pipeline
    python -m scraper.music_pipeline --source jamendo --mood chillout --max 50
    python -m scraper.music_pipeline --source incompetech --genre ambient
    python -m scraper.music_pipeline --dry-run

Or imported and called from FastAPI:
    from scraper.music_pipeline import MusicPipeline, run_discovery
    result = await run_discovery("jamendo", mood_tags=["chillout"], max_tracks=50)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

import aiofiles

# Ensure /app is importable inside the container
sys.path.insert(0, "/app")

from security import validate_url_for_ssrf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GEMINI_MODEL = os.environ.get("GEMINI_TAGGING_MODEL", os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
# Override: never use the Live API model for tagging
if "live" in _GEMINI_MODEL.lower():
    _GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_EMBED_MODEL = "gemini-embedding-001"
_CONCURRENCY = int(os.environ.get("MUSIC_PIPELINE_CONCURRENCY", "3"))

# Cache directory mirrors the transcoder's CACHE_DIR layout
_DEFAULT_CACHE_BASE = Path(os.environ.get("MUSIC_STORAGE_PATH", "/music")) / "cache"

# ---------------------------------------------------------------------------
# Gemini Flash tagging prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a music metadata analyst. Your job is to analyse a music track's "
    "metadata and produce a structured JSON object describing it for a mood-based "
    "streaming platform. Respond ONLY with valid JSON — no markdown fences, no "
    "extra text."
)

_TAG_PROMPT_TEMPLATE = """\
Analyze this music track and provide structured tags.

Title: {title}
Artist: {artist}
Album: {album}
Duration: {duration_sec}s
Source genre tags: {source_genre_tags}
Source mood tags: {source_mood_tags}

Respond in JSON only:
{{
  "energy": <1-10, 1=very calm ambient, 10=very energetic intense>,
  "bpm_estimate": <estimated beats per minute>,
  "danceability": <1-10>,
  "acousticness": <1-10, 10=fully acoustic>,
  "vibe": "<single hyphenated descriptor like 'late-night-jazz' or 'sunday-morning-acoustic'>",
  "mood_tags": ["tag1", "tag2", "tag3"],
  "genre_tags": ["genre1", "genre2"],
  "summary": "<1-2 sentence description of the track's feel and use case>"
}}"""


# ---------------------------------------------------------------------------
# Parsing helpers (copied from tagger.py pattern)
# ---------------------------------------------------------------------------

def _parse_json_response(raw: str) -> dict:
    """Extract the JSON object from the model response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {raw[:200]}")
    return json.loads(match.group(0))


def _safe_int(val, lo: int = 1, hi: int = 10, default: int = 5) -> int:
    """Coerce to int, clamp to [lo, hi]."""
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        return default


def _safe_list(val) -> list[str]:
    """Coerce to list of strings."""
    if isinstance(val, list):
        return [str(item) for item in val if item]
    if isinstance(val, str):
        return [val] if val else []
    return []


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------

class MusicPipeline:
    """Orchestrates the full music ingestion flow.

    Stage order per track:
      discover → check_existing → download → extract_metadata
      → fingerprint → insert_db → tag → embed → transcode → mark_ready
    """

    def __init__(self, use_gemini: bool = False) -> None:
        """Initialise the pipeline.

        Args:
            use_gemini: When True, use Gemini Flash for tagging (legacy behaviour).
                        When False (default), use the local analysis pipeline
                        (audio_analyzer + ast_tagger + audio_embeddings).
        """
        self._gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        self._use_gemini = use_gemini

    # ------------------------------------------------------------------
    # Stage 1 — Discovery
    # ------------------------------------------------------------------

    async def discover(
        self,
        source: str,
        mood_tags: list[str] | None = None,
        genre: str | None = None,
        max_tracks: int = 50,
    ) -> list[dict]:
        """Discover tracks from a source.

        Args:
            source:     'jamendo', 'openverse', or 'incompetech'
            mood_tags:  Tags to search for (used as query / filter)
            genre:      Genre filter
            max_tracks: Maximum tracks to discover

        Returns:
            List of normalised track dicts from the source client.
        """
        source = source.lower().strip()
        logger.info(
            "discover: source=%s mood_tags=%s genre=%s max=%d",
            source, mood_tags, genre, max_tracks,
        )

        if source == "jamendo":
            return await self._discover_jamendo(mood_tags, genre, max_tracks)
        if source == "openverse":
            return await self._discover_openverse(mood_tags, genre, max_tracks)
        if source == "incompetech":
            return await self._discover_incompetech(mood_tags, genre, max_tracks)

        logger.error("Unknown source: %r (must be jamendo, openverse, or incompetech)", source)
        return []

    async def _discover_jamendo(
        self,
        mood_tags: list[str] | None,
        genre: str | None,
        max_tracks: int,
    ) -> list[dict]:
        from sources.jamendo import JamendoClient

        async with JamendoClient() as client:
            # Jamendo supports up to 200 results per call; split if needed
            limit = min(max_tracks, 200)
            tracks = await client.search_tracks(
                tags=mood_tags or [],
                genre=genre,
                limit=limit,
            )
        logger.info("Jamendo discover: %d tracks returned", len(tracks))
        return tracks

    async def _discover_openverse(
        self,
        mood_tags: list[str] | None,
        genre: str | None,
        max_tracks: int,
    ) -> list[dict]:
        from sources.openverse import OpenverseClient

        query_parts = list(mood_tags or [])
        if genre:
            query_parts.append(genre)
        query = " ".join(query_parts) if query_parts else "music"

        async with OpenverseClient() as client:
            tracks = await client.search_audio(query=query, limit=min(max_tracks, 500))
        logger.info("Openverse discover: %d tracks returned", len(tracks))
        return tracks

    async def _discover_incompetech(
        self,
        mood_tags: list[str] | None,
        genre: str | None,
        max_tracks: int,
    ) -> list[dict]:
        from sources.incompetech import IncompetechLoader

        async with IncompetechLoader() as loader:
            if genre:
                tracks = await loader.search_by_genre(genre, limit=max_tracks)
            elif mood_tags:
                # Search by first mood tag; union results for remaining tags
                tracks = await loader.search_by_feel(mood_tags[0], limit=max_tracks)
                seen_ids = {t["source_id"] for t in tracks}
                for tag in mood_tags[1:]:
                    extra = await loader.search_by_feel(tag, limit=max_tracks)
                    for t in extra:
                        if t["source_id"] not in seen_ids:
                            seen_ids.add(t["source_id"])
                            tracks.append(t)
                            if len(tracks) >= max_tracks:
                                break
                    if len(tracks) >= max_tracks:
                        break
            else:
                # Full catalog load (respects max_tracks)
                catalog = await loader.load_catalog()
                tracks = catalog[:max_tracks]

        logger.info("Incompetech discover: %d tracks returned", len(tracks))
        return tracks

    # ------------------------------------------------------------------
    # Stage 2 — Ingest (orchestrates remaining stages per track)
    # ------------------------------------------------------------------

    async def ingest(self, tracks: list[dict]) -> dict:
        """Ingest discovered tracks through the full pipeline.

        For each track:
        1. Check DB for existing (by source + source_id)
        2. Download audio file to /music/cache/{source}/{source_id}.mp3
        3. Extract metadata via ffprobe
        4. Fingerprint via fpcalc (if available)
        5. Check fingerprint for cross-source dedup
        6. Insert into tracks table (status = 'processing')
        7. Tag via Gemini Flash (energy, mood_tags, genre_tags, vibe, summary)
        8. Generate embedding via gemini-embedding-001
        9. Transcode to HLS
        10. Update status to 'ready'

        Returns:
            { ingested: int, skipped: int, failed: int, errors: list[str] }
        """
        result: dict = {"ingested": 0, "skipped": 0, "failed": 0, "errors": []}

        if not tracks:
            logger.info("ingest: no tracks to process")
            return result

        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def _guarded(track: dict) -> None:
            async with semaphore:
                await self._ingest_one(track, result)

        tasks = [asyncio.create_task(_guarded(t)) for t in tracks]
        await asyncio.gather(*tasks, return_exceptions=False)

        logger.info(
            "Ingest complete — ingested=%d skipped=%d failed=%d",
            result["ingested"], result["skipped"], result["failed"],
        )
        return result

    async def _ingest_one(self, track: dict, result: dict) -> None:
        """Process a single track through the full ingest pipeline."""
        source = track.get("source", "unknown")
        source_id = track.get("source_id", "")
        title = track.get("title", "Unknown")[:80]

        try:
            from db import ensure_schema, execute, fetch_one

            await ensure_schema()

            # ── 1. DB dedup by source + source_id ─────────────────────────
            if source_id:
                existing = await fetch_one(
                    "SELECT id, status FROM tracks WHERE source = $1 AND source_id = $2",
                    source, source_id,
                )
                if existing:
                    logger.debug(
                        "Skipping %r (%s/%s) — already in DB (id=%d status=%s)",
                        title, source, source_id, existing["id"], existing["status"],
                    )
                    result["skipped"] += 1
                    return

            # ── 2. Download audio ──────────────────────────────────────────
            cache_dir = _DEFAULT_CACHE_BASE / source
            cache_dir.mkdir(parents=True, exist_ok=True)

            safe_id = re.sub(r"[^\w\-]", "_", source_id or title) or "track"
            file_path = str(cache_dir / f"{safe_id}.mp3")

            downloaded = await self._download_track(track, file_path)
            if not downloaded:
                logger.warning("Could not download %r — skipping", title)
                result["failed"] += 1
                result["errors"].append(f"Download failed: {source}/{source_id} '{title}'")
                return

            # ── 3. Extract metadata via ffprobe ────────────────────────────
            from transcoder import extract_metadata
            meta = await extract_metadata(file_path)

            # Merge: source-provided metadata wins over ffprobe for title/artist
            duration_sec = meta.get("duration_sec") or track.get("duration_sec") or 0
            final_title  = track.get("title") or meta.get("title") or "Unknown"
            final_artist = track.get("artist") or meta.get("artist") or "Unknown"
            final_album  = track.get("album") or meta.get("album")

            # ── 4. Fingerprint ─────────────────────────────────────────────
            fingerprint = await self.fingerprint_track(file_path)

            # ── 5. Cross-source fingerprint dedup ─────────────────────────
            if fingerprint:
                fp_existing = await fetch_one(
                    "SELECT id FROM tracks WHERE fingerprint = $1",
                    fingerprint,
                )
                if fp_existing:
                    logger.debug(
                        "Skipping %r — fingerprint match with track id=%d",
                        title, fp_existing["id"],
                    )
                    result["skipped"] += 1
                    return

            # ── 6. Insert into tracks table ────────────────────────────────
            row = await fetch_one(
                """INSERT INTO tracks
                       (source, source_id, title, artist, album, duration_sec,
                        file_path, fingerprint, license, attribution, status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'processing')
                   ON CONFLICT (source, source_id)
                       WHERE source_id IS NOT NULL
                       DO NOTHING
                   RETURNING id""",
                source,
                source_id or None,
                final_title,
                final_artist,
                final_album,
                duration_sec or None,
                file_path,
                fingerprint,
                track.get("license") or None,
                track.get("attribution") or None,
            )

            if not row:
                # ON CONFLICT DO NOTHING — was inserted by concurrent worker
                logger.debug("Concurrent insert for %r — skipping", title)
                result["skipped"] += 1
                return

            track_id: int = row["id"]
            logger.info("Inserted track id=%d '%s' (%s/%s)", track_id, final_title, source, source_id)

            # Attach enriched fields for downstream stages
            track["_db_id"] = track_id
            track["_title"]  = final_title
            track["_artist"] = final_artist
            track["_album"]  = final_album
            track["_duration_sec"] = duration_sec

            # ── 7. Tag + embed via local analysis pipeline ─────────────────
            if not self._use_gemini:
                await self._analyze_and_tag_local(track_id, file_path, track)
            else:
                # Legacy Gemini Flash path (--gemini flag or explicit opt-in)
                if self._gemini_api_key:
                    await self.tag_track(track_id, track)
                else:
                    logger.warning("GEMINI_API_KEY not set — skipping AI tagging for track %d", track_id)

            # ── 8. Generate text embedding (Gemini) ────────────────────────
            if self._gemini_api_key:
                await self.embed_track(track_id)
            else:
                logger.debug("GEMINI_API_KEY not set — skipping text embedding for track %d", track_id)

            # ── 9. Transcode to HLS ────────────────────────────────────────
            from transcoder import transcode_to_hls
            hls_dir = await transcode_to_hls(file_path, track_id)

            if hls_dir:
                # ── 10. Mark ready ─────────────────────────────────────────
                await execute(
                    "UPDATE tracks SET hls_path = $1, status = 'ready', updated_at = NOW() WHERE id = $2",
                    hls_dir, track_id,
                )
                logger.info("Track %d '%s' → ready (HLS: %s)", track_id, final_title, hls_dir)
            else:
                await execute(
                    "UPDATE tracks SET status = 'error', updated_at = NOW() WHERE id = $1",
                    track_id,
                )
                logger.error("Transcode failed for track %d '%s'", track_id, final_title)
                result["failed"] += 1
                result["errors"].append(f"Transcode failed: track_id={track_id} '{final_title}'")
                return

            result["ingested"] += 1

        except Exception as exc:
            logger.error("Unexpected error ingesting '%s' (%s/%s): %s", title, source, source_id, exc)
            result["failed"] += 1
            result["errors"].append(f"Exception for '{title}': {exc}")

    # ------------------------------------------------------------------
    # Stage helper — download
    # ------------------------------------------------------------------

    async def _download_track(self, track: dict, file_path: str) -> bool:
        """Download a track's audio to disk, using the appropriate source client."""
        # Skip download if the file already exists (cache hit)
        if Path(file_path).exists() and Path(file_path).stat().st_size > 0:
            logger.debug("Cache hit: %s", file_path)
            return True

        source = track.get("source", "")

        if source == "jamendo":
            from sources.jamendo import JamendoClient
            async with JamendoClient() as client:
                return await client.download_track(track["source_id"], file_path)

        if source == "incompetech":
            from sources.incompetech import IncompetechLoader
            async with IncompetechLoader() as loader:
                return await loader.download_track(track, file_path)

        # Generic download via httpx for openverse and any unknown source
        audio_url = track.get("download_url") or track.get("audio_url") or ""
        if not audio_url:
            logger.warning("No audio URL for track '%s'", track.get("title", "?"))
            return False

        ssrf_err = validate_url_for_ssrf(audio_url)
        if ssrf_err:
            logger.error("SSRF blocked for track '%s': %s", track.get("title", "?"), ssrf_err)
            return False

        import httpx
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                async with client.stream("GET", audio_url) as resp:
                    resp.raise_for_status()
                    async with aiofiles.open(file_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(8192):
                            await f.write(chunk)
        except (httpx.HTTPStatusError, httpx.RequestError, OSError) as exc:
            logger.error("Download failed for '%s': %s", track.get("title", "?"), exc)
            return False

        logger.info("Downloaded '%s' → %s", track.get("title", "?"), file_path)
        return True

    # ------------------------------------------------------------------
    # Stage 7 (local) — unified local analysis pipeline
    # ------------------------------------------------------------------

    async def _analyze_and_tag_local(
        self,
        track_id: int,
        file_path: str,
        track: dict | None = None,
    ) -> dict:
        """Analyse a track using the local pipeline (no Gemini API).

        Calls analyze_track() then save_analysis() from analysis_pipeline.
        Also enriches the tracks table with the vibe derived from tags.

        Args:
            track_id:  DB primary key for the track.
            file_path: Path to the audio file on disk.
            track:     Optional pre-loaded track dict (unused, kept for API symmetry).

        Returns:
            Dict of persisted tag fields, or {} on total failure.
        """
        from analysis_pipeline import analyze_track, save_analysis  # type: ignore[import]
        from db import execute

        logger.info(
            "_analyze_and_tag_local: track_id=%d file=%s", track_id, file_path
        )

        try:
            result = await analyze_track(file_path)
        except Exception as exc:
            logger.error(
                "Local analysis raised for track_id=%d: %s", track_id, exc
            )
            return {}

        await save_analysis(track_id, result)

        # Derive a vibe label from the first genre tag (used by the music engine)
        vibe = "local-analysis"
        if result.genre_tags:
            vibe = result.genre_tags[0].replace(" ", "-").lower()

        logger.info(
            "Local analysis complete for track_id=%d: bpm=%.1f key=%s energy=%d "
            "vibe=%s errors=%s",
            track_id, result.bpm, result.key, result.energy, vibe,
            result.errors or "none",
        )

        return {
            "bpm": result.bpm,
            "musical_key": result.key,
            "camelot_code": result.camelot_code,
            "energy": result.energy,
            "brightness": result.brightness,
            "danceability": result.danceability,
            "genre_tags": result.genre_tags,
            "mood_tags": result.mood_tags,
            "instrument_tags": result.instrument_tags,
            "vibe": vibe,
            "analysis_source": result.analysis_source,
            "errors": result.errors,
        }

    # ------------------------------------------------------------------
    # Stage 7 (Gemini legacy) — Gemini Flash tagging
    # ------------------------------------------------------------------

    async def tag_track(self, track_id: int, track: dict | None = None) -> dict:
        """Tag a single track using Gemini Flash.

        Sends track metadata (title, artist, album, duration, genre/mood hints)
        to Gemini Flash and stores the result in track_tags.

        Args:
            track_id: DB primary key for the track
            track:    Optional pre-loaded track dict.  If None the record is
                      fetched from the database.

        Returns:
            Dict of parsed tag fields (also written to DB).
        """
        from db import execute, fetch_one

        # Load from DB when called standalone (e.g. re-tag)
        if track is None:
            row = await fetch_one(
                "SELECT id, title, artist, album, duration_sec, source, source_id "
                "FROM tracks WHERE id = $1",
                track_id,
            )
            if not row:
                logger.error("tag_track: track_id=%d not found", track_id)
                return {}
            track = dict(row)

        title       = track.get("_title")  or track.get("title")  or "Unknown"
        artist      = track.get("_artist") or track.get("artist") or "Unknown"
        album       = track.get("_album")  or track.get("album")  or "N/A"
        duration    = track.get("_duration_sec") or track.get("duration_sec") or 0
        genre_tags  = track.get("genre_tags")  or []
        mood_tags   = track.get("mood_tags")   or []

        prompt = _TAG_PROMPT_TEMPLATE.format(
            title=title,
            artist=artist,
            album=album or "N/A",
            duration_sec=duration,
            source_genre_tags=", ".join(genre_tags) or "none",
            source_mood_tags=", ".join(mood_tags)  or "none",
        )

        api_key = self._gemini_api_key
        if not api_key:
            logger.warning("tag_track: GEMINI_API_KEY not set — aborting tag for track %d", track_id)
            return {}

        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=api_key)
        loop   = asyncio.get_event_loop()

        try:
            response = await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=_GEMINI_MODEL,
                    contents=genai_types.Content(
                        parts=[genai_types.Part(text=prompt)]
                    ),
                    config=genai_types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        max_output_tokens=1024,
                        temperature=0.3,
                    ),
                ),
            )
        except Exception as exc:
            logger.error("Gemini API error tagging track %d '%s': %s", track_id, title, exc)
            return {}

        raw_text = response.text or ""
        try:
            parsed = _parse_json_response(raw_text)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error(
                "JSON parse error for track %d '%s': %s\nRaw: %s",
                track_id, title, exc, raw_text[:300],
            )
            return {}

        energy       = _safe_int(parsed.get("energy"))
        bpm_estimate = _safe_int(parsed.get("bpm_estimate"), lo=20, hi=300, default=120)
        danceability = _safe_int(parsed.get("danceability"))
        acousticness = _safe_int(parsed.get("acousticness"))
        vibe         = str(parsed.get("vibe") or "unknown")
        ai_mood_tags = _safe_list(parsed.get("mood_tags"))
        ai_genre_tags= _safe_list(parsed.get("genre_tags"))
        summary      = str(parsed.get("summary") or "")

        # Write to track_tags (upsert)
        await execute(
            """INSERT INTO track_tags
                   (track_id, energy, bpm, danceability, acousticness,
                    vibe, mood_tags, genre_tags, claude_summary)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (track_id) DO UPDATE SET
                   energy       = EXCLUDED.energy,
                   bpm          = EXCLUDED.bpm,
                   danceability = EXCLUDED.danceability,
                   acousticness = EXCLUDED.acousticness,
                   vibe         = EXCLUDED.vibe,
                   mood_tags    = EXCLUDED.mood_tags,
                   genre_tags   = EXCLUDED.genre_tags,
                   claude_summary = EXCLUDED.claude_summary,
                   tagged_at    = NOW()""",
            track_id, energy, bpm_estimate, danceability, acousticness,
            vibe, ai_mood_tags, ai_genre_tags, summary,
        )

        tokens = 0
        if response.usage_metadata:
            tokens = (
                (response.usage_metadata.prompt_token_count or 0)
                + (response.usage_metadata.candidates_token_count or 0)
            )

        logger.info(
            "Tagged track %d '%s': energy=%d bpm=%d vibe=%s (%d tokens)",
            track_id, title[:50], energy, bpm_estimate, vibe, tokens,
        )

        return {
            "energy": energy,
            "bpm_estimate": bpm_estimate,
            "danceability": danceability,
            "acousticness": acousticness,
            "vibe": vibe,
            "mood_tags": ai_mood_tags,
            "genre_tags": ai_genre_tags,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Stage 8 — Embedding
    # ------------------------------------------------------------------

    async def embed_track(self, track_id: int) -> bool:
        """Generate 768-dim embedding for a track.

        Creates a text description from the track's metadata + tags,
        then calls gemini-embedding-001 to get a 768-dim vector.
        Stores in media_embeddings with media_type='track'.

        Returns True on success, False on any error.
        """
        from db import execute, fetch_one

        # Fetch track + tags in one query
        row = await fetch_one(
            """SELECT t.title, t.artist,
                      tt.vibe, tt.mood_tags, tt.genre_tags, tt.claude_summary
               FROM tracks t
               LEFT JOIN track_tags tt ON tt.track_id = t.id
               WHERE t.id = $1""",
            track_id,
        )

        if not row:
            logger.error("embed_track: track_id=%d not found", track_id)
            return False

        # Build embedding text — same high-signal-first ordering as embed.py
        parts: list[str] = []
        if row["title"]:
            parts.append(f"Title: {row['title']}")
        if row["artist"]:
            parts.append(f"Artist: {row['artist']}")
        vibe = (row["vibe"] or "").strip()
        if vibe and vibe != "unknown":
            parts.append(f"Vibe: {vibe}")
        mood = row["mood_tags"] or []
        if mood:
            parts.append(f"Mood: {', '.join(mood)}")
        genre = row["genre_tags"] or []
        if genre:
            parts.append(f"Genre: {', '.join(genre)}")
        summary = (row["claude_summary"] or "").strip()
        if summary:
            parts.append(f"Summary: {summary}")

        embed_text = " | ".join(parts)
        if not embed_text.strip():
            logger.warning("embed_track: no embeddable text for track_id=%d — skipping", track_id)
            return False

        api_key = self._gemini_api_key
        if not api_key:
            logger.warning("embed_track: GEMINI_API_KEY not set — skipping track %d", track_id)
            return False

        from google import genai

        client = genai.Client(api_key=api_key)
        loop   = asyncio.get_event_loop()

        try:
            result = await loop.run_in_executor(
                None,
                lambda: client.models.embed_content(
                    model=_GEMINI_EMBED_MODEL,
                    contents=embed_text,
                    config={"output_dimensionality": 768},
                ),
            )
        except Exception as exc:
            logger.error("Gemini embed error for track_id=%d: %s", track_id, exc)
            return False

        embedding = list(result.embeddings[0].values)
        if len(embedding) != 768:
            logger.error(
                "Unexpected embedding dimension %d for track_id=%d — expected 768",
                len(embedding), track_id,
            )
            return False

        vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

        try:
            await execute(
                """INSERT INTO media_embeddings (media_type, track_id, embedding, model)
                   VALUES ('track', $1, $2::vector, $3)
                   ON CONFLICT DO NOTHING""",
                track_id, vec_literal, _GEMINI_EMBED_MODEL,
            )
        except Exception as exc:
            # Fallback: update existing row if constraint issue
            try:
                await execute(
                    """UPDATE media_embeddings
                       SET embedding = $2::vector, model = $3, created_at = NOW()
                       WHERE media_type = 'track' AND track_id = $1""",
                    track_id, vec_literal, _GEMINI_EMBED_MODEL,
                )
            except Exception as exc2:
                logger.error("DB write failed for track_id=%d embed: %s / %s", track_id, exc, exc2)
                return False

        logger.info("Embedded track_id=%d '%s'", track_id, (row["title"] or "")[:50])
        return True

    # ------------------------------------------------------------------
    # Stage 4 — Fingerprinting
    # ------------------------------------------------------------------

    async def fingerprint_track(self, file_path: str) -> str | None:
        """Generate Chromaprint fingerprint for dedup.

        Uses fpcalc (Chromaprint CLI).  Returns fingerprint string or
        None if fpcalc is not available or the file cannot be analysed.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "fpcalc", "-raw", file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                for line in stdout.decode().splitlines():
                    if line.startswith("FINGERPRINT="):
                        return line.split("=", 1)[1]
        except FileNotFoundError:
            logger.debug("fpcalc not available — skipping fingerprint for %s", file_path)
        return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def status(self) -> dict:
        """Get pipeline status: counts by stage/status, recent errors, etc."""
        from db import ensure_schema, fetch_all, fetch_one

        await ensure_schema()

        counts_row = await fetch_one(
            """SELECT
                   COUNT(*) FILTER (WHERE status = 'processing') AS processing,
                   COUNT(*) FILTER (WHERE status = 'ready')      AS ready,
                   COUNT(*) FILTER (WHERE status = 'error')      AS error,
                   COUNT(*) AS total
               FROM tracks"""
        )

        tagged_row = await fetch_one(
            "SELECT COUNT(DISTINCT track_id) AS tagged FROM track_tags"
        )

        embedded_row = await fetch_one(
            "SELECT COUNT(*) AS embedded FROM media_embeddings WHERE media_type = 'track'"
        )

        sources_rows = await fetch_all(
            "SELECT source, COUNT(*) AS cnt FROM tracks GROUP BY source ORDER BY cnt DESC"
        )

        return {
            "total":      int(counts_row["total"])      if counts_row else 0,
            "ready":      int(counts_row["ready"])      if counts_row else 0,
            "processing": int(counts_row["processing"]) if counts_row else 0,
            "error":      int(counts_row["error"])      if counts_row else 0,
            "tagged":     int(tagged_row["tagged"])     if tagged_row else 0,
            "embedded":   int(embedded_row["embedded"]) if embedded_row else 0,
            "by_source":  {r["source"]: int(r["cnt"]) for r in sources_rows},
        }


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

async def run_discovery(
    source: str,
    mood_tags: list[str] | None = None,
    genre: str | None = None,
    max_tracks: int = 50,
    use_gemini: bool = False,
) -> dict:
    """Convenience function: discover + ingest in one call.

    Args:
        source:      'jamendo', 'openverse', or 'incompetech'
        mood_tags:   Tags to search for
        genre:       Genre filter
        max_tracks:  Maximum tracks to discover
        use_gemini:  When True, use Gemini Flash for tagging (legacy).
                     When False (default), use local analysis pipeline.

    Example::

        result = await run_discovery("jamendo", mood_tags=["chillout"], max_tracks=50)
        result = await run_discovery("jamendo", mood_tags=["chillout"], use_gemini=True)
    """
    pipeline = MusicPipeline(use_gemini=use_gemini)
    tracks = await pipeline.discover(source, mood_tags, genre, max_tracks)
    return await pipeline.ingest(tracks)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Herakles Play — music ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default run: Jamendo chillout/ambient/jazz + Incompetech full catalog
  python -m scraper.music_pipeline

  # Single source, specific mood
  python -m scraper.music_pipeline --source jamendo --mood chillout ambient --max 50

  # Incompetech by genre
  python -m scraper.music_pipeline --source incompetech --genre ambient --max 100

  # Openverse search
  python -m scraper.music_pipeline --source openverse --mood "lofi hip hop" --max 20

  # Dry-run (discover only, no download/DB)
  python -m scraper.music_pipeline --dry-run

  # Pipeline status
  python -m scraper.music_pipeline --status

  # Verbose output
  python -m scraper.music_pipeline --verbose
""",
    )
    p.add_argument(
        "--source",
        choices=["jamendo", "openverse", "incompetech"],
        default=None,
        help="Source to ingest (default: run all default sources)",
    )
    p.add_argument(
        "--mood",
        nargs="+",
        metavar="TAG",
        dest="mood_tags",
        help="Mood/tag filter(s)",
    )
    p.add_argument(
        "--genre",
        metavar="GENRE",
        default=None,
        help="Genre filter",
    )
    p.add_argument(
        "--max",
        type=int,
        default=50,
        metavar="N",
        dest="max_tracks",
        help="Max tracks per source (default: 50)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover tracks but skip download, tagging, and DB writes",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="Print current pipeline status and exit",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )

    # Analysis backend
    backend_group = p.add_mutually_exclusive_group()
    backend_group.add_argument(
        "--local",
        action="store_true",
        help="Use local analysis pipeline for tagging (default: local is used unless --gemini specified)",
    )
    backend_group.add_argument(
        "--gemini",
        action="store_true",
        help="Use Gemini Flash API for tagging (legacy behaviour, requires GEMINI_API_KEY)",
    )

    return p


async def _main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    # --local is default when neither flag is given; --gemini opts in to legacy
    use_gemini = bool(getattr(args, "gemini", False))
    pipeline = MusicPipeline(use_gemini=use_gemini)
    logger.info("Analysis backend: %s", "gemini" if use_gemini else "local")

    # ── Status mode ──────────────────────────────────────────────────────────
    if args.status:
        st = await pipeline.status()
        print(f"\n{'='*60}")
        print(f"  Music pipeline status")
        print(f"  Total tracks : {st['total']}")
        print(f"  Ready        : {st['ready']}")
        print(f"  Processing   : {st['processing']}")
        print(f"  Error        : {st['error']}")
        print(f"  Tagged       : {st['tagged']}")
        print(f"  Embedded     : {st['embedded']}")
        if st["by_source"]:
            print(f"  By source    :")
            for src, cnt in st["by_source"].items():
                print(f"    {src:15s} {cnt}")
        print(f"{'='*60}\n")
        return 0

    # ── Discovery jobs ───────────────────────────────────────────────────────
    if args.source:
        # Single source specified by user
        jobs = [(args.source, args.mood_tags, args.genre, args.max_tracks)]
    else:
        # Default multi-source run when no --source given
        jobs = [
            ("jamendo",     ["chillout"],           None,      50),
            ("jamendo",     ["ambient"],             None,      50),
            ("jamendo",     ["jazz"],                None,      50),
            ("incompetech", None,                    None,      500),  # full catalog
        ]

    overall = {"ingested": 0, "skipped": 0, "failed": 0, "errors": []}

    for (src, mood, genre, max_t) in jobs:
        logger.info("--- Job: source=%s mood=%s genre=%s max=%d", src, mood, genre, max_t)

        tracks = await pipeline.discover(src, mood, genre, max_t)
        if not tracks:
            logger.warning("No tracks discovered for %s/%s/%s", src, mood, genre)
            continue

        logger.info("%d track(s) discovered from %s", len(tracks), src)

        if args.dry_run:
            for t in tracks[:5]:
                logger.info(
                    "[DRY RUN] Would ingest: [%s] %s — %s",
                    t.get("source_id", "?"),
                    t.get("title", "?")[:60],
                    t.get("artist", "?")[:40],
                )
            if len(tracks) > 5:
                logger.info("[DRY RUN] ... and %d more", len(tracks) - 5)
            continue

        result = await pipeline.ingest(tracks)
        overall["ingested"] += result["ingested"]
        overall["skipped"]  += result["skipped"]
        overall["failed"]   += result["failed"]
        overall["errors"].extend(result["errors"])

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Music pipeline complete")
    print(f"  Ingested : {overall['ingested']}")
    print(f"  Skipped  : {overall['skipped']}")
    print(f"  Failed   : {overall['failed']}")
    if overall["errors"]:
        print(f"  Errors ({len(overall['errors'])}):")
        for err in overall["errors"][:10]:
            print(f"    • {err}")
        if len(overall["errors"]) > 10:
            print(f"    ... ({len(overall['errors']) - 10} more)")
    print(f"{'='*60}\n")

    return 0 if overall["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
