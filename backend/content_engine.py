"""
Content Engine — fetch_video, log_skip, log_playback

All tool calls that Gemini issues for content selection are handled here,
server-side, so the browser client never touches the database.

Query strategy for fetch_video:
  1. Primary: tag match + pacing window + vibe exclusions + duration cap
     + exclude last-20-played.  Ordered by novelty DESC + RANDOM()*0.3.
  2. Fallback: skip tag/pacing matching entirely; just exclude last-20-played
     and order randomly.  Returns whatever is in the DB.
  3. Empty DB: returns a structured "no content" sentinel dict so the caller
     can communicate gracefully rather than crash.
"""

import logging
import os
from db import fetch_one, fetch_all, execute

logger = logging.getLogger("play-backend.content-engine")

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_video(
    mood_tags: list[str],
    pacing: int,
    max_duration: int | None = None,
    exclude_vibes: list[str] | None = None,
    user_id: str = "default",
) -> dict:
    """
    Select the best next video from the database.

    Returns a dict with video metadata, or a sentinel dict if the DB is empty.
    Never raises — returns the sentinel on any DB error so Gemini always gets
    a valid tool response.
    """
    try:
        # 1. Merge caller exclusions with persisted user preferences
        user_prefs = await _get_user_prefs(user_id)
        combined_excludes = _merge_excludes(exclude_vibes, user_prefs.get("exclude_vibes", []))

        # 2. Collect last-20 video IDs to prevent immediate repeats
        recent_ids = await _recent_video_ids(user_id, limit=20)

        # 3. Primary query: tag + pacing match
        row = await _primary_query(
            mood_tags=mood_tags,
            pacing=pacing,
            max_duration=max_duration,
            exclude_vibes=combined_excludes,
            exclude_ids=recent_ids,
        )

        # 4. Fallback: any unplayed video in random order
        if row is None:
            logger.info("Primary fetch_video query returned no rows; using fallback")
            row = await _fallback_query(
                max_duration=max_duration,
                exclude_ids=recent_ids,
            )

        # 4b. Vector search fallback: embed the mood_tags query and find the
        #     nearest video by cosine distance in video_embeddings.
        if row is None and mood_tags:
            logger.info("Tag fallback returned no rows; attempting vector search fallback")
            row = await _vector_fallback_query(
                mood_tags=mood_tags,
                exclude_ids=recent_ids,
            )
            if row is not None:
                logger.info("Vector fallback returned a result: %s", row["title"])

        # 5. DB empty or no match — auto-discover from subscribed channels
        if row is None:
            logger.info("fetch_video: no matches in DB — triggering auto-discovery")
            try:
                import video_discovery
                # Build a search query from the mood tags
                search_query = " ".join(mood_tags) if mood_tags else "interesting video"
                discovery = await video_discovery.discover_videos(
                    query=search_query,
                    max_results=5,
                )
                if discovery.get("videos"):
                    # Re-query the DB now that we have fresh content
                    row = await _fallback_query(
                        max_duration=max_duration,
                        exclude_ids=recent_ids,
                    )
                    if row is not None:
                        logger.info("Auto-discovery filled DB, serving: %s", row["title"])
                        return _row_to_dict(row)
                    # If still no row, return first discovered video directly
                    first = discovery["videos"][0]
                    return {
                        "id": first.get("id"),
                        "url": first.get("url"),
                        "title": first.get("title", ""),
                        "channel": first.get("channel", ""),
                        "duration_sec": first.get("duration_sec"),
                        "pacing": first.get("pacing"),
                        "stimulation": first.get("stimulation"),
                        "novelty": first.get("novelty"),
                        "vibe": first.get("vibe"),
                        "mood_tags": first.get("mood_tags", []),
                        "content_tags": first.get("content_tags", []),
                        "summary": first.get("summary", ""),
                    }
            except Exception:
                logger.exception("Auto-discovery failed during fetch_video")

            logger.warning("fetch_video: database has no videos and discovery failed")
            return _no_content_sentinel()

        return _row_to_dict(row)

    except Exception:
        logger.exception("fetch_video raised an unexpected error")
        return _no_content_sentinel()


