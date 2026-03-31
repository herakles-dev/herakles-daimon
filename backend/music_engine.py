"""
Music Engine — fetch_track, skip_track, log_track_playback, get_library, get_track_stats

All tool calls that Gemini issues for music selection are handled here,
server-side, so the browser client never touches the database.

Query strategy for fetch_track:
  1. Primary: mood_tags GIN overlap + energy window (±2) + optional genre
     filter + vibe exclusions + exclude last-30-played.
     Ordered by RANDOM() for variety.  When current_camelot is supplied,
     harmonically compatible keys are preferred via a CASE expression in
     ORDER BY (compatible = 0, non-compatible = 1) so they sort first.
  2. Fallback: ignore tags/energy entirely; exclude last-30-played and
     pick any ready track at random.
  3. Vector: if mood_tags were supplied and tiers 1+2 miss, embed the
     query via Gemini and search media_embeddings WHERE media_type='track'.
  4. Empty DB: return a structured "no music" sentinel so the caller can
     communicate gracefully rather than crash.

All functions are async and use asyncpg parameterised queries.
Never use string interpolation for user-supplied SQL values.
"""

import asyncio
import logging
import os
from db import fetch_one, fetch_all, execute

logger = logging.getLogger("play-backend.music-engine")

# ─────────────────────────────────────────────────────────────────────────────
# Camelot wheel helpers
# ─────────────────────────────────────────────────────────────────────────────

_CAMELOT_NUMBERS = set(range(1, 13))  # 1–12 inclusive
_CAMELOT_MODES = {"A", "B"}


def _parse_camelot(code: str) -> tuple[int, str] | None:
    """
    Parse a Camelot code such as "8B" or "12A" into (number, mode).

    Returns None if the code is invalid rather than raising.
    """
    if not code or len(code) < 2:
        return None
    mode = code[-1].upper()
    if mode not in _CAMELOT_MODES:
        return None
    try:
        number = int(code[:-1])
    except ValueError:
        return None
    if number not in _CAMELOT_NUMBERS:
        return None
    return (number, mode)


