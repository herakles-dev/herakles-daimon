"""Seed the music database with a mood-diverse initial collection.

Target: 200+ tracks across 6 mood categories.
Sources: Jamendo (primary), Incompetech (cinematic/score).

Usage:
    docker compose exec backend python -m scraper.seed_music
    docker compose exec backend python -m scraper.seed_music --dry-run
    docker compose exec backend python -m scraper.seed_music --status
    docker compose exec backend python -m scraper.seed_music --batch chill
"""
from __future__ import annotations

import asyncio
import logging
import sys

# Ensure /app is importable inside the container
sys.path.insert(0, "/app")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed plan
# ---------------------------------------------------------------------------
# Each entry: (source, mood_tags, genre, max_tracks, batch_label, description)
#
# Batch labels let you run a single category:
#   python -m scraper.seed_music --batch chill
#   python -m scraper.seed_music --batch focus
#   python -m scraper.seed_music --batch jazz
#   python -m scraper.seed_music --batch energetic
#   python -m scraper.seed_music --batch melancholic
#   python -m scraper.seed_music --batch cinematic
#
# Total target: ~255 tracks (accounts for dedup/skip attrition)
# ---------------------------------------------------------------------------

SEED_PLAN: list[tuple[str, list[str] | None, str | None, int, str, str]] = [
    # (source, mood_tags, genre, max_tracks, batch_label, description)

    # ── Chill / Ambient ──────────────────────────────────────────────────────
    ("jamendo", ["chillout", "relaxing"],   None, 40, "chill", "Chill/Relaxing"),
    ("jamendo", ["ambient", "downtempo"],   None, 30, "chill", "Ambient/Downtempo"),
    ("incompetech", None, "Ambient",        10,  "chill", "Ambient (Incompetech)"),

    # ── Focus / Study ────────────────────────────────────────────────────────
    ("jamendo", ["electronic", "minimal"],  None, 30, "focus", "Focus/Study Electronic"),
    ("jamendo", ["lofi", "study"],          None, 20, "focus", "Lo-fi Study"),

    # ── Energetic / Workout ──────────────────────────────────────────────────
    ("jamendo", ["energetic", "upbeat"],    None, 30, "energetic", "Energetic/Upbeat"),
    ("jamendo", ["rock", "indie"],          None, 20, "energetic", "Rock/Indie"),

    # ── Melancholic / Introspective ──────────────────────────────────────────
    ("jamendo", ["melancholic", "acoustic"], None, 25, "melancholic", "Melancholic/Acoustic"),
    ("jamendo", ["sad", "emotional"],        None, 15, "melancholic", "Sad/Emotional"),

    # ── Jazz / Blues ─────────────────────────────────────────────────────────
    ("jamendo", ["jazz", "smooth"],         None, 30, "jazz", "Jazz/Smooth"),
    ("incompetech", None, "Jazz",           15,  "jazz", "Jazz (Incompetech)"),

    # ── Cinematic / Score ────────────────────────────────────────────────────
    ("incompetech", None, "Cinematic",      25,  "cinematic", "Cinematic/Epic (Incompetech)"),
    ("jamendo", ["cinematic", "orchestral"], None, 15, "cinematic", "Cinematic (Jamendo)"),
]


def _plan_for_batch(batch: str | None) -> list[tuple]:
    """Filter SEED_PLAN to the requested batch (or return all if None)."""
    if batch is None:
        return SEED_PLAN
    matched = [entry for entry in SEED_PLAN if entry[4] == batch]
    if not matched:
        valid = sorted({e[4] for e in SEED_PLAN})
        raise ValueError(
            f"Unknown batch {batch!r}. Valid batches: {', '.join(valid)}"
        )
    return matched


# ---------------------------------------------------------------------------
# Core seed logic
# ---------------------------------------------------------------------------

