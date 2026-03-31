"""
Beat Analyzer — beat-level analysis for crossfade timing.

Provides async beat detection via librosa and utilities to find optimal
crossfade points within a given playback window.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("play-backend.beat-analyzer")


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BeatInfo:
    beat_times: list[float]       # seconds — position of each beat
    downbeat_times: list[float]   # seconds — bar-level (every 4th beat approx.)
    tempo: float                  # BPM
    beat_strength: list[float]    # 0-1 confidence per beat


@dataclass
class CrossfadePoint:
    time: float          # seconds — best point to crossfade
    beat_index: int      # index into BeatInfo.beat_times
    is_downbeat: bool    # True when this beat is also a downbeat
    confidence: float    # 0-1, derived from beat_strength


# ─────────────────────────────────────────────────────────────────────────────
# Beat detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_beats_sync(file_path: str) -> BeatInfo:
    """CPU-bound librosa work — must be run inside run_in_executor."""
    import librosa  # imported here so the module loads even when librosa is absent

    logger.info("Loading audio for beat detection: %s", file_path)

    # Load mono, native sample rate for speed; limit to 10 minutes to avoid OOM.
    y, sr = librosa.load(file_path, mono=True, duration=600.0)

    if len(y) == 0:
        logger.warning("Audio file is empty: %s", file_path)
        return BeatInfo(beat_times=[], downbeat_times=[], tempo=0.0, beat_strength=[])

    # Beat tracking — returns (tempo_scalar, beat_frames)
    tempo_arr, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")

    # Normalise tempo to a Python float (numpy scalar in some librosa versions)
    if hasattr(tempo_arr, "__len__"):
        tempo = float(tempo_arr[0]) if len(tempo_arr) > 0 else 0.0
    else:
        tempo = float(tempo_arr)

    if len(beat_frames) == 0:
        logger.warning("No beats detected in: %s", file_path)
        return BeatInfo(beat_times=[], downbeat_times=[], tempo=tempo, beat_strength=[])

    # Convert frame indices → seconds
    beat_times: list[float] = [
        float(librosa.frames_to_time(f, sr=sr)) for f in beat_frames
    ]

    # Onset strength envelope — used as per-beat confidence
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    # Sample onset strength at each beat frame; clip to valid range
    strengths: list[float] = []
    for f in beat_frames:
        idx = min(int(f), len(onset_env) - 1)
        strengths.append(float(onset_env[idx]))

    # Normalise strengths to [0, 1]
    max_strength = max(strengths) if strengths else 1.0
    if max_strength > 0:
        strengths = [s / max_strength for s in strengths]

    # Downbeat estimation — every 4th beat starting at the first beat.
    # librosa does not expose bar-level structure without a full beat-tracking
    # model, so we approximate: group beats in sets of 4 and mark the first.
    downbeat_times: list[float] = []
    beats_per_bar = 4
    for i in range(0, len(beat_times), beats_per_bar):
        downbeat_times.append(beat_times[i])

    logger.info(
        "Beat detection complete: %.1f BPM, %d beats, %d downbeats — %s",
        tempo,
        len(beat_times),
        len(downbeat_times),
        file_path,
    )

    return BeatInfo(
        beat_times=beat_times,
        downbeat_times=downbeat_times,
        tempo=tempo,
        beat_strength=strengths,
    )


async def detect_beats(file_path: str) -> BeatInfo:
    """Async entry point — offloads CPU-bound librosa work to a thread executor.

    Args:
        file_path: Absolute or relative path to the audio file.

    Returns:
        BeatInfo with beat positions, downbeat positions, tempo, and strength.
        Returns an empty BeatInfo (all lists empty, tempo 0) on failure.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _detect_beats_sync, file_path)
    except FileNotFoundError:
        logger.error("Audio file not found: %s", file_path)
        return BeatInfo(beat_times=[], downbeat_times=[], tempo=0.0, beat_strength=[])
    except Exception as exc:
        logger.error("Beat detection failed for %s: %s", file_path, exc)
        return BeatInfo(beat_times=[], downbeat_times=[], tempo=0.0, beat_strength=[])


# ─────────────────────────────────────────────────────────────────────────────
# Crossfade point selection
# ─────────────────────────────────────────────────────────────────────────────

def find_crossfade_points(
    beat_info: BeatInfo,
    position_sec: float,
    window_sec: float = 4.0,
) -> list[CrossfadePoint]:
    """Return beat boundaries near *position_sec* within ±window_sec.

    Downbeats are preferred (higher base confidence).  All beats are
    sorted by distance from the requested position so callers can pick the
    nearest one.

    Args:
        beat_info:    BeatInfo returned by detect_beats().
        position_sec: Current playback position in seconds.
        window_sec:   Half-width of the search window (default 4 s each side).

    Returns:
        List of CrossfadePoint sorted by proximity to position_sec.
        Empty list when no beats exist or none fall within the window.
    """
    if not beat_info.beat_times:
        logger.debug("find_crossfade_points: no beats available")
        return []

    downbeat_set = set(beat_info.downbeat_times)
    lo = position_sec - window_sec
    hi = position_sec + window_sec

    candidates: list[CrossfadePoint] = []
    for idx, t in enumerate(beat_info.beat_times):
        if t < lo or t > hi:
            continue

        is_downbeat = t in downbeat_set
        base_strength = (
            beat_info.beat_strength[idx]
            if idx < len(beat_info.beat_strength)
            else 0.5
        )
        # Boost confidence for downbeats
        confidence = min(1.0, base_strength * (1.3 if is_downbeat else 1.0))

        candidates.append(
            CrossfadePoint(
                time=t,
                beat_index=idx,
                is_downbeat=is_downbeat,
                confidence=confidence,
            )
        )

    # Sort by distance from requested position (nearest first)
    candidates.sort(key=lambda cp: abs(cp.time - position_sec))

    logger.debug(
        "find_crossfade_points: position=%.2fs window=%.1fs → %d candidates",
        position_sec,
        window_sec,
        len(candidates),
    )
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Serialization helpers
# ─────────────────────────────────────────────────────────────────────────────

def beat_info_to_dict(beat_info: BeatInfo) -> dict:
    """Convert BeatInfo to a JSON-serialisable dict for DB storage."""
    return {
        "beat_times": beat_info.beat_times,
        "downbeat_times": beat_info.downbeat_times,
        "tempo": beat_info.tempo,
        "beat_strength": beat_info.beat_strength,
    }


def beat_info_from_dict(data: dict) -> BeatInfo:
    """Reconstruct BeatInfo from a previously serialised dict."""
    return BeatInfo(
        beat_times=data.get("beat_times", []),
        downbeat_times=data.get("downbeat_times", []),
        tempo=float(data.get("tempo", 0.0)),
        beat_strength=data.get("beat_strength", []),
    )