def get_compatible_keys(camelot_code: str) -> list[str]:
    """
    Return the list of harmonically compatible Camelot codes for *camelot_code*.

    Compatibility rules (Camelot wheel):
      - Same code (identity)
      - Neighbour -1, wrapping 1 → 12
      - Neighbour +1, wrapping 12 → 1
      - Parallel key: same number, opposite mode (A ↔ B)

    Example: "8B" → ["8B", "7B", "9B", "8A"]

    Returns an empty list if *camelot_code* is not a valid Camelot code.
    """
    parsed = _parse_camelot(camelot_code)
    if parsed is None:
        logger.warning("get_compatible_keys: invalid Camelot code %r", camelot_code)
        return []

    number, mode = parsed
    parallel_mode = "A" if mode == "B" else "B"

    prev_number = 12 if number == 1 else number - 1
    next_number = 1 if number == 12 else number + 1

    return [
        f"{number}{mode}",          # identity
        f"{prev_number}{mode}",     # -1 (wraps)
        f"{next_number}{mode}",     # +1 (wraps)
        f"{number}{parallel_mode}", # parallel key
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_track(
    mood_tags: list[str],
    energy: int,
    genre: str | None = None,
    max_duration: int | None = None,
    exclude_vibes: list[str] | None = None,
    user_id: str = "default",
    current_camelot: str | None = None,
) -> dict:
    """
    Select the best next track from the database.

    When *current_camelot* is supplied (e.g. "8B"), the primary query will
    prefer harmonically compatible tracks by ordering them first via a CASE
    expression.  Compatible keys are not required — if none exist the query
    still returns the best available result.

    Returns a dict with track metadata, or a sentinel dict if the DB is empty
    or music tables do not yet exist.  Never raises.
    """
    try:
        # 1. Resolve compatible Camelot codes when key context is provided
        compatible_keys: list[str] | None = None
        if current_camelot:
            compatible_keys = get_compatible_keys(current_camelot)
            if compatible_keys:
                logger.info(
                    "fetch_track: current_camelot=%r → preferring compatible keys %s",
                    current_camelot,
                    compatible_keys,
                )
            else:
                logger.warning(
                    "fetch_track: current_camelot=%r is not a valid Camelot code; "
                    "harmonic preference disabled",
                    current_camelot,
                )
                compatible_keys = None

        # 2. Merge caller exclusions with persisted user preferences
        user_prefs = await _get_user_prefs(user_id)
        combined_excludes = _merge_excludes(
            exclude_vibes, user_prefs.get("exclude_vibes", [])
        )

        # 3. Collect last-30 track IDs to prevent immediate repeats
        recent_ids = await _recent_track_ids(user_id, limit=30)

        # 4. Primary query: tag + energy + optional genre match
        row = await _primary_query(
            mood_tags=mood_tags,
            energy=energy,
            genre=genre,
            max_duration=max_duration,
            exclude_vibes=combined_excludes,
            exclude_ids=recent_ids,
            compatible_keys=compatible_keys,
        )

        # 5. Fallback: any unplayed ready track in random order
        if row is None:
            logger.info("Primary fetch_track query returned no rows; using fallback")
            row = await _fallback_query(
                max_duration=max_duration,
                exclude_ids=recent_ids,
            )

        # 5b. Vector search fallback: embed the mood_tags query and find the
        #     nearest track by cosine distance in media_embeddings.
        if row is None and mood_tags:
            logger.info("Tag fallback returned no rows; attempting vector search fallback")
            row = await _vector_fallback_query(
                mood_tags=mood_tags,
                exclude_ids=recent_ids,
            )
            if row is not None:
                logger.info("Vector fallback returned a result: %s", row["title"])

        # 5. Truly empty DB or missing tables
        if row is None:
            logger.warning("fetch_track: database has no tracks")
            return _no_music_sentinel()

        return _row_to_dict(row)

    except Exception:
        logger.exception("fetch_track raised an unexpected error")
        return _no_music_sentinel()


async def skip_track(
    track_id: int,
    reason: str,
    listen_duration_seconds: int,
    user_id: str = "default",
) -> dict:
    """
    Record a skip event and auto-ban a vibe if the user has skipped it 3+
    times in the last 24 hours.

    Returns { skipped: True, auto_banned_vibe: str | None }.
    Never raises.
    """
    try:
        # Resolve the vibe for this track
        tag_row = await fetch_one(
            "SELECT vibe FROM track_tags WHERE track_id = $1",
            track_id,
        )
        vibe = tag_row["vibe"] if tag_row else None

        await execute(
            """
            INSERT INTO playback_log
                (user_id, track_id, content_type, watch_duration_sec, skipped, skip_reason)
            VALUES ($1, $2, 'track', $3, TRUE, $4)
            """,
            user_id, track_id, listen_duration_seconds, reason,
        )

        auto_banned_vibe: str | None = None

        if vibe:
            skip_count_row = await fetch_one(
                """
                SELECT COUNT(*) AS cnt
                FROM playback_log pl
                JOIN track_tags tt ON tt.track_id = pl.track_id
                WHERE pl.user_id        = $1
                  AND pl.content_type   = 'track'
                  AND pl.skipped        = TRUE
                  AND tt.vibe           = $2
                  AND pl.played_at     >= NOW() - INTERVAL '1 day'
                """,
                user_id, vibe,
            )
            count = int(skip_count_row["cnt"]) if skip_count_row else 0

            if count >= 3:
                prefs = await _get_user_prefs(user_id)
                current_excludes: list[str] = prefs.get("exclude_vibes") or []
                if vibe not in current_excludes:
                    new_excludes = current_excludes + [vibe]
                    await execute(
                        """
                        INSERT INTO user_preferences (user_id, exclude_vibes, updated_at)
                        VALUES ($1, $2, NOW())
                        ON CONFLICT (user_id) DO UPDATE
                            SET exclude_vibes = $2, updated_at = NOW()
                        """,
                        user_id, new_excludes,
                    )
                    auto_banned_vibe = vibe
                    logger.info(
                        "Auto-banned vibe '%s' for user '%s' after %d track skips today",
                        vibe, user_id, count,
                    )

        return {
            "skipped": True,
            "auto_banned_vibe": auto_banned_vibe,
        }

    except Exception:
        logger.exception("skip_track raised an unexpected error")
        return {"skipped": False, "auto_banned_vibe": None, "error": "internal"}


async def log_track_playback(
    track_id: int,
    listen_duration_seconds: int,
    mood_tags_at_play: list[str],
    user_id: str = "default",
) -> dict:
    """
    Record a completed (non-skipped) track playback event.

    Returns { logged: True }.  Never raises.
    """
    try:
        await execute(
            """
            INSERT INTO playback_log
                (user_id, track_id, content_type, watch_duration_sec, skipped, mood_tags_at_play)
            VALUES ($1, $2, 'track', $3, FALSE, $4)
            """,
            user_id, track_id, listen_duration_seconds, mood_tags_at_play,
        )
        return {"logged": True}

    except Exception:
        logger.exception("log_track_playback raised an unexpected error")
        return {"logged": False, "error": "internal"}


async def get_liked_music_tags(user_id: str = "default", days: int = 7, limit: int = 20) -> list[str]:
    """Return mood/genre tags from recently completed (non-skipped) track playback."""
    try:
        rows = await fetch_all(
            """
            SELECT DISTINCT unnest(array_cat(tt.mood_tags, tt.genre_tags)) AS tag
            FROM playback_log pl
            JOIN track_tags tt ON tt.track_id = pl.track_id
            WHERE pl.user_id = $1 AND pl.skipped = FALSE
              AND pl.content_type = 'track'
              AND pl.played_at >= NOW() - make_interval(days => $2)
            LIMIT $3
            """,
            user_id, days, limit,
        )
        return [r["tag"] for r in rows if r["tag"]]
    except Exception:
        logger.exception("get_liked_music_tags failed")
        return []


async def get_library(
    user_id: str = "default",
    search: str | None = None,
    sort_by: str = "recent",
    genre: str | None = None,
    mood: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Paginated library query joining tracks + track_tags.

    sort_by accepts: 'recent', 'title', 'artist', 'duration', 'energy'.
    Returns { tracks: [...], total: int, limit: int, offset: int }.
    Never raises — returns empty result set on any error.
    """
    try:
        params: list = []
        param_idx = 1

        # Build WHERE clauses
        where_clauses: list[str] = ["t.status = 'ready'"]

        if search:
            where_clauses.append(
                f"(t.title ILIKE ${param_idx} OR t.artist ILIKE ${param_idx} OR t.album ILIKE ${param_idx})"
            )
            params.append(f"%{search}%")
            param_idx += 1

        if genre:
            where_clauses.append(f"tt.genre_tags @> ARRAY[${param_idx}]::text[]")
            params.append(genre)
            param_idx += 1

        if mood:
            where_clauses.append(f"tt.mood_tags @> ARRAY[${param_idx}]::text[]")
            params.append(mood)
            param_idx += 1

        if source:
            where_clauses.append(f"t.source = ${param_idx}")
            params.append(source)
            param_idx += 1

        where_sql = "WHERE " + " AND ".join(where_clauses)

        # Validate and map sort_by to a SQL ORDER BY expression
        sort_map = {
            "recent":   "t.created_at DESC",
            "title":    "t.title ASC",
            "artist":   "t.artist ASC",
            "duration": "t.duration_sec ASC",
            "energy":   "tt.energy DESC NULLS LAST",
        }
        # Default to 'recent' for any unrecognised sort key
        order_sql = sort_map.get(sort_by, sort_map["recent"])

        # Count query (no LIMIT/OFFSET)
        count_query = f"""
            SELECT COUNT(*) AS cnt
            FROM tracks t
            LEFT JOIN track_tags tt ON tt.track_id = t.id
            {where_sql}
        """
        count_row = await fetch_one(count_query, *params)
        total = int(count_row["cnt"]) if count_row else 0

        # Data query
        params_data = params + [limit, offset]
        limit_param = param_idx
        offset_param = param_idx + 1

        data_query = f"""
            SELECT
                t.id,
                t.title,
                t.artist,
                t.album,
                t.duration_sec,
                t.source,
                t.status,
                t.created_at,
                tt.energy,
                tt.vibe,
                tt.mood_tags,
                tt.genre_tags,
                tt.bpm,
                tt.musical_key
            FROM tracks t
            LEFT JOIN track_tags tt ON tt.track_id = t.id
            {where_sql}
            ORDER BY {order_sql}
            LIMIT ${limit_param} OFFSET ${offset_param}
        """
        rows = await fetch_all(data_query, *params_data)

        tracks = [_library_row_to_dict(r) for r in rows]
        return {
            "tracks": tracks,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    except Exception:
        logger.exception("get_library raised an unexpected error")
        return {"tracks": [], "total": 0, "limit": limit, "offset": offset}


async def get_track_stats() -> dict:
    """
    Summary statistics for the music library.

    Returns:
        {
            total: int,
            by_source: { upload: N, jamendo: N, ... },
            tagged: int,
            embedded: int,
            ready: int,
        }
    Never raises.
    """
    try:
        total_row = await fetch_one("SELECT COUNT(*) AS cnt FROM tracks")
        total = int(total_row["cnt"]) if total_row else 0

        source_rows = await fetch_all(
            "SELECT source, COUNT(*) AS cnt FROM tracks GROUP BY source"
        )
        by_source = {r["source"]: int(r["cnt"]) for r in source_rows}

        ready_row = await fetch_one(
            "SELECT COUNT(*) AS cnt FROM tracks WHERE status = 'ready'"
        )
        ready = int(ready_row["cnt"]) if ready_row else 0

        tagged_row = await fetch_one(
            "SELECT COUNT(*) AS cnt FROM track_tags"
        )
        tagged = int(tagged_row["cnt"]) if tagged_row else 0

        embedded_row = await fetch_one(
            "SELECT COUNT(*) AS cnt FROM media_embeddings WHERE media_type = 'track'"
        )
        embedded = int(embedded_row["cnt"]) if embedded_row else 0

        return {
            "total": total,
            "by_source": by_source,
            "tagged": tagged,
            "embedded": embedded,
            "ready": ready,
        }

    except Exception:
        logger.exception("get_track_stats raised an unexpected error")
        return {
            "total": 0,
            "by_source": {},
            "tagged": 0,
            "embedded": 0,
            "ready": 0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_user_prefs(user_id: str) -> dict:
    row = await fetch_one(
        "SELECT * FROM user_preferences WHERE user_id = $1", user_id
    )
    if row is None:
        return {"exclude_vibes": []}
    return dict(row)


async def _recent_track_ids(user_id: str, limit: int = 30) -> list[int]:
    rows = await fetch_all(
        """
        SELECT track_id
        FROM playback_log
        WHERE user_id = $1
          AND content_type = 'track'
          AND track_id IS NOT NULL
        ORDER BY played_at DESC
        LIMIT $2
        """,
        user_id, limit,
    )
    return [r["track_id"] for r in rows]


def _merge_excludes(
    request_excludes: list[str] | None,
    pref_excludes: list[str],
) -> list[str]:
    combined = set(pref_excludes or [])
    if request_excludes:
        combined.update(request_excludes)
    return list(combined)


async def _primary_query(
    mood_tags: list[str],
    energy: int,
    genre: str | None,
    max_duration: int | None,
    exclude_vibes: list[str],
    exclude_ids: list[int],
    compatible_keys: list[str] | None = None,
) -> "asyncpg.Record | None":
    """
    SELECT with mood_tags GIN overlap, energy window (±2), optional genre
    containment, vibe exclusion, duration cap, and history exclusion.

    When *compatible_keys* is provided, the ORDER BY clause uses a CASE
    expression to sort harmonically compatible tracks first (score 0), then
    all others (score 1).  RANDOM() is used as the tiebreaker within each
    group so variety is preserved.  A compatible key match is never required
    — this is purely a preference signal.
    """
    params: list = [mood_tags, energy]
    param_idx = 3  # next available $N

    genre_clause = ""
    if genre:
        genre_clause = f"AND tt.genre_tags @> ARRAY[${param_idx}]::text[]"
        params.append(genre)
        param_idx += 1

    duration_clause = ""
    if max_duration is not None:
        duration_clause = f"AND t.duration_sec <= ${param_idx}"
        params.append(max_duration)
        param_idx += 1

    vibe_clause = ""
    if exclude_vibes:
        vibe_clause = f"AND (tt.vibe IS NULL OR tt.vibe != ALL(${param_idx}::text[]))"
        params.append(exclude_vibes)
        param_idx += 1

    history_clause = ""
    if exclude_ids:
        history_clause = f"AND t.id != ALL(${param_idx}::int[])"
        params.append(exclude_ids)
        param_idx += 1

    # Harmonic preference: compatible tracks sort before non-compatible ones.
    # RANDOM() within each group preserves variety.
    if compatible_keys:
        camelot_param = f"${param_idx}"
        params.append(compatible_keys)
        param_idx += 1
        order_clause = (
            f"CASE WHEN tt.camelot_code = ANY({camelot_param}::text[]) THEN 0 ELSE 1 END, "
            "RANDOM()"
        )
    else:
        order_clause = "RANDOM()"

    query = f"""
        SELECT
            t.id,
            t.title,
            t.artist,
            t.album,
            t.duration_sec,
            t.source,
            tt.energy,
            tt.vibe,
            tt.mood_tags,
            tt.genre_tags,
            tt.bpm,
            tt.musical_key,
            tt.camelot_code
        FROM tracks t
        JOIN track_tags tt ON tt.track_id = t.id
        WHERE t.status = 'ready'
          AND tt.mood_tags && $1::text[]
          AND tt.energy BETWEEN ($2 - 2) AND ($2 + 2)
          {genre_clause}
          {duration_clause}
          {vibe_clause}
          {history_clause}
        ORDER BY {order_clause}
        LIMIT 1
    """
    return await fetch_one(query, *params)


async def _fallback_query(
    max_duration: int | None,
    exclude_ids: list[int],
) -> "asyncpg.Record | None":
    """
    Broad fallback: ignore tags/energy, just pick a ready track at random.
    """
    params: list = []
    param_idx = 1

    duration_clause = ""
    if max_duration is not None:
        duration_clause = f"AND t.duration_sec <= ${param_idx}"
        params.append(max_duration)
        param_idx += 1

    history_clause = ""
    if exclude_ids:
        history_clause = f"AND t.id != ALL(${param_idx}::int[])"
        params.append(exclude_ids)
        param_idx += 1

    query = f"""
        SELECT
            t.id,
            t.title,
            t.artist,
            t.album,
            t.duration_sec,
            t.source,
            tt.energy,
            tt.vibe,
            tt.mood_tags,
            tt.genre_tags,
            tt.bpm,
            tt.musical_key,
            tt.camelot_code
        FROM tracks t
        LEFT JOIN track_tags tt ON tt.track_id = t.id
        WHERE t.status = 'ready'
          {duration_clause}
          {history_clause}
        ORDER BY RANDOM()
        LIMIT 1
    """
    return await fetch_one(query, *params)


async def _vector_fallback_query(
    mood_tags: list[str],
    exclude_ids: list[int],
) -> "asyncpg.Record | None":
    """
    Semantic search fallback using pgvector cosine distance on media_embeddings
    WHERE media_type='track'.

    Constructs a query string from mood_tags, generates a Gemini embedding,
    then finds the closest track.  Returns None if embeddings are unavailable.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — cannot run vector fallback for tracks")
        return None

    query_text = "Mood: " + ", ".join(mood_tags)

    try:
        from google import genai as _genai
        client = _genai.Client(api_key=api_key)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: client.models.embed_content(
                model="models/gemini-embedding-001",
                contents=query_text,
                config={"output_dimensionality": 768},
            ),
        )
        embedding: list[float] = list(result.embeddings[0].values)
    except Exception as exc:
        logger.warning("Gemini embedding failed in track vector fallback: %s", exc)
        return None

    vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

    params: list = [vec_literal]
    param_idx = 2

    history_clause = ""
    if exclude_ids:
        history_clause = f"AND t.id != ALL(${param_idx}::int[])"
        params.append(exclude_ids)
        param_idx += 1

    query = f"""
        SELECT
            t.id,
            t.title,
            t.artist,
            t.album,
            t.duration_sec,
            t.source,
            tt.energy,
            tt.vibe,
            tt.mood_tags,
            tt.genre_tags,
            tt.bpm,
            tt.musical_key,
            tt.camelot_code
        FROM media_embeddings me
        JOIN tracks t ON t.id = me.track_id
        LEFT JOIN track_tags tt ON tt.track_id = t.id
        WHERE me.media_type = 'track'
          AND t.status = 'ready'
          {history_clause}
        ORDER BY me.embedding <=> $1::vector
        LIMIT 1
    """

    try:
        return await fetch_one(query, *params)
    except Exception as exc:
        logger.warning("Track vector fallback DB query failed: %s", exc)
        return None


async def find_similar_tracks(
    track_id: int,
    limit: int = 10,
    user_id: str = "default",
    exclude_recent: bool = True,
) -> list[dict]:
    """Find tracks that sound similar using hybrid scoring.

    Scoring: 0.6 * audio_cosine_similarity + 0.2 * energy_closeness + 0.2 * bpm_closeness

    Strategy:
    1. Fetch source track audio_embedding from media_embeddings.
    2. If no audio_embedding, fall back to the 768-dim text embedding.
    3. Query pgvector for the top N*2 candidate tracks by cosine distance.
    4. Re-rank candidates with the hybrid score.
    5. Exclude the source track and, optionally, the last-30-played tracks.

    Returns list of track dicts with similarity_score field. Never raises.
    """
    try:
        # Fetch the source track's embeddings + tags in one query
        source_row = await fetch_one(
            """
            SELECT
                me.audio_embedding,
                me.embedding,
                me.embedding_dim,
                tt.energy,
                tt.bpm
            FROM media_embeddings me
            LEFT JOIN track_tags tt ON tt.track_id = me.track_id
            WHERE me.track_id = $1
              AND me.media_type = 'track'
            """,
            track_id,
        )

        if source_row is None:
            logger.warning(
                "find_similar_tracks: no media_embeddings row for track_id=%d", track_id
            )
            return []

        # Decide which embedding to use
        audio_emb = source_row["audio_embedding"]
        text_emb = source_row["embedding"]
        using_audio = audio_emb is not None

        if using_audio:
            vec_values = list(audio_emb)
            dim = len(vec_values)
            cast = f"vector({dim})"
            distance_col = "me.audio_embedding"
            logger.debug(
                "find_similar_tracks: using audio_embedding (%d-dim) for track %d",
                dim, track_id,
            )
        elif text_emb is not None:
            vec_values = list(text_emb)
            dim = len(vec_values)
            cast = f"vector({dim})"
            distance_col = "me.embedding"
            logger.info(
                "find_similar_tracks: track %d has no audio_embedding; "
                "falling back to text embedding (%d-dim)",
                track_id, dim,
            )
        else:
            logger.warning(
                "find_similar_tracks: track %d has no audio or text embedding; "
                "returning empty",
                track_id,
            )
            return []

        source_energy: float | None = (
            float(source_row["energy"]) if source_row["energy"] is not None else None
        )
        source_bpm: float | None = (
            float(source_row["bpm"]) if source_row["bpm"] is not None else None
        )

        # IDs to skip: the source track itself + optionally recent history
        exclude_ids: list[int] = [track_id]
        if exclude_recent:
            recent = await _recent_track_ids(user_id, limit=30)
            exclude_ids.extend(r for r in recent if r != track_id)

        # Build the pgvector literal for asyncpg parameterised query
        vec_literal = "[" + ",".join(str(v) for v in vec_values) + "]"

        params: list = [vec_literal, exclude_ids, limit * 2]

        candidate_query = f"""
            SELECT
                t.id,
                t.title,
                t.artist,
                t.album,
                t.duration_sec,
                t.source,
                tt.energy,
                tt.vibe,
                tt.mood_tags,
                tt.genre_tags,
                tt.bpm,
                tt.musical_key,
                tt.camelot_code,
                ({distance_col} <=> $1::{cast}) AS cosine_distance
            FROM media_embeddings me
            JOIN tracks t ON t.id = me.track_id
            LEFT JOIN track_tags tt ON tt.track_id = t.id
            WHERE me.media_type = 'track'
              AND t.status = 'ready'
              AND t.id != ALL($2::int[])
            ORDER BY {distance_col} <=> $1::{cast}
            LIMIT $3
        """

        candidates = await fetch_all(candidate_query, *params)

        if not candidates:
            logger.info(
                "find_similar_tracks: no candidate tracks found for track_id=%d", track_id
            )
            return []

        # Re-rank with hybrid score
        results: list[dict] = []
        for row in candidates:
            cosine_distance = (
                float(row["cosine_distance"])
                if row["cosine_distance"] is not None
                else 1.0
            )
            audio_sim = 1.0 - cosine_distance

            # Energy closeness: 1 - abs(delta) / 10  (energy is 1-10)
            candidate_energy = (
                float(row["energy"]) if row["energy"] is not None else None
            )
            if source_energy is not None and candidate_energy is not None:
                energy_closeness = 1.0 - abs(source_energy - candidate_energy) / 10.0
            else:
                energy_closeness = 0.5  # neutral when data unavailable

            # BPM closeness: 1 - min(abs(delta) / 60, 1.0)
            candidate_bpm = (
                float(row["bpm"]) if row["bpm"] is not None else None
            )
            if source_bpm is not None and candidate_bpm is not None:
                bpm_closeness = 1.0 - min(abs(source_bpm - candidate_bpm) / 60.0, 1.0)
            else:
                bpm_closeness = 0.5  # neutral when data unavailable

            final_score = (
                0.6 * audio_sim
                + 0.2 * energy_closeness
                + 0.2 * bpm_closeness
            )

            track_dict = _row_to_dict(row)
            track_dict["similarity_score"] = round(final_score, 4)
            results.append(track_dict)

        # Sort by hybrid score descending, return top `limit`
        results.sort(key=lambda x: x["similarity_score"], reverse=True)
        top = results[:limit]

        logger.info(
            "find_similar_tracks: track_id=%d -> %d results (top score %.4f)",
            track_id, len(top), top[0]["similarity_score"] if top else 0.0,
        )
        return top

    except Exception:
        logger.exception(
            "find_similar_tracks raised an unexpected error for track_id=%d", track_id
        )
        return []


def _row_to_dict(row) -> dict:
    """Convert an asyncpg Record (from a tracks query) to a JSON-serialisable dict."""
    track_id = int(row["id"])
    return {
        "id": track_id,
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "duration_sec": int(row["duration_sec"]) if row["duration_sec"] is not None else None,
        "artwork_url": f"/api/tracks/{track_id}/artwork",
        "hls_url": f"/api/tracks/{track_id}/stream.m3u8",
        "energy": int(row["energy"]) if row["energy"] is not None else None,
        "vibe": row["vibe"],
        "mood_tags": list(row["mood_tags"]) if row["mood_tags"] else [],
        "genre_tags": list(row["genre_tags"]) if row["genre_tags"] else [],
        "source": row["source"],
        "camelot_code": row["camelot_code"],
    }


def _library_row_to_dict(row) -> dict:
    """Convert a library query row to a JSON-serialisable dict."""
    track_id = int(row["id"])
    return {
        "id": track_id,
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "duration_sec": int(row["duration_sec"]) if row["duration_sec"] is not None else None,
        "artwork_url": f"/api/tracks/{track_id}/artwork",
        "hls_url": f"/api/tracks/{track_id}/stream.m3u8",
        "source": row["source"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "energy": int(row["energy"]) if row["energy"] is not None else None,
        "vibe": row["vibe"],
        "mood_tags": list(row["mood_tags"]) if row["mood_tags"] else [],
        "genre_tags": list(row["genre_tags"]) if row["genre_tags"] else [],
        "bpm": int(row["bpm"]) if row["bpm"] is not None else None,
        "musical_key": row["musical_key"],
    }


def _no_music_sentinel() -> dict:
    return {
        "id": None,
        "title": "No music available",
        "artist": None,
        "album": None,
        "duration_sec": None,
        "artwork_url": None,
        "hls_url": None,
        "energy": None,
        "vibe": None,
        "mood_tags": [],
        "genre_tags": [],
        "source": None,
        "no_content": True,
        "message": "The music library is empty. Add tracks to get started.",
    }