async def log_skip(
    video_url: str,
    reason: str,
    watch_duration: int,
    user_id: str = "default",
) -> dict:
    """
    Record a skip event and auto-ban a vibe if the user has skipped it 3+
    times today.

    Returns a dict with skip_count_today and whether the vibe was auto-banned.
    """
    try:
        video = await fetch_one("SELECT id, vibe FROM videos WHERE url = $1", video_url)
        if video is None:
            return {"status": "video_not_found", "url": video_url}

        video_id = video["id"]
        vibe = (await fetch_one(
            "SELECT vibe FROM video_tags WHERE video_id = $1", video_id
        ) or {}).get("vibe")

        await execute(
            """
            INSERT INTO playback_log
                (user_id, video_id, watch_duration_sec, skipped, skip_reason)
            VALUES ($1, $2, $3, TRUE, $4)
            """,
            user_id, video_id, watch_duration, reason,
        )

        # Count skips of this vibe today
        auto_banned = False
        if vibe:
            skip_count = await fetch_one(
                """
                SELECT COUNT(*) AS cnt
                FROM playback_log pl
                JOIN video_tags vt ON vt.video_id = pl.video_id
                WHERE pl.user_id   = $1
                  AND pl.skipped   = TRUE
                  AND vt.vibe      = $2
                  AND pl.played_at >= NOW() - INTERVAL '1 day'
                """,
                user_id, vibe,
            )
            count = int(skip_count["cnt"]) if skip_count else 0

            if count >= 3:
                # Auto-add to exclude_vibes if not already present
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
                    auto_banned = True
                    logger.info(
                        "Auto-banned vibe '%s' for user '%s' after %d skips today",
                        vibe, user_id, count,
                    )

        return {
            "status": "logged",
            "video_id": video_id,
            "vibe": vibe,
            "skip_count_today": count if vibe else None,
            "vibe_auto_banned": auto_banned,
        }

    except Exception:
        logger.exception("log_skip raised an unexpected error")
        return {"status": "error"}


async def log_playback(
    video_url: str,
    watch_duration: int,
    mood_tags: list[str],
    user_id: str = "default",
) -> None:
    """
    Record a completed (non-skipped) playback event.
    """
    try:
        video = await fetch_one("SELECT id FROM videos WHERE url = $1", video_url)
        if video is None:
            logger.warning("log_playback: unknown url %s", video_url)
            return

        await execute(
            """
            INSERT INTO playback_log
                (user_id, video_id, watch_duration_sec, skipped, mood_tags_at_play)
            VALUES ($1, $2, $3, FALSE, $4)
            """,
            user_id, video["id"], watch_duration, mood_tags,
        )
    except Exception:
        logger.exception("log_playback raised an unexpected error")


