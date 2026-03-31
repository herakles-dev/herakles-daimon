"""
Embedding generator — creates vector representations for all videos
that don't yet have an entry in video_embeddings.

Constructs a rich text from: title + vibe + mood_tags + content_tags
+ claude_summary, then calls Gemini gemini-embedding-001 (768 dims)
and stores the result in the video_embeddings table.

Usage:
    python -m scraper.embed
    python -m scraper.embed --batch-size 20 --limit 50
    python -m scraper.embed --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import List, Optional

# Ensure /app is importable inside the container
sys.path.insert(0, "/app")

from db import ensure_schema, execute, fetch_all

logger = logging.getLogger("play-backend.embed")

# ---------------------------------------------------------------------------
# Text construction
# ---------------------------------------------------------------------------

def _build_embed_text(row: dict) -> str:
    """
    Combine structured tag fields into a single string for embedding.

    The text is ordered to weight high-signal fields first (title, vibe,
    mood) before lower-signal ones (content tags, summary).
    """
    parts: List[str] = []

    title = (row.get("title") or "").strip()
    if title:
        parts.append(f"Title: {title}")

    channel = (row.get("channel") or "").strip()
    if channel:
        parts.append(f"Channel: {channel}")

    vibe = (row.get("vibe") or "").strip()
    if vibe and vibe != "unknown":
        parts.append(f"Vibe: {vibe}")

    mood_tags = row.get("mood_tags") or []
    if mood_tags:
        parts.append(f"Mood: {', '.join(mood_tags)}")

    content_tags = row.get("content_tags") or []
    if content_tags:
        parts.append(f"Topics: {', '.join(content_tags)}")

    summary = (row.get("claude_summary") or "").strip()
    if summary:
        parts.append(f"Summary: {summary}")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Embedding call
# ---------------------------------------------------------------------------

def _get_embedding(client, text: str) -> List[float]:
    """
    Call Gemini gemini-embedding-001 synchronously (the google-genai SDK
    does not expose an async embed method, so we run it in an executor).
    """
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
        config={"output_dimensionality": 768},
    )
    return list(result.embeddings[0].values)


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

async def _embed_batch(
    rows: List[dict],
    client,
    loop: asyncio.AbstractEventLoop,
    dry_run: bool,
) -> tuple[int, int]:
    """Process a single batch. Returns (success, errors)."""
    success = 0
    errors = 0

    for row in rows:
        video_id = row["id"]
        title_short = (row.get("title") or "")[:60]
        text = _build_embed_text(row)

        if not text.strip():
            logger.warning("video_id=%d has no embeddable text — skipping", video_id)
            errors += 1
            continue

        if dry_run:
            logger.info("[DRY RUN] Would embed video_id=%d '%s'", video_id, title_short)
            logger.debug("  text: %s", text[:120])
            success += 1
            continue

        try:
            # Run the synchronous SDK call in a thread pool
            embedding = await loop.run_in_executor(
                None, _get_embedding, client, text
            )
        except Exception as exc:
            logger.error("Gemini embed error for video_id=%d '%s': %s", video_id, title_short, exc)
            errors += 1
            continue

        if len(embedding) != 768:
            logger.error(
                "Unexpected embedding dimension %d for video_id=%d — expected 768",
                len(embedding), video_id,
            )
            errors += 1
            continue

        # Format as a pgvector literal: '[f1,f2,...,f768]'
        vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

        try:
            await execute(
                """
                INSERT INTO video_embeddings (video_id, embedding, model)
                VALUES ($1, $2::vector, 'gemini-embedding-001')
                ON CONFLICT (video_id) DO UPDATE
                    SET embedding   = EXCLUDED.embedding,
                        model       = EXCLUDED.model,
                        created_at  = NOW()
                """,
                video_id,
                vec_literal,
            )
            logger.info("Embedded video_id=%d '%s'", video_id, title_short)
            success += 1
        except Exception as exc:
            logger.error("DB write failed for video_id=%d: %s", video_id, exc)
            errors += 1

    return success, errors


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Gemini embeddings for unembedded videos",
    )
    parser.add_argument("--batch-size", type=int, default=10, metavar="N",
                        help="Videos per batch (default: 10)")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max videos to embed (default: all missing)")
    parser.add_argument("--all", dest="embed_all", action="store_true",
                        help="Re-embed ALL videos, not just missing ones")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip actual Gemini calls and DB writes")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG logging")
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        logger.error("GEMINI_API_KEY not set — cannot call Gemini embedding API.")
        return 1

    await ensure_schema()

    # Fetch videos that need embedding
    if args.embed_all:
        base_query = """
            SELECT
                v.id, v.title, v.channel,
                vt.vibe, vt.mood_tags, vt.content_tags, vt.claude_summary
            FROM videos v
            LEFT JOIN video_tags vt ON vt.video_id = v.id
        """
    else:
        base_query = """
            SELECT
                v.id, v.title, v.channel,
                vt.vibe, vt.mood_tags, vt.content_tags, vt.claude_summary
            FROM videos v
            LEFT JOIN video_tags vt ON vt.video_id = v.id
            LEFT JOIN video_embeddings ve ON ve.video_id = v.id
            WHERE ve.video_id IS NULL
        """

    if args.limit:
        base_query += f" LIMIT {args.limit}"

    rows = await fetch_all(base_query)
    videos = [dict(r) for r in rows]

    logger.info(
        "Found %d video(s) needing embeddings (batch_size=%d dry_run=%s)",
        len(videos), args.batch_size, args.dry_run,
    )

    if not videos:
        print("Nothing to embed — all videos already have embeddings.")
        return 0

    # Initialise Gemini client
    if not args.dry_run:
        from google import genai
        client = genai.Client(api_key=api_key)
    else:
        client = None

    loop = asyncio.get_event_loop()
    total_success = 0
    total_errors = 0

    # Process in batches
    for batch_start in range(0, len(videos), args.batch_size):
        batch = videos[batch_start : batch_start + args.batch_size]
        batch_num = (batch_start // args.batch_size) + 1
        total_batches = (len(videos) + args.batch_size - 1) // args.batch_size

        logger.info(
            "Processing batch %d/%d (%d videos)",
            batch_num, total_batches, len(batch),
        )

        s, e = await _embed_batch(batch, client, loop, dry_run=args.dry_run)
        total_success += s
        total_errors += e

        logger.info(
            "Batch %d done — success=%d errors=%d | running total: %d/%d",
            batch_num, s, e, total_success, total_success + total_errors,
        )

    print(f"\n{'='*60}")
    print(f"  Embedding complete")
    print(f"  Total processed : {len(videos)}")
    print(f"  Success         : {total_success}")
    print(f"  Errors          : {total_errors}")
    print(f"{'='*60}\n")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
