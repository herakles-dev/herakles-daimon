"""
Metadata-only re-tagger for videos with default/unknown tags.

By default uses Gemini Flash (title + channel + description) to generate ADHD tags.
With --local, uses the local analysis pipeline (audio_analyzer + ast_tagger +
audio_embeddings) for tracks that have a file_path stored in the database.

Usage:
    python -m scraper.retag                        # Gemini (default, legacy)
    python -m scraper.retag --gemini               # Explicit Gemini mode
    python -m scraper.retag --local                # Local analysis pipeline
    python -m scraper.retag --limit 20 --concurrency 5
    python -m scraper.retag --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import List, Optional

from google import genai
from google.genai import types as genai_types

# Ensure the backend root is on the path so `db` is importable when run
# as  python -m scraper.retag  from /app inside the container.
sys.path.insert(0, "/app")

from db import ensure_schema, execute, fetch_all, fetch_one

logger = logging.getLogger("play-backend.retag")

# ---------------------------------------------------------------------------
# Local-pipeline re-tagger (--local flag)
# ---------------------------------------------------------------------------


async def _retag_local(
    videos: list[dict],
    concurrency: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Re-tag videos using the local analysis pipeline (no Gemini API).

    For each video the track's audio file_path is looked up from the tracks
    table.  If no file_path exists (video-only content) the entry is skipped.

    Returns (success_count, error_count).
    """
    from analysis_pipeline import analyze_track, save_analysis  # type: ignore[import]

    semaphore = asyncio.Semaphore(concurrency)
    success = 0
    errors = 0
    total = len(videos)

    async def _process_one(row: dict) -> None:
        nonlocal success, errors
        async with semaphore:
            video_id = row["id"]
            title_short = (row.get("title") or "")[:60]

            # Look up file_path from tracks table (may not exist for video-only content)
            track_row = await fetch_one(
                "SELECT id, file_path FROM tracks WHERE source_id = $1",
                str(video_id),
            )

            if not track_row or not track_row["file_path"]:
                logger.warning(
                    "No audio file for video_id=%d '%s' — cannot run local analysis",
                    video_id, title_short,
                )
                errors += 1
                return

            file_path = track_row["file_path"]
            track_db_id: int = track_row["id"]

            if dry_run:
                logger.info(
                    "[DRY RUN] Would locally analyse video_id=%d '%s' (file=%s)",
                    video_id, title_short, file_path,
                )
                success += 1
                return

            try:
                result = await analyze_track(file_path)
                await save_analysis(track_db_id, result)

                # Also update video_tags mood/genre from local analysis
                await execute(
                    """INSERT INTO video_tags
                           (video_id, vibe, mood_tags, content_tags, claude_summary)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (video_id) DO UPDATE SET
                           vibe          = EXCLUDED.vibe,
                           mood_tags     = EXCLUDED.mood_tags,
                           content_tags  = EXCLUDED.content_tags,
                           claude_summary = EXCLUDED.claude_summary,
                           tagged_at     = NOW()""",
                    video_id,
                    "local-analysis",
                    result.mood_tags,
                    result.genre_tags,
                    f"Local analysis — key: {result.key}, BPM: {result.bpm:.0f}, "
                    f"energy: {result.energy}/10.",
                )
                logger.info(
                    "Locally tagged video_id=%d '%s': bpm=%.1f key=%s energy=%d errors=%s",
                    video_id, title_short, result.bpm, result.key, result.energy,
                    result.errors or "none",
                )
                success += 1
            except Exception as exc:
                logger.error("Local analysis failed for video_id=%d '%s': %s", video_id, title_short, exc)
                errors += 1

            done = success + errors
            if done % 10 == 0:
                logger.info("Progress: %d/%d (success=%d errors=%d)", done, total, success, errors)

    tasks = [asyncio.create_task(_process_one(row)) for row in videos]
    await asyncio.gather(*tasks)
    return success, errors

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert content analyst specialising in ADHD-friendly media curation.
Your job is to analyse YouTube video metadata (title, channel, description, duration) and output a
precise JSON object describing the video's ADHD relevance and content taxonomy.

Since you have no transcript, use channel reputation, title phrasing, description style,
and duration to make your best inference.

ADHD METRIC DEFINITIONS
-----------------------
pacing (1-10):
  How fast information is delivered. Consider channel style and title energy.
  10 = hyper-fast (Fireship 100-second videos), 1 = slow lecture / meditation.