async def update_user_profile(
    new_interest_tags: list[str] | None = None,
    remove_interest_tags: list[str] | None = None,
    user_id: str = "default",
) -> dict:
    """
    Permanently add or remove interest tags on the user's profile.

    - new_interest_tags: appended; duplicates are deduplicated in-place.
    - remove_interest_tags: removed if present; no-op for absent tags.
    - Returns the updated interest_tags list (empty list when DB has no row yet).
    Never raises — returns an error sentinel on unexpected failures.
    """
    try:
        if new_interest_tags:
            # Upsert row, then append new tags and deduplicate in one statement.
            # array_cat appends; array_remove strips a single value at a time,
            # so we use array(SELECT DISTINCT ...) via a subquery-free approach:
            # ARRAY(SELECT DISTINCT unnest(array_cat(interest_tags, $2::text[])))
            await execute(
                """
                INSERT INTO user_preferences (user_id, interest_tags, updated_at)
                VALUES ($1, $2::text[], NOW())
                ON CONFLICT (user_id) DO UPDATE
                    SET interest_tags = ARRAY(
                            SELECT DISTINCT unnest(
                                array_cat(user_preferences.interest_tags, $2::text[])
                            )
                        ),
                        updated_at = NOW()
                """,
                user_id, new_interest_tags,
            )

        if remove_interest_tags:
            # Ensure the row exists first (may not if no add was performed).
            await execute(
                """
                INSERT INTO user_preferences (user_id, interest_tags, updated_at)
                VALUES ($1, ARRAY[]::text[], NOW())
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )
            # Remove each tag. PostgreSQL's array_remove removes all occurrences.
            # We build a single UPDATE that folds all removals in one pass.
            await execute(
                """
                UPDATE user_preferences
                SET interest_tags = (
                        SELECT COALESCE(
                            ARRAY(
                                SELECT elem
                                FROM unnest(interest_tags) AS elem
                                WHERE elem != ALL($2::text[])
                            ),
                            ARRAY[]::text[]
                        )
                    ),
                    updated_at = NOW()
                WHERE user_id = $1
                """,
                user_id, remove_interest_tags,
            )

        # Fetch and return current state
        row = await fetch_one(
            "SELECT interest_tags FROM user_preferences WHERE user_id = $1",
            user_id,
        )
        updated_tags: list[str] = list(row["interest_tags"]) if row and row["interest_tags"] else []

        logger.info(
            "update_user_profile: user=%s added=%s removed=%s result=%s",
            user_id, new_interest_tags, remove_interest_tags, updated_tags,
        )
        return {"status": "updated", "interest_tags": updated_tags}

    except Exception:
        logger.exception("update_user_profile raised an unexpected error")
        return {"status": "error", "interest_tags": []}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_user_prefs(user_id: str) -> dict:
    row = await fetch_one(
        "SELECT * FROM user_preferences WHERE user_id = $1", user_id
    )
    if row is None:
        return {
            "exclude_vibes": [],
            "preferred_pacing_min": 3,
            "preferred_pacing_max": 8,
        }
    return dict(row)


async def _recent_video_ids(user_id: str, limit: int = 20) -> list[int]:
    rows = await fetch_all(
        """
        SELECT video_id
        FROM playback_log
        WHERE user_id = $1 AND video_id IS NOT NULL
        ORDER BY played_at DESC
        LIMIT $2
        """,
        user_id, limit,
    )
    return [r["video_id"] for r in rows]


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
    pacing: int,
    max_duration: int | None,
    exclude_vibes: list[str],
    exclude_ids: list[int],
) -> "asyncpg.Record | None":
    """
    SELECT with tag overlap, pacing window, vibe exclusion, duration cap,
    and history exclusion.  Ordered by novelty DESC + random jitter.
    """
    # Build dynamic clauses without string interpolation for user data.
    # Parameterised positions shift depending on optional args.
    params: list = [mood_tags, pacing]
    param_idx = 3  # next available $N

    duration_clause = ""
    if max_duration is not None:
        duration_clause = f"AND v.duration_sec <= ${param_idx}"
        params.append(max_duration)
        param_idx += 1

    vibe_clause = ""
    if exclude_vibes:
        vibe_clause = f"AND (vt.vibe IS NULL OR vt.vibe != ALL(${param_idx}::text[]))"
        params.append(exclude_vibes)
        param_idx += 1

    history_clause = ""
    if exclude_ids:
        history_clause = f"AND v.id != ALL(${param_idx}::int[])"
        params.append(exclude_ids)
        param_idx += 1

    query = f"""
        SELECT
            v.id,
            v.url,
            v.title,
            v.channel,
            v.duration_sec,
            vt.pacing,
            vt.stimulation,
            vt.novelty,
            vt.vibe,
            vt.mood_tags,
            vt.content_tags,
            vt.claude_summary
        FROM videos v
        JOIN video_tags vt ON vt.video_id = v.id
        WHERE vt.mood_tags && $1::text[]
          AND vt.pacing BETWEEN ($2 - 2) AND ($2 + 2)
          {duration_clause}
          {vibe_clause}
          {history_clause}
        ORDER BY vt.novelty DESC, RANDOM() * 0.3
        LIMIT 1
    """
    return await fetch_one(query, *params)


async def _fallback_query(
    max_duration: int | None,
    exclude_ids: list[int],
) -> "asyncpg.Record | None":
    """
    Broad fallback: ignore tags/pacing, just pick something unplayed at random.
    """
    params: list = []
    param_idx = 1

    duration_clause = ""
    if max_duration is not None:
        duration_clause = f"AND v.duration_sec <= ${param_idx}"
        params.append(max_duration)
        param_idx += 1

    history_clause = ""
    if exclude_ids:
        history_clause = f"AND v.id != ALL(${param_idx}::int[])"
        params.append(exclude_ids)
        param_idx += 1

    query = f"""
        SELECT
            v.id,
            v.url,
            v.title,
            v.channel,
            v.duration_sec,
            vt.pacing,
            vt.stimulation,
            vt.novelty,
            vt.vibe,
            vt.mood_tags,
            vt.content_tags,
            vt.claude_summary
        FROM videos v
        LEFT JOIN video_tags vt ON vt.video_id = v.id
        WHERE TRUE
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
    Semantic search fallback using pgvector cosine distance.

    Constructs a query string from mood_tags, generates a Gemini embedding,
    then finds the closest video in video_embeddings.  Returns None if
    embeddings are unavailable (no API key, table empty, etc.).
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — cannot run vector fallback")
        return None

    query_text = "Mood: " + ", ".join(mood_tags)

    try:
        from google import genai as _genai
        client = _genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=query_text,
            config={"output_dimensionality": 768},
        )
        embedding: list[float] = list(result.embeddings[0].values)
    except Exception as exc:
        logger.warning("Gemini embedding failed in vector fallback: %s", exc)
        return None

    vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

    params: list = [vec_literal]
    param_idx = 2

    history_clause = ""
    if exclude_ids:
        history_clause = f"AND v.id != ALL(${param_idx}::int[])"
        params.append(exclude_ids)
        param_idx += 1

    query = f"""
        SELECT
            v.id,
            v.url,
            v.title,
            v.channel,
            v.duration_sec,
            vt.pacing,
            vt.stimulation,
            vt.novelty,
            vt.vibe,
            vt.mood_tags,
            vt.content_tags,
            vt.claude_summary
        FROM video_embeddings ve
        JOIN videos v ON v.id = ve.video_id
        LEFT JOIN video_tags vt ON vt.video_id = v.id
        WHERE TRUE
          {history_clause}
        ORDER BY ve.embedding <=> $1::vector
        LIMIT 1
    """

    try:
        return await fetch_one(query, *params)
    except Exception as exc:
        logger.warning("Vector fallback DB query failed: %s", exc)
        return None


