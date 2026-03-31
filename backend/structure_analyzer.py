"""
Herakles Play — Song Structure Analyzer

Performs self-similarity-based segmentation of audio tracks to identify
structural sections (intro, verse, chorus, bridge, drop, outro, etc.).

Algorithm:
  1. Load audio via librosa (mono, 22050 Hz)
  2. Extract MFCC, chroma, and spectral contrast features
  3. Combine features into a single frame-level descriptor
  4. Build a self-similarity matrix (SSM)
  5. Detect segment boundaries via librosa.segment.agglomerative
  6. Label segments heuristically by energy and recurrence
  7. Merge segments shorter than MIN_SEGMENT_SEC with their neighbor
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List

import numpy as np

logger = logging.getLogger("play-backend.structure-analyzer")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MIN_SEGMENT_SEC: float = 4.0        # Segments shorter than this are merged
VERY_SHORT_TRACK_SEC: float = 10.0  # Tracks this short get a single segment
SAMPLE_RATE: int = 22050            # librosa default
HOP_LENGTH: int = 512               # Frames ≈ 23 ms each at 22050 Hz
N_MFCC: int = 20
N_CHROMA: int = 12
N_CONTRAST: int = 7

ALLOWED_LABELS = frozenset({
    "intro", "verse", "chorus", "bridge", "drop",
    "outro", "instrumental", "breakdown",
})


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Segment:
    label: str          # One of ALLOWED_LABELS
    start_sec: float
    end_sec: float
    confidence: float   # 0-1


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_structure(file_path: str) -> List[Segment]:
    """Return a list of Segments covering the entire track with no gaps.

    Runs the CPU-bound librosa work in a thread pool executor so the
    asyncio event loop remains responsive.

    Edge cases handled:
    - Track shorter than VERY_SHORT_TRACK_SEC → single "full" segment
    - Silent or near-silent audio → single "instrumental" segment
    - Mono and stereo inputs both accepted
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _analyze_sync, file_path)


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous implementation (runs in executor)
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_sync(file_path: str) -> List[Segment]:
    """Blocking implementation; call via analyze_structure for async use."""
    try:
        import librosa
    except ImportError as exc:
        raise RuntimeError(
            "librosa is required for structure analysis. "
            "Install it with: pip install librosa"
        ) from exc

    logger.info("Loading audio: %s", file_path)

    # Load as mono at the standard sample rate.
    try:
        y, sr = librosa.load(file_path, sr=SAMPLE_RATE, mono=True)
    except Exception as exc:
        logger.error("Failed to load audio file %s: %s", file_path, exc)
        raise

    duration_sec = float(len(y)) / sr
    logger.info("Duration: %.1f s", duration_sec)

    # Edge case: very short tracks.
    if duration_sec < VERY_SHORT_TRACK_SEC:
        logger.info("Track too short (%.1f s), returning single segment", duration_sec)
        return [Segment(label="instrumental", start_sec=0.0, end_sec=duration_sec, confidence=1.0)]

    # Edge case: near-silence.
    rms_global = float(np.sqrt(np.mean(y ** 2)))
    if rms_global < 1e-5:
        logger.warning("Audio appears silent (rms=%.2e); returning single segment", rms_global)
        return [Segment(label="instrumental", start_sec=0.0, end_sec=duration_sec, confidence=1.0)]

    # ── Feature extraction ───────────────────────────────────────────────────

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC, hop_length=HOP_LENGTH)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP_LENGTH)
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=HOP_LENGTH)

    # Normalise each feature block to unit variance for balanced contribution.
    def _normalise(feat: np.ndarray) -> np.ndarray:
        std = feat.std(axis=1, keepdims=True)
        std[std < 1e-8] = 1.0
        return (feat - feat.mean(axis=1, keepdims=True)) / std

    combined = np.vstack([_normalise(mfcc), _normalise(chroma), _normalise(contrast)])
    n_frames = combined.shape[1]

    # ── Self-similarity matrix ───────────────────────────────────────────────

    # Recurrence matrix with cosine affinity; bounded lag prevents spurious
    # long-range links on very long tracks.
    try:
        rec = librosa.segment.recurrence_matrix(
            combined,
            width=max(3, int(SAMPLE_RATE * 8 / HOP_LENGTH)),   # ~8 s lag window
            metric="cosine",
            sym=True,
        ).astype(float)
    except Exception as exc:
        logger.warning("Recurrence matrix failed (%s); falling back to identity", exc)
        rec = np.eye(n_frames)

    # ── Segment boundary detection ───────────────────────────────────────────

    # Target roughly one boundary every ~10 seconds; clamp to [3, 20].
    k = int(np.clip(duration_sec / 10, 3, 20))

    try:
        boundary_frames = librosa.segment.agglomerative(rec, k=k)
        boundary_frames = np.sort(boundary_frames)
    except Exception as exc:
        logger.warning("Agglomerative segmentation failed (%s); using uniform split", exc)
        boundary_frames = np.linspace(0, n_frames, k + 1, dtype=int)[1:-1]

    # Convert frames to seconds.
    boundary_sec = librosa.frames_to_time(
        np.concatenate([[0], boundary_frames, [n_frames]]),
        sr=sr,
        hop_length=HOP_LENGTH,
    )
    boundary_sec = np.clip(boundary_sec, 0.0, duration_sec)
    boundary_sec[-1] = duration_sec   # Ensure last segment reaches end exactly.

    # ── RMS energy per segment ───────────────────────────────────────────────

    rms_frames = librosa.feature.rms(y=y, hop_length=HOP_LENGTH)[0]

    def _segment_rms(start_f: int, end_f: int) -> float:
        end_f = min(end_f, len(rms_frames))
        if start_f >= end_f:
            return 0.0
        return float(np.mean(rms_frames[start_f:end_f]))

    # ── Build raw segments ───────────────────────────────────────────────────

    raw_segments: list[dict] = []
    for i in range(len(boundary_sec) - 1):
        start_s = float(boundary_sec[i])
        end_s = float(boundary_sec[i + 1])
        start_f = librosa.time_to_frames(start_s, sr=sr, hop_length=HOP_LENGTH)
        end_f = librosa.time_to_frames(end_s, sr=sr, hop_length=HOP_LENGTH)
        seg_rms = _segment_rms(start_f, end_f)
        raw_segments.append({"start": start_s, "end": end_s, "rms": seg_rms, "idx": i})

    # ── Merge short segments ─────────────────────────────────────────────────

    raw_segments = _merge_short(raw_segments, min_dur=MIN_SEGMENT_SEC, total_dur=duration_sec)

    # ── Heuristic labelling ──────────────────────────────────────────────────

    labeled = _label_segments(raw_segments, rms_frames=rms_frames, sr=sr, duration_sec=duration_sec)

    logger.info("Structure analysis complete: %d segments", len(labeled))
    return labeled


