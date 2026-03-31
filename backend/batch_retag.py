"""Batch re-analysis pipeline for Herakles Play — Sprint 11 S11.5.

Re-analyzes all existing tracks with status='ready' using the local analysis
pipeline (analysis_pipeline.py). Replaces Gemini Flash tags with local ML
inference for all tracks in the library.

Public interface
----------------
retag_all(concurrency, force, delay)  -> dict

CLI usage
---------
python -m batch_retag
python -m batch_retag --force
python -m batch_retag --concurrency 4 --delay 0.5
python -m batch_retag --force --concurrency 1 --delay 2.0
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure /app is on the path when run inside the container
sys.path.insert(0, "/app")

logger = logging.getLogger("play-backend.batch-retag")

# Path for persisting resume progress across restarts
_PROGRESS_FILE = Path("/tmp/batch_retag_progress.json")


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def _load_progress() -> dict:
    """Load saved progress from disk. Returns empty dict on any error."""
    try:
        if _PROGRESS_FILE.exists():
            data = json.loads(_PROGRESS_FILE.read_text())
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning("Could not read progress file %s: %s", _PROGRESS_FILE, exc)
    return {}


def _save_progress(last_track_id: int, stats: dict) -> None:
    """Persist the last successfully processed track_id to disk."""
    try:
        _PROGRESS_FILE.write_text(
            json.dumps({"last_track_id": last_track_id, **stats})
        )
    except Exception as exc:
        logger.warning("Could not write progress file %s: %s", _PROGRESS_FILE, exc)


def _clear_progress() -> None:
    """Remove the progress file (called at the start of a fresh force-run)."""
    try:
        if _PROGRESS_FILE.exists():
            _PROGRESS_FILE.unlink()
    except Exception as exc:
        logger.warning("Could not remove progress file %s: %s", _PROGRESS_FILE, exc)


# ---------------------------------------------------------------------------
# Core batch function
# ---------------------------------------------------------------------------


async def retag_all(
    concurrency: int = 2,
    force: bool = False,
    delay: float = 1.0,
) -> dict:
    """Re-analyze all tracks with status='ready' using the local pipeline.

    Args:
        concurrency: Maximum parallel analyses (CPU-bound, keep low).
        force:       If True, re-analyze even if already tagged locally.
        delay:       Seconds between track analyses (CPU cooldown).

    Returns:
        {"total": N, "success": N, "failed": N, "skipped": N}
    """
    from analysis_pipeline import analyze_track, save_analysis
    from db import ensure_schema, execute as db_execute, fetch_all

    await ensure_schema()

    # Clear progress file early if force-rerun requested
    if force:
        _clear_progress()

    # ── fetch candidate tracks ──────────────────────────────────────────────
    tracks = await fetch_all(
        """SELECT id, title, file_path
           FROM tracks
           WHERE status = 'ready'
             AND file_path IS NOT NULL
           ORDER BY id ASC"""
    )

    total = len(tracks)
    if total == 0:
        logger.info("No ready tracks found — nothing to do.")
        return {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    logger.info("Found %d ready tracks to process.", total)

    # ── resume support ───────────────────────────────────────────────────────
    resume_from: int = 0
    if not force:
        progress = _load_progress()
        resume_from = int(progress.get("last_track_id", 0))
        if resume_from:
            logger.info(
                "Resuming from last_track_id=%d (use --force to restart from scratch)",
                resume_from,
            )
    else:
        pass  # force already cleared progress above

    # ── fetch already-locally-tagged track ids ───────────────────────────────
    already_tagged_rows = await fetch_all(
        "SELECT track_id FROM track_tags WHERE tag_source = 'local'"
    )
    already_tagged: set[int] = {int(row["track_id"]) for row in already_tagged_rows}

    # ── counters and semaphore ───────────────────────────────────────────────
    stats = {"success": 0, "failed": 0, "skipped": 0}
    sem = asyncio.Semaphore(concurrency)
    processed = 0

    # ── per-track worker ─────────────────────────────────────────────────────
    async def _process_track(track_id: int, title: str, file_path: str) -> None:
        nonlocal processed

        async with sem:
            # Resume skip: already processed in a previous interrupted run
            if not force and track_id <= resume_from:
                stats["skipped"] += 1
                processed += 1
                return

            # Source-based skip: already has a local tag
            if not force and track_id in already_tagged:
                logger.debug(
                    "track_id=%d '%s' already tagged locally — skipping",
                    track_id, title,
                )
                stats["skipped"] += 1
                processed += 1
                _log_progress(processed, total, stats, track_id)
                return

            try:
                result = await analyze_track(file_path)
                await save_analysis(track_id, result)
                # Mark the tag_source as 'local'
                await db_execute(
                    """UPDATE track_tags
                       SET tag_source = 'local', tagged_at = NOW()
                       WHERE track_id = $1""",
                    track_id,
                )
                stats["success"] += 1
                logger.info(
                    "track_id=%d '%s' — analysis complete (bpm=%.1f energy=%d)",
                    track_id, title, result.bpm, result.energy,
                )
            except Exception as exc:
                stats["failed"] += 1
                logger.error(
                    "track_id=%d '%s' — analysis FAILED: %s",
                    track_id, title, exc,
                )
            finally:
                processed += 1
                _log_progress(processed, total, stats, track_id)

            # CPU cooldown between tracks
            await asyncio.sleep(delay)

    def _log_progress(done: int, total: int, stats: dict, last_id: int) -> None:
        pct = int(done / total * 100) if total else 0
        logger.info(
            "Retagged %d/%d tracks (%d%%) — %d failed, %d skipped",
            done, total, pct, stats["failed"], stats["skipped"],
        )
        _save_progress(last_id, stats)

    # ── dispatch all tasks ───────────────────────────────────────────────────
    tasks = [
        _process_track(
            int(row["id"]),
            row["title"] or "Unknown",
            row["file_path"],
        )
        for row in tracks
    ]
    await asyncio.gather(*tasks)

    result_summary = {
        "total": total,
        "success": stats["success"],
        "failed": stats["failed"],
        "skipped": stats["skipped"],
    }

    logger.info(
        "Batch retag complete — total=%d success=%d failed=%d skipped=%d",
        total, stats["success"], stats["failed"], stats["skipped"],
    )

    # Clean up progress file on clean completion (no failures)
    if stats["failed"] == 0:
        _clear_progress()

    return result_summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Batch re-analyze all ready tracks using the local ML pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m batch_retag\n"
            "  python -m batch_retag --force\n"
            "  python -m batch_retag --concurrency 4 --delay 0.5\n"
            "  python -m batch_retag --force --concurrency 1 --delay 2.0\n"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyze even tracks already tagged locally",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        metavar="N",
        help="Maximum parallel analyses (default: 2, keep low for CPU-bound work)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Seconds to sleep between track analyses for CPU cooldown (default: 1.0)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    summary = asyncio.run(
        retag_all(
            concurrency=args.concurrency,
            force=args.force,
            delay=args.delay,
        )
    )

    print(
        f"\nBatch retag complete:\n"
        f"  Total    : {summary['total']}\n"
        f"  Success  : {summary['success']}\n"
        f"  Failed   : {summary['failed']}\n"
        f"  Skipped  : {summary['skipped']}\n"
    )

    # Exit non-zero if any tracks failed
    sys.exit(1 if summary["failed"] > 0 else 0)