def _row_to_dict(row) -> dict:
    """Convert an asyncpg Record to a JSON-serialisable dict."""
    return {
        "id": int(row["id"]),
        "url": row["url"],
        "title": row["title"],
        "channel": row["channel"],
        "duration_sec": int(row["duration_sec"]) if row["duration_sec"] is not None else None,
        "pacing": int(row["pacing"]) if row["pacing"] is not None else None,
        "stimulation": int(row["stimulation"]) if row["stimulation"] is not None else None,
        "novelty": int(row["novelty"]) if row["novelty"] is not None else None,
        "vibe": row["vibe"],
        "mood_tags": list(row["mood_tags"]) if row["mood_tags"] else [],
        "content_tags": list(row["content_tags"]) if row["content_tags"] else [],
        "summary": row["claude_summary"],
    }


async def get_liked_tags(user_id: str = "default", days: int = 7, limit: int = 20) -> list[str]:
    """Return mood/content tags from recently completed (non-skipped) video playback."""
    try:
        rows = await fetch_all(
            """
            SELECT DISTINCT unnest(array_cat(vt.mood_tags, vt.content_tags)) AS tag
            FROM playback_log pl
            JOIN video_tags vt ON vt.video_id = pl.video_id
            WHERE pl.user_id = $1 AND pl.skipped = FALSE
              AND pl.played_at >= NOW() - make_interval(days => $2)
            LIMIT $3
            """,
            user_id, days, limit,
        )
        return [r["tag"] for r in rows if r["tag"]]
    except Exception:
        logger.exception("get_liked_tags failed")
        return []


def _no_content_sentinel() -> dict:
    return {
        "id": None,
        "url": None,
        "title": "No content available",
        "channel": None,
        "duration_sec": None,
        "pacing": None,
        "stimulation": None,
        "novelty": None,
        "vibe": None,
        "mood_tags": [],
        "content_tags": [],
        "summary": "The video library is empty. Add videos to get started.",
        "no_content": True,
    }