# ─────────────────────────────────────────────────────────────────────────────
# Merge short segments
# ─────────────────────────────────────────────────────────────────────────────

def _merge_short(
    segs: list[dict],
    min_dur: float,
    total_dur: float,
) -> list[dict]:
    """Merge segments shorter than min_dur with the closest neighbor.

    Iterates until no short segments remain.  A single segment is never
    further split and is returned as-is (it covers the whole track).
    """
    if len(segs) <= 1:
        return segs

    changed = True
    while changed:
        changed = False
        merged: list[dict] = []
        i = 0
        while i < len(segs):
            seg = segs[i]
            dur = seg["end"] - seg["start"]
            if dur < min_dur and len(segs) > 1:
                # Merge with neighbor that has lower RMS delta (more similar).
                if i == 0:
                    neighbor = 1
                elif i == len(segs) - 1:
                    neighbor = i - 1
                else:
                    left_rms_diff = abs(segs[i - 1]["rms"] - seg["rms"])
                    right_rms_diff = abs(segs[i + 1]["rms"] - seg["rms"])
                    neighbor = i - 1 if left_rms_diff <= right_rms_diff else i + 1

                if neighbor < i:
                    # Merge into previous.
                    merged[-1]["end"] = seg["end"]
                    merged[-1]["rms"] = (merged[-1]["rms"] + seg["rms"]) / 2
                    segs = merged + segs[i + 1:]
                else:
                    # Merge into next.
                    segs[neighbor]["start"] = seg["start"]
                    segs[neighbor]["rms"] = (segs[neighbor]["rms"] + seg["rms"]) / 2
                    segs = segs[:i] + segs[i + 1:]
                changed = True
                # Restart iteration after structural change.
                break
            else:
                merged.append(seg)
                i += 1
        else:
            break

    return segs


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic labelling
# ─────────────────────────────────────────────────────────────────────────────