async def seed(dry_run: bool = False, batch: str | None = None) -> dict:
    """Execute the seed plan.

    Args:
        dry_run: Discover tracks but do not write to the database.
        batch:   Run only a named subset (e.g. 'chill', 'jazz').

    Returns:
        Summary dict with ingested/skipped/failed counts and DB totals.
    """
    from .music_pipeline import MusicPipeline

    plan = _plan_for_batch(batch)
    pipeline = MusicPipeline()

    total_ingested = 0
    total_skipped  = 0
    total_failed   = 0

    plan_total = sum(entry[3] for entry in plan)
    label = f"batch={batch}" if batch else "full seed"
    logger.info(
        "Starting seed (%s) — %d entries, target ~%d tracks",
        label, len(plan), plan_total,
    )

    for source, mood_tags, genre, max_tracks, _batch_label, desc in plan:
        logger.info(
            "  [%s] source=%s mood_tags=%s genre=%s max=%d",
            desc, source, mood_tags, genre, max_tracks,
        )

        try:
            tracks = await pipeline.discover(
                source=source,
                mood_tags=mood_tags,
                genre=genre,
                max_tracks=max_tracks,
            )
            logger.info("    discovered %d tracks", len(tracks))

            if dry_run:
                logger.info("    [DRY RUN] would ingest %d tracks", len(tracks))
                total_ingested += len(tracks)
                continue

            result = await pipeline.ingest(tracks)
            ingested = result.get("ingested", 0)
            skipped  = result.get("skipped",  0)
            failed   = result.get("failed",   0)
            errors   = result.get("errors",   [])

            total_ingested += ingested
            total_skipped  += skipped
            total_failed   += failed

            logger.info(
                "    ingested=%d skipped=%d failed=%d",
                ingested, skipped, failed,
            )
            for err in errors[:3]:   # cap noise from large batches
                logger.warning("    error: %s", err)

        except Exception as exc:
            logger.error("  [%s] FAILED — %s", desc, exc)
            total_failed += max_tracks

    # ── Final summary ────────────────────────────────────────────────────────
    logger.info(
        "\nSeed %scomplete — ingested=%d skipped=%d failed=%d",
        "(dry-run) " if dry_run else "",
        total_ingested, total_skipped, total_failed,
    )

    status: dict = {}
    if not dry_run:
        try:
            status = await pipeline.status()
            logger.info(
                "DB totals — total=%d ready=%d processing=%d error=%d tagged=%d embedded=%d",
                status.get("total",      0),
                status.get("ready",      0),
                status.get("processing", 0),
                status.get("error",      0),
                status.get("tagged",     0),
                status.get("embedded",   0),
            )
        except Exception as exc:
            logger.warning("Could not fetch DB status: %s", exc)

    return {
        "ingested":  total_ingested,
        "skipped":   total_skipped,
        "failed":    total_failed,
        "db_total":  status.get("total", 0),
        "db_ready":  status.get("ready", 0),
    }


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

async def show_status() -> None:
    """Print current music library status to stdout."""
    from .music_pipeline import MusicPipeline

    pipeline = MusicPipeline()
    try:
        status = await pipeline.status()
    except Exception as exc:
        print(f"Error fetching status: {exc}", file=sys.stderr)
        sys.exit(1)

    total      = status.get("total",      0)
    ready      = status.get("ready",      0)
    processing = status.get("processing", 0)
    error      = status.get("error",      0)
    tagged     = status.get("tagged",     0)
    embedded   = status.get("embedded",   0)
    by_source  = status.get("by_source",  {})

    print()
    print("Music Library Status")
    print("=" * 40)
    print(f"  Total tracks : {total}")
    print(f"  Ready        : {ready}")
    print(f"  Processing   : {processing}")
    print(f"  Errors       : {error}")
    print(f"  Tagged       : {tagged}")
    print(f"  Embedded     : {embedded}")

    if by_source:
        print()
        print("  By source:")
        for src, count in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"    {src:<15} {count}")

    # Seed plan summary
    print()
    print("Seed plan targets:")
    batches: dict[str, int] = {}
    for _src, _mood, _genre, max_t, batch_label, _desc in SEED_PLAN:
        batches[batch_label] = batches.get(batch_label, 0) + max_t
    for batch_label, target in sorted(batches.items()):
        print(f"    {batch_label:<15} ~{target} tracks")
    print(f"    {'TOTAL':<15} ~{sum(batches.values())} tracks")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    args = sys.argv[1:]

    if "--status" in args:
        asyncio.run(show_status())

    elif "--dry-run" in args:
        batch = None
        if "--batch" in args:
            idx = args.index("--batch")
            try:
                batch = args[idx + 1]
            except IndexError:
                print("Error: --batch requires an argument", file=sys.stderr)
                sys.exit(1)
        asyncio.run(seed(dry_run=True, batch=batch))

    elif "--batch" in args:
        idx = args.index("--batch")
        try:
            batch = args[idx + 1]
        except IndexError:
            print("Error: --batch requires an argument", file=sys.stderr)
            sys.exit(1)
        asyncio.run(seed(batch=batch))

    else:
        asyncio.run(seed())
