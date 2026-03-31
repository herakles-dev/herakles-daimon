"""Unified audio analysis pipeline for Herakles Play — Sprint 11 Audio Intelligence (S11.4).

Orchestrates all three local analyzers in parallel and persists results to the database.
Replaces Gemini Flash for audio tagging, using fully local inference.

Public interface
----------------
analyze_track(file_path)            → AnalysisResult
save_analysis(track_id, result)     → None

CLI usage
---------
python -m analysis_pipeline --track-id 42
python -m analysis_pipeline --file /music/cache/jamendo/12345.mp3
python -m analysis_pipeline --track-id 42 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Ensure /app is on the path when run inside the container
sys.path.insert(0, "/app")

logger = logging.getLogger("play-backend.analysis-pipeline")

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    """Aggregated output from all three local analyzers."""

    # From audio_analyzer (AudioFeatures)
    bpm: float = 120.0
    key: str = "C major"
    camelot_code: str = "8B"
    energy: int = 5
    brightness: int = 5
    danceability: int = 5

    # From ast_tagger (ASTResult)
    genre_tags: list[str] = field(default_factory=list)
    mood_tags: list[str] = field(default_factory=list)
    instrument_tags: list[str] = field(default_factory=list)

    # From audio_embeddings — None when the embeddings module fails
    audio_embedding: Optional[np.ndarray] = field(default=None, repr=False)

    # Metadata
    analysis_source: str = "local"
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


async def analyze_track(file_path: str) -> AnalysisResult:
    """Run all three analyzers in parallel. Partial failures produce partial results.

    Each analyzer runs concurrently via asyncio.gather(return_exceptions=True).
    If a sub-analyzer raises, its section of the result is populated with
    safe defaults and the exception is recorded in AnalysisResult.errors.

    Args:
        file_path: Path to the audio file (MP3, FLAC, WAV, OGG, etc.)

    Returns:
        AnalysisResult — always succeeds; check .errors for failure details.
    """
    from audio_analyzer import analyze_audio
    from ast_tagger import classify_audio
    from audio_embeddings import embed_audio

    logger.info("Starting unified analysis for: %s", file_path)

    audio_coro   = analyze_audio(file_path)
    ast_coro     = classify_audio(file_path)
    embed_coro   = embed_audio(file_path)

    raw_audio, raw_ast, raw_embed = await asyncio.gather(
        audio_coro, ast_coro, embed_coro,
        return_exceptions=True,
    )

    result = AnalysisResult()

    # ── audio_analyzer ────────────────────────────────────────────────────
    if isinstance(raw_audio, Exception):
        msg = f"audio_analyzer failed: {raw_audio}"
        logger.warning(msg)
        result.errors.append(msg)
        # defaults already set by dataclass
    else:
        result.bpm         = raw_audio.bpm
        result.key         = raw_audio.key
        result.camelot_code = raw_audio.camelot_code
        result.energy      = raw_audio.energy
        result.brightness  = raw_audio.brightness
        result.danceability = raw_audio.danceability

    # ── ast_tagger ────────────────────────────────────────────────────────
    if isinstance(raw_ast, Exception):
        msg = f"ast_tagger failed: {raw_ast}"
        logger.warning(msg)
        result.errors.append(msg)
        # genre_tags / mood_tags / instrument_tags remain []
    else:
        result.genre_tags      = list(raw_ast.genre_tags)
        result.mood_tags       = list(raw_ast.mood_tags)
        result.instrument_tags = list(raw_ast.instrument_tags)

    # ── audio_embeddings ──────────────────────────────────────────────────
    if isinstance(raw_embed, Exception):
        msg = f"audio_embeddings failed: {raw_embed}"
        logger.warning(msg)
        result.errors.append(msg)
        result.audio_embedding = None
    else:
        result.audio_embedding = raw_embed

    if result.errors:
        logger.warning(
            "Analysis completed with %d partial failure(s) for %s",
            len(result.errors), file_path,
        )
    else:
        logger.info(
            "Analysis complete for %s — bpm=%.1f key=%s energy=%d "
            "genre=%s mood=%s embed_dim=%s",
            file_path,
            result.bpm,
            result.key,
            result.energy,
            result.genre_tags[:3],
            result.mood_tags[:3],
            result.audio_embedding.shape if result.audio_embedding is not None else "None",
        )

    return result


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


async def save_analysis(track_id: int, result: AnalysisResult) -> None:
    """Upsert analysis results into track_tags and media_embeddings.

    track_tags: upserted with bpm, musical_key, energy, danceability,
                mood_tags, genre_tags, analysis_source
    media_embeddings: upserted with audio_embedding vector(2048)
                      when an embedding is present.

    Args:
        track_id: Primary key of the track row.
        result:   AnalysisResult returned by analyze_track().
    """
    from db import execute

    logger.info("Saving analysis results for track_id=%d", track_id)

    # ── track_tags upsert ─────────────────────────────────────────────────
    await execute(
        """INSERT INTO track_tags
               (track_id, bpm, musical_key, energy, danceability,
                mood_tags, genre_tags, claude_summary, camelot_code, tag_source)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'local')
           ON CONFLICT (track_id) DO UPDATE SET
               bpm           = EXCLUDED.bpm,
               musical_key   = EXCLUDED.musical_key,
               energy        = EXCLUDED.energy,
               danceability  = EXCLUDED.danceability,
               mood_tags     = EXCLUDED.mood_tags,
               genre_tags    = EXCLUDED.genre_tags,
               claude_summary = EXCLUDED.claude_summary,
               camelot_code  = EXCLUDED.camelot_code,
               tag_source    = EXCLUDED.tag_source,
               tagged_at     = NOW()""",
        track_id,
        int(round(result.bpm)),
        result.key,
        int(result.energy),
        int(result.danceability),
        result.mood_tags,
        result.genre_tags,
        f"Local analysis — key: {result.key}, camelot: {result.camelot_code}, "
        f"brightness: {result.brightness}. "
        f"Instruments: {', '.join(result.instrument_tags) or 'none detected'}.",
        result.camelot_code,
    )
    logger.info("track_tags written for track_id=%d", track_id)

    # ── media_embeddings upsert (audio_embedding) ─────────────────────────
    if result.audio_embedding is None:
        logger.info(
            "No audio embedding available for track_id=%d — skipping media_embeddings write",
            track_id,
        )
        return

    embedding = result.audio_embedding
    if embedding.shape != (2048,):
        logger.warning(
            "Unexpected audio_embedding shape %s for track_id=%d — expected (2048,), skipping",
            embedding.shape, track_id,
        )
        return

    # Convert numpy floats to Python floats and build pgvector literal
    vec_literal = "[" + ",".join(str(float(v)) for v in embedding) + "]"

    try:
        await execute(
            """INSERT INTO media_embeddings
                   (media_type, track_id, audio_embedding, embedding_dim)
               VALUES ('track', $1, $2::vector, $3)
               ON CONFLICT (media_type, track_id) DO UPDATE SET
                   audio_embedding = EXCLUDED.audio_embedding,
                   embedding_dim   = EXCLUDED.embedding_dim,
                   created_at      = NOW()""",
            track_id,
            vec_literal,
            int(embedding.shape[0]),
        )
    except Exception as exc:
        # Fallback: try UPDATE path if the INSERT constraint fails
        logger.warning(
            "INSERT failed for track_id=%d audio_embedding (%s) — trying UPDATE",
            track_id, exc,
        )
        try:
            await execute(
                """UPDATE media_embeddings
                   SET audio_embedding = $2::vector,
                       embedding_dim   = $3,
                       created_at      = NOW()
                   WHERE media_type = 'track' AND track_id = $1""",
                track_id,
                vec_literal,
                int(embedding.shape[0]),
            )
        except Exception as exc2:
            logger.error(
                "DB write failed for track_id=%d audio_embedding: %s / %s",
                track_id, exc, exc2,
            )
            return

    logger.info(
        "audio_embedding (dim=%d) written for track_id=%d",
        int(embedding.shape[0]), track_id,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run local audio analysis pipeline on a single track.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m analysis_pipeline --track-id 42\n"
            "  python -m analysis_pipeline --file /music/cache/jamendo/12345.mp3\n"
            "  python -m analysis_pipeline --track-id 42 --dry-run\n"
        ),
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--track-id", type=int, metavar="N",
        help="Analyse the audio file whose DB track_id is N (looks up file_path from DB)",
    )
    source_group.add_argument(
        "--file", metavar="PATH",
        help="Analyse a specific audio file (no DB lookup, prints result only)",
    )

    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyse but do not write results to the database",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    if args.file:
        # File mode — no DB needed
        file_path = args.file
        logger.info("File mode: %s", file_path)

        result = await analyze_track(file_path)

        print("\n" + "=" * 60)
        print("  Analysis result")
        print("=" * 60)
        print(f"  BPM          : {result.bpm:.1f}")
        print(f"  Key          : {result.key}")
        print(f"  Camelot      : {result.camelot_code}")
        print(f"  Energy       : {result.energy}/10")
        print(f"  Brightness   : {result.brightness}/10")
        print(f"  Danceability : {result.danceability}/10")
        print(f"  Genre tags   : {result.genre_tags}")
        print(f"  Mood tags    : {result.mood_tags}")
        print(f"  Instruments  : {result.instrument_tags}")
        embed_dim = result.audio_embedding.shape[0] if result.audio_embedding is not None else "N/A"
        print(f"  Embed dim    : {embed_dim}")
        if result.errors:
            print(f"  Errors       : {result.errors}")
        print("=" * 60 + "\n")

        if args.dry_run:
            print("[DRY RUN] Not writing to database.")
        else:
            print("No --track-id provided — skipping DB write (file mode).")

        return 0

    # Track-ID mode
    from db import ensure_schema, fetch_one

    await ensure_schema()

    track_id = args.track_id
    row = await fetch_one(
        "SELECT id, title, file_path FROM tracks WHERE id = $1",
        track_id,
    )
    if not row:
        logger.error("No track found with id=%d", track_id)
        return 1

    title     = row["title"] or "Unknown"
    file_path = row["file_path"]
    if not file_path:
        logger.error("track_id=%d '%s' has no file_path — cannot analyse", track_id, title)
        return 1

    logger.info("Analysing track_id=%d '%s' at %s", track_id, title, file_path)
    result = await analyze_track(file_path)

    print("\n" + "=" * 60)
    print(f"  Analysis result for track_id={track_id} '{title}'")
    print("=" * 60)
    print(f"  BPM          : {result.bpm:.1f}")
    print(f"  Key          : {result.key}")
    print(f"  Camelot      : {result.camelot_code}")
    print(f"  Energy       : {result.energy}/10")
    print(f"  Brightness   : {result.brightness}/10")
    print(f"  Danceability : {result.danceability}/10")
    print(f"  Genre tags   : {result.genre_tags}")
    print(f"  Mood tags    : {result.mood_tags}")
    print(f"  Instruments  : {result.instrument_tags}")
    embed_dim = result.audio_embedding.shape[0] if result.audio_embedding is not None else "N/A"
    print(f"  Embed dim    : {embed_dim}")
    if result.errors:
        print(f"  Errors       : {result.errors}")
    print("=" * 60 + "\n")

    if args.dry_run:
        print("[DRY RUN] Not writing to database.")
        return 0

    await save_analysis(track_id, result)
    print(f"Results saved for track_id={track_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