stimulation (1-10):
  Total audio + visual sensory load estimated from channel style.
  10 = constant music, fast cuts, animations; 1 = static talking head, no music.

novelty (1-10):
  How unexpected or niche the content is.
  10 = deeply obscure or counterintuitive deep-dive; 1 = very common beginner topic.

VIBE TAXONOMY (pick the closest match or invent a new hyphenated label):
  late-night-rabbit-hole | high-energy-tutorial | background-ambiance |
  deep-dive-explainer | visual-spectacle | talking-head | hands-on-build |
  news-update | comedy-tech | ambient-visual | satisfying-build |
  science-explainer | math-visual | dev-rant | wholesome-adventure

OUTPUT FORMAT — respond ONLY with valid JSON, no markdown fences, no extra text:
{
  "pacing": <1-10 int>,
  "stimulation": <1-10 int>,
  "novelty": <1-10 int>,
  "vibe": "<hyphenated-string>",
  "mood_tags": ["<tag>", ...],
  "content_tags": ["<tag>", ...],
  "claude_summary": "<1-2 sentences>"
}"""


def _build_user_prompt(row: dict) -> str:
    title = row.get("title") or "Unknown Title"
    channel = row.get("channel") or "Unknown Channel"
    description = (row.get("description") or "")[:1000]
    duration = row.get("duration_sec") or 0

    parts = [
        f"TITLE: {title}",
        f"CHANNEL: {channel}",
        f"DURATION: {duration} seconds",
    ]
    if description:
        parts.append(f"\nDESCRIPTION (truncated to 1000 chars):\n{description}")
    else:
        parts.append("\nDESCRIPTION: not available")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON parsing helpers (identical pattern to tagger.py)
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {raw[:200]}")
    return json.loads(match.group(0))


def _safe_int(val, default: int = 5) -> int:
    try:
        return max(1, min(10, int(val)))
    except (TypeError, ValueError):
        return default


def _safe_list(val) -> List[str]:
    if isinstance(val, list):
        return [str(item) for item in val]
    if isinstance(val, str):
        return [val] if val else []
    return []


# ---------------------------------------------------------------------------
# Single-video tagging
# ---------------------------------------------------------------------------

async def _tag_one(
    client: genai.Client,
    row: dict,
    model: str,
) -> Optional[dict]:
    """
    Call Gemini for one video row.  Returns a dict of tag values, or None on error.
    """
    user_prompt = _build_user_prompt(row)
    video_id = row["id"]
    title_short = (row.get("title") or "")[:60]

    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=model,
                contents=genai_types.Content(
                    parts=[genai_types.Part(text=user_prompt)]
                ),
                config=genai_types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    max_output_tokens=2048,
                    temperature=0.3,
                ),
            ),
        )
    except Exception as exc:
        logger.error("Gemini API error for video_id=%d '%s': %s", video_id, title_short, exc)
        return None

    raw_text = response.text if response.text else ""
    tokens = 0
    if response.usage_metadata:
        tokens = (response.usage_metadata.prompt_token_count or 0) + (
            response.usage_metadata.candidates_token_count or 0
        )

    try:
        parsed = _parse_response(raw_text)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error(
            "Parse error for video_id=%d '%s': %s | raw: %s",
            video_id, title_short, exc, raw_text[:300],
        )
        return None

    tags = {
        "pacing":       _safe_int(parsed.get("pacing")),
        "stimulation":  _safe_int(parsed.get("stimulation")),
        "novelty":      _safe_int(parsed.get("novelty")),
        "vibe":         str(parsed.get("vibe") or "unknown"),
        "mood_tags":    _safe_list(parsed.get("mood_tags")),
        "content_tags": _safe_list(parsed.get("content_tags")),
        "claude_summary": str(parsed.get("claude_summary") or ""),
    }

    logger.info(
        "Tagged video_id=%d '%s': pacing=%d stim=%d novelty=%d vibe=%s (%d tokens)",
        video_id, title_short,
        tags["pacing"], tags["stimulation"], tags["novelty"], tags["vibe"], tokens,
    )
    return tags


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

async def _retag_all(
    videos: List[dict],
    model: str,
    concurrency: int,
    dry_run: bool,
) -> tuple[int, int]:
    """
    Process all videos with semaphore-limited concurrency.
    Returns (success_count, error_count).
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key and not dry_run:
        logger.error("GEMINI_API_KEY not set — cannot tag videos.")
        return 0, len(videos)

    client = genai.Client(api_key=api_key)
    semaphore = asyncio.Semaphore(concurrency)
    success = 0
    errors = 0
    total = len(videos)

    async def _process_one(idx: int, row: dict) -> None:
        nonlocal success, errors
        async with semaphore:
            if dry_run:
                logger.info(
                    "[DRY RUN] Would tag video_id=%d '%s'",
                    row["id"], (row.get("title") or "")[:60],
                )
                success += 1
                return

            tags = await _tag_one(client, row, model)
            if tags is None:
                errors += 1
                return

            try:
                await execute(
                    """
                    INSERT INTO video_tags
                        (video_id, pacing, stimulation, novelty, vibe,
                         mood_tags, content_tags, claude_summary)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (video_id) DO UPDATE SET
                        pacing        = EXCLUDED.pacing,
                        stimulation   = EXCLUDED.stimulation,
                        novelty       = EXCLUDED.novelty,
                        vibe          = EXCLUDED.vibe,
                        mood_tags     = EXCLUDED.mood_tags,
                        content_tags  = EXCLUDED.content_tags,
                        claude_summary = EXCLUDED.claude_summary,
                        tagged_at     = NOW()
                    """,
                    row["id"],
                    tags["pacing"],
                    tags["stimulation"],
                    tags["novelty"],
                    tags["vibe"],
                    tags["mood_tags"],
                    tags["content_tags"],
                    tags["claude_summary"],
                )
                success += 1
            except Exception as exc:
                logger.error("DB write failed for video_id=%d: %s", row["id"], exc)
                errors += 1

            # Progress log every 10 completions
            done = success + errors
            if done % 10 == 0:
                logger.info("Progress: %d/%d (success=%d errors=%d)", done, total, success, errors)

    tasks = [asyncio.create_task(_process_one(i, row)) for i, row in enumerate(videos)]
    await asyncio.gather(*tasks)

    return success, errors


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-tag videos using title+description only (no transcript)",
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Max videos to retag (default: all untagged)")
    parser.add_argument("--concurrency", type=int, default=3, metavar="N",
                        help="Parallel Claude calls (default: 3)")
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="Gemini model to use")
    parser.add_argument("--all", dest="retag_all", action="store_true",
                        help="Retag ALL videos, not just vibe=unknown ones")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip actual API calls and DB writes")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG logging")

    # Analysis backend selection — mutually exclusive, default is Gemini (legacy)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--local", action="store_true",
        help="Use local analysis pipeline (audio_analyzer + ast_tagger + audio_embeddings) "
             "instead of Gemini Flash.  Requires audio files on disk.",
    )
    mode_group.add_argument(
        "--gemini", action="store_true",
        help="Use Gemini Flash API for tagging (default legacy behaviour).",
    )

    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    await ensure_schema()

    # Fetch videos needing retagging
    if args.retag_all:
        base_query = """
            SELECT v.id, v.title, v.channel, v.description, v.duration_sec
            FROM videos v
        """
    else:
        base_query = """
            SELECT v.id, v.title, v.channel, v.description, v.duration_sec
            FROM videos v
            LEFT JOIN video_tags vt ON vt.video_id = v.id
            WHERE vt.video_id IS NULL
               OR vt.vibe = 'unknown'
               OR vt.vibe IS NULL
               OR array_length(vt.mood_tags, 1) IS NULL
               OR array_length(vt.mood_tags, 1) = 0
        """

    if args.limit:
        base_query += f" LIMIT {args.limit}"

    rows = await fetch_all(base_query)
    videos = [dict(r) for r in rows]

    use_local = args.local  # --local flag selects local pipeline

    logger.info(
        "Found %d video(s) to retag (backend=%s model=%s concurrency=%d dry_run=%s)",
        len(videos),
        "local" if use_local else "gemini",
        args.model,
        args.concurrency,
        args.dry_run,
    )

    if not videos:
        print("Nothing to retag — all videos already have tags.")
        return 0

    if use_local:
        success, errors = await _retag_local(
            videos=videos,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
        )
    else:
        success, errors = await _retag_all(
            videos=videos,
            model=args.model,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
        )

    print(f"\n{'='*60}")
    print(f"  Retag complete")
    print(f"  Total processed : {len(videos)}")
    print(f"  Success         : {success}")
    print(f"  Errors          : {errors}")
    print(f"{'='*60}\n")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
