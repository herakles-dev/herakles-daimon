"""
Main scraper pipeline — discover → transcript → tag → store.

Can be run standalone:
    python -m scraper.pipeline
    python -m scraper.pipeline --channels https://www.youtube.com/@NetworkChuck
    python -m scraper.pipeline --max-videos 5 --dry-run

Or imported and called from FastAPI:
    from scraper.pipeline import run_pipeline, PipelineResult
    results = await run_pipeline(config)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Set

from .config import ScraperConfig, get_config
from .tagger import ADHDTags, Tagger
from .youtube import VideoMeta, deduplicate_videos, fetch_transcript, list_recent_videos

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ProcessedVideo:
    """One fully-processed video ready for storage."""
    meta: VideoMeta
    tags: ADHDTags


@dataclass
class PipelineResult:
    """Aggregate outcome of a full pipeline run."""
    processed: List[ProcessedVideo] = field(default_factory=list)
    skipped_duplicate: int = 0
    skipped_no_transcript: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return len(self.processed)


# ---------------------------------------------------------------------------
# Per-video processing
# ---------------------------------------------------------------------------

async def _process_one(
    video: VideoMeta,
    tagger: Tagger,
    config: ScraperConfig,
) -> Optional[ProcessedVideo]:
    """
    Fetch transcript and generate tags for a single video.

    Returns None on unrecoverable error so the batch can continue.
    """
    try:
        # Fetch transcript (mutates video in-place)
        video = await fetch_transcript(
            video,
            preferred_langs=config.transcript_languages,
            delay_min=config.request_delay_min,
            delay_max=config.request_delay_max,
        )

        # Generate ADHD tags via Claude
        tags = await tagger.tag_video(video)

        return ProcessedVideo(meta=video, tags=tags)

    except Exception as exc:
        logger.error(
            "Unexpected error processing '%s' (%s): %s",
            video.title[:60],
            video.url,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Batch processing with concurrency cap
# ---------------------------------------------------------------------------

async def _process_batch(
    videos: List[VideoMeta],
    tagger: Tagger,
    config: ScraperConfig,
    result: PipelineResult,
) -> None:
    """
    Process a list of videos with a semaphore-limited concurrency.

    Results are appended to `result` in-place.
    """
    semaphore = asyncio.Semaphore(config.concurrency)

    async def _guarded(video: VideoMeta):
        async with semaphore:
            return await _process_one(video, tagger, config)

    tasks = [asyncio.create_task(_guarded(v)) for v in videos]
    outcomes = await asyncio.gather(*tasks, return_exceptions=False)

    for outcome in outcomes:
        if outcome is None:
            result.errors += 1
        else:
            if outcome.meta.transcript is None:
                result.skipped_no_transcript += 1
                # We still include tag results — Claude infers from title/desc
            result.processed.append(outcome)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    config: Optional[ScraperConfig] = None,
    channel_urls: Optional[List[str]] = None,
    seen_urls: Optional[Set[str]] = None,
    dry_run: bool = False,
) -> PipelineResult:
    """
    Full async pipeline: discover → transcript → tag.

    Args:
        config:       ScraperConfig instance (defaults to get_config()).
        channel_urls: Override channels (ignores config.channels if provided).
        seen_urls:    Set of already-processed URLs for de-duplication.
                      Updated in-place as new videos are queued.
        dry_run:      Discover videos but skip transcript + tagging.

    Returns:
        PipelineResult with all processed videos attached.
    """
    if config is None:
        config = get_config()

    if seen_urls is None:
        seen_urls = set()

    result = PipelineResult()

    # Determine target channels
    targets = channel_urls or [ch.url for ch in config.channels]
    if not targets:
        logger.warning("No channels configured — nothing to scrape.")
        return result

    if not config.gemini_api_key and not dry_run:
        logger.error("GEMINI_API_KEY is not set — cannot tag videos.")
        return result

    tagger = Tagger(api_key=config.gemini_api_key, model=config.gemini_model)

    logger.info(
        "Pipeline start — channels=%d max_videos=%d concurrency=%d dry_run=%s",
        len(targets),
        config.max_videos_per_channel,
        config.concurrency,
        dry_run,
    )

    for channel_url in targets:
        logger.info("--- Channel: %s", channel_url)

        # 1. Discover recent videos
        videos = await list_recent_videos(
            channel_url,
            max_videos=config.max_videos_per_channel,
            preferred_langs=config.transcript_languages,
            delay_min=config.request_delay_min,
            delay_max=config.request_delay_max,
        )

        if not videos:
            logger.warning("No videos found for %s", channel_url)
            continue

        # 2. De-duplicate against already-seen URLs
        fresh = deduplicate_videos(videos, seen_urls)
        dupes = len(videos) - len(fresh)
        if dupes:
            logger.info("Skipped %d duplicate(s) for %s", dupes, channel_url)
            result.skipped_duplicate += dupes

        if not fresh:
            logger.info("All videos already seen for %s — skipping", channel_url)
            continue

        logger.info(
            "%d fresh video(s) to process from %s", len(fresh), channel_url
        )

        if dry_run:
            for v in fresh:
                logger.info("[DRY RUN] Would process: %s — %s", v.video_id, v.title[:70])
            continue

        # 3. Transcript + tag in parallel (semaphore-limited)
        await _process_batch(fresh, tagger, config, result)

    logger.info(
        "Pipeline complete — processed=%d skipped_dupes=%d no_transcript=%d errors=%d",
        result.total,
        result.skipped_duplicate,
        result.skipped_no_transcript,
        result.errors,
    )

    return result


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

async def _store_results(processed: List[ProcessedVideo]) -> int:
    """Write processed videos + tags to the database. Returns count stored."""
    import hashlib
    sys.path.insert(0, "/app")  # ensure db module is importable
    from db import execute, ensure_schema, fetch_one

    await ensure_schema()
    stored = 0

    for pv in processed:
        v = pv.meta
        t = pv.tags
        try:
            # Insert video (skip if URL already exists)
            existing = await fetch_one("SELECT id FROM videos WHERE url = $1", v.url)
            if existing:
                video_id = existing["id"]
            else:
                transcript_hash = hashlib.sha256(
                    (v.transcript or "").encode()
                ).hexdigest()[:16] if v.transcript else None

                row = await fetch_one(
                    """INSERT INTO videos (url, source, title, channel, description,
                       duration_sec, thumbnail_url, transcript_hash)
                       VALUES ($1, 'youtube', $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (url) DO NOTHING
                       RETURNING id""",
                    v.url, v.title, v.channel_name,
                    (v.description or "")[:2000],
                    v.duration_seconds, v.thumbnail_url, transcript_hash,
                )
                if not row:
                    continue
                video_id = row["id"]

            # Insert tags
            await execute(
                """INSERT INTO video_tags (video_id, pacing, stimulation, novelty,
                   vibe, mood_tags, content_tags, claude_summary)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   ON CONFLICT (video_id) DO UPDATE SET
                     pacing = EXCLUDED.pacing, stimulation = EXCLUDED.stimulation,
                     novelty = EXCLUDED.novelty, vibe = EXCLUDED.vibe,
                     mood_tags = EXCLUDED.mood_tags, content_tags = EXCLUDED.content_tags,
                     claude_summary = EXCLUDED.claude_summary, tagged_at = NOW()""",
                video_id, t.pacing, t.stimulation, t.novelty,
                t.vibe, t.mood_tags, t.content_tags, t.claude_summary,
            )
            stored += 1

        except Exception as exc:
            logger.error("Failed to store %s: %s", v.url, exc)

    return stored


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Herakles Play — Claude content scraper pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with default seed channels
  python -m scraper.pipeline

  # Override channels
  python -m scraper.pipeline --channels https://www.youtube.com/@Fireship

  # Limit to 3 videos per channel, dry run
  python -m scraper.pipeline --max-videos 3 --dry-run

  # Verbose output
  python -m scraper.pipeline --verbose
""",
    )
    p.add_argument(
        "--channels",
        nargs="+",
        metavar="URL",
        help="Override seed channels (space-separated YouTube channel URLs)",
    )
    p.add_argument(
        "--max-videos",
        type=int,
        default=None,
        metavar="N",
        help="Max videos per channel (overrides config default)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover videos but skip transcript fetch and tagging",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return p


async def _main(argv: Optional[List[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    config = get_config()

    if args.max_videos is not None:
        config.max_videos_per_channel = args.max_videos

    result = await run_pipeline(
        config=config,
        channel_urls=args.channels,
        dry_run=args.dry_run,
    )

    # Store results in DB
    if result.processed and not args.dry_run:
        stored = await _store_results(result.processed)
        logger.info("Stored %d videos in database", stored)
    else:
        stored = 0

    # Print summary to stdout for human consumption
    print(f"\n{'='*60}")
    print(f"  Scrape complete")
    print(f"  Processed   : {result.total}")
    print(f"  Stored in DB: {stored}")
    print(f"  Dupes skipped: {result.skipped_duplicate}")
    print(f"  No transcript: {result.skipped_no_transcript}")
    print(f"  Errors       : {result.errors}")
    print(f"{'='*60}\n")

    if result.processed and not args.dry_run:
        print("Sample results (first 3):")
        for pv in result.processed[:3]:
            print(f"\n  [{pv.meta.video_id}] {pv.meta.title[:65]}")
            print(f"    vibe      : {pv.tags.vibe}")
            print(f"    pacing    : {pv.tags.pacing}/10")
            print(f"    stimulation: {pv.tags.stimulation}/10")
            print(f"    novelty   : {pv.tags.novelty}/10")
            print(f"    mood      : {', '.join(pv.tags.mood_tags)}")
            print(f"    content   : {', '.join(pv.tags.content_tags)}")
            if pv.tags.claude_summary:
                print(f"    summary   : {pv.tags.claude_summary[:120]}")

    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