def _label_segments(
    segs: list[dict],
    rms_frames: np.ndarray,
    sr: int,
    duration_sec: float,
) -> List[Segment]:
    """Assign structural labels based on energy patterns and position.

    Heuristics (applied in order of precedence):
    1. First segment, energy below median → "intro"
    2. Last segment, energy declining vs. previous → "outro"
    3. Very low energy non-boundary segment → "breakdown" or "bridge"
    4. Most repeated energy tier → "chorus"
    5. Sections between choruses → "verse"
    6. High energy peaks (above 75th percentile) → "drop"
    7. Everything else → "instrumental"
    """
    if not segs:
        return []

    n = len(segs)
    rms_vals = np.array([s["rms"] for s in segs])
    median_rms = float(np.median(rms_vals))
    p75_rms = float(np.percentile(rms_vals, 75))
    p25_rms = float(np.percentile(rms_vals, 25))

    labels = ["instrumental"] * n

    # Pass 1 — position-based intro/outro.
    if n >= 2:
        if rms_vals[0] < median_rms * 0.9:
            labels[0] = "intro"
        if rms_vals[-1] < rms_vals[-2] * 0.95:
            labels[-1] = "outro"

    # Pass 2 — high-energy peaks → "drop".
    for i in range(n):
        if rms_vals[i] >= p75_rms and labels[i] == "instrumental":
            labels[i] = "drop"

    # Pass 3 — low-energy interior sections → "breakdown" or "bridge".
    for i in range(1, n - 1):
        if rms_vals[i] <= p25_rms and labels[i] == "instrumental":
            # "bridge" if surrounded by higher-energy segments, else "breakdown"
            left_high = rms_vals[i - 1] > median_rms
            right_high = rms_vals[i + 1] > median_rms if i + 1 < n else False
            labels[i] = "bridge" if (left_high and right_high) else "breakdown"

    # Pass 4 — cluster remaining "instrumental" into "chorus" and "verse"
    # by treating the middle energy tier as repeated chorus sections.
    # "Chorus" = segments whose RMS is close to the upper-mid tier (between
    # median and p75). "Verse" = segments in the lower-mid tier or unlabelled.
    chorus_threshold = median_rms + 0.4 * (p75_rms - median_rms)
    for i in range(n):
        if labels[i] != "instrumental":
            continue
        if rms_vals[i] >= chorus_threshold:
            labels[i] = "chorus"
        else:
            labels[i] = "verse"

    # ── Confidence ────────────────────────────────────────────────────────────
    # Rough confidence: how far the RMS deviates from the boundary between
    # the segment's tier and the next.  Normalised to [0.5, 1.0].
    rms_range = max(float(rms_vals.max() - rms_vals.min()), 1e-8)
    confidences = np.clip(
        0.5 + 0.5 * np.abs(rms_vals - median_rms) / rms_range, 0.5, 1.0
    )

    result: List[Segment] = []
    for i, seg in enumerate(segs):
        label = labels[i]
        if label not in ALLOWED_LABELS:
            label = "instrumental"
        result.append(
            Segment(
                label=label,
                start_sec=round(float(seg["start"]), 3),
                end_sec=round(float(seg["end"]), 3),
                confidence=round(float(confidences[i]), 3),
            )
        )

    return result
