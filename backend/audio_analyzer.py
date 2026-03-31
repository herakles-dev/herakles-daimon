"""Audio analysis module for Herakles Play — Sprint 11 Audio Intelligence.

Extracts BPM, musical key, Camelot code, energy, brightness, and danceability
from audio files using librosa.  CPU-bound work is offloaded to a thread
executor so that the FastAPI event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("play-backend.audio-analyzer")

# ---------------------------------------------------------------------------
# Krumhansl-Schmuckler key profiles
# ---------------------------------------------------------------------------
# These 12-element profiles represent the perceived stability of each pitch
# class within a major or minor key, as established by Krumhansl & Schmuckler
# (1990).  Correlation of the chroma vector against all 24 rotations picks
# the most likely key.

_KS_MAJOR = np.array([
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
    2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
])
_KS_MINOR = np.array([
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
    2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
])

# Pitch-class names ordered chromatically starting at C
_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F",
                  "F#", "G", "G#", "A", "A#", "B"]

# ---------------------------------------------------------------------------
# Camelot wheel — maps (pitch_class_index, mode) → Camelot code
# mode: "major" or "minor"
# ---------------------------------------------------------------------------
# The Camelot wheel numbers 1–12 map to keys in a circle-of-fifths layout;
# "B" suffix = major (outer ring), "A" suffix = minor (inner ring).

_CAMELOT: dict[tuple[int, str], str] = {
    # Major (B)
    (0,  "major"): "8B",   # C major
    (1,  "major"): "3B",   # C# / Db major
    (2,  "major"): "10B",  # D major
    (3,  "major"): "5B",   # D# / Eb major
    (4,  "major"): "12B",  # E major
    (5,  "major"): "7B",   # F major
    (6,  "major"): "2B",   # F# / Gb major
    (7,  "major"): "9B",   # G major
    (8,  "major"): "4B",   # G# / Ab major
    (9,  "major"): "11B",  # A major
    (10, "major"): "6B",   # A# / Bb major
    (11, "major"): "1B",   # B major
    # Minor (A)
    (0,  "minor"): "5A",   # C minor
    (1,  "minor"): "12A",  # C# / Db minor
    (2,  "minor"): "7A",   # D minor
    (3,  "minor"): "2A",   # D# / Eb minor
    (4,  "minor"): "9A",   # E minor
    (5,  "minor"): "4A",   # F minor
    (6,  "minor"): "11A",  # F# / Gb minor
    (7,  "minor"): "6A",   # G minor
    (8,  "minor"): "1A",   # G# / Ab minor
    (9,  "minor"): "8A",   # A minor
    (10, "minor"): "3A",   # A# / Bb minor
    (11, "minor"): "10A",  # B minor
}

# ---------------------------------------------------------------------------
# Energy RMS percentile thresholds (calibrated against a broad corpus)
# These map RMS dB values (typically −40 to 0 dBFS) onto the 1-10 scale.
# Values below −35 dBFS → 1; values above −5 dBFS → 10.
# ---------------------------------------------------------------------------
_ENERGY_THRESHOLDS_DB = np.linspace(-35.0, -5.0, 9)  # 9 boundaries → 10 bands

# Spectral centroid thresholds in Hz (20 Hz – 20 kHz audible range).
# Low centroid (< ~800 Hz) = dark/bass-heavy → 1.
# High centroid (> ~8 kHz) = bright/airy → 10.
_BRIGHTNESS_THRESHOLDS_HZ = np.linspace(800.0, 8000.0, 9)

# ---------------------------------------------------------------------------
# DataClass
# ---------------------------------------------------------------------------


@dataclass
class AudioFeatures:
    """Perceptual audio features extracted by librosa."""

    bpm: float
    key: str           # e.g. "C major" or "A minor"
    camelot_code: str  # e.g. "8B" or "8A"
    energy: int        # 1 (silence) – 10 (loud)
    brightness: int    # 1 (dark/bass) – 10 (bright/airy)
    danceability: int  # 1 (non-rhythmic) – 10 (highly danceable)


# ---------------------------------------------------------------------------
# Internal helpers (synchronous — run in executor)
# ---------------------------------------------------------------------------


def _detect_key(
    y: "np.ndarray", sr: int
) -> tuple[str, str]:
    """Return (key_name, camelot_code) using Krumhansl-Schmuckler profiles."""
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    mean_chroma = chroma.mean(axis=1)  # shape (12,)

    # Correlate against all 24 rotations (12 major + 12 minor)
    best_r = -np.inf
    best_pc = 0
    best_mode = "major"

    for pc in range(12):
        rotated = np.roll(mean_chroma, -pc)
        r_major = np.corrcoef(rotated, _KS_MAJOR)[0, 1]
        r_minor = np.corrcoef(rotated, _KS_MINOR)[0, 1]
        if r_major > best_r:
            best_r = r_major
            best_pc = pc
            best_mode = "major"
        if r_minor > best_r:
            best_r = r_minor
            best_pc = pc
            best_mode = "minor"

    key_name = f"{_PITCH_CLASSES[best_pc]} {best_mode}"
    camelot_code = _CAMELOT[(best_pc, best_mode)]
    return key_name, camelot_code


def _db_to_scale(rms_db: float) -> int:
    """Map an RMS dB value to the 1–10 energy scale."""
    if rms_db <= _ENERGY_THRESHOLDS_DB[0]:
        return 1
    if rms_db >= _ENERGY_THRESHOLDS_DB[-1]:
        return 10
    idx = int(np.searchsorted(_ENERGY_THRESHOLDS_DB, rms_db))
    return max(1, min(10, idx + 1))


def _centroid_to_scale(centroid_hz: float) -> int:
    """Map a mean spectral centroid (Hz) to the 1–10 brightness scale."""
    if centroid_hz <= _BRIGHTNESS_THRESHOLDS_HZ[0]:
        return 1
    if centroid_hz >= _BRIGHTNESS_THRESHOLDS_HZ[-1]:
        return 10
    idx = int(np.searchsorted(_BRIGHTNESS_THRESHOLDS_HZ, centroid_hz))
    return max(1, min(10, idx + 1))


def _compute_danceability(y: "np.ndarray", sr: int, tempo: float) -> int:
    """Estimate danceability from onset strength variance + tempo stability.

    High onset-strength variance combined with a stable tempo suggests a
    well-defined rhythmic pulse — the hallmark of danceable music.

    Returns an integer 1–10.
    """
    import librosa

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)

    # Variance of onset strength — captures rhythmic contrast
    onset_var = float(np.var(onset_env))

    # Tempo stability: measure variation in inter-beat intervals
    _, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    if len(beats) >= 2:
        ibi = np.diff(beats.astype(float))
        ibi_cv = float(np.std(ibi) / (np.mean(ibi) + 1e-9))  # coeff. of variation
    else:
        ibi_cv = 1.0  # no stable beat detected → low danceability

    # Combine: high onset variance + low IBI CV → high danceability
    # onset_var is unbounded; we clamp after empirical calibration.
    onset_score = min(onset_var / 5.0, 1.0)       # normalise to [0, 1]
    stability_score = max(0.0, 1.0 - ibi_cv)       # 1 = perfectly stable

    raw = (onset_score * 0.6 + stability_score * 0.4)
    score = int(round(raw * 9)) + 1                 # map [0, 1] → [1, 10]
    return max(1, min(10, score))


def _analyze_sync(file_path: str) -> AudioFeatures:
    """Synchronous librosa analysis — intended to run in a thread executor."""
    import librosa

    logger.info("Analyzing audio file: %s", file_path)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    # Load audio — mono, 22050 Hz by default.
    # duration=None loads the whole file; librosa handles all common formats
    # (MP3, FLAC, WAV, OGG, …) via soundfile/audioread.
    try:
        y, sr = librosa.load(file_path, mono=True)
    except Exception as exc:
        raise ValueError(f"Cannot decode audio file '{file_path}': {exc}") from exc

    duration = librosa.get_duration(y=y, sr=sr)
    if duration < 1.0:
        logger.warning(
            "Very short audio file (%.2fs): %s — results may be unreliable",
            duration,
            file_path,
        )
        if duration == 0.0:
            raise ValueError(
                f"Audio file appears to be silent or zero-length: {file_path}"
            )

    # --- BPM ---
    tempo_arr, _ = librosa.beat.beat_track(y=y, sr=sr)
    # librosa >= 0.10 returns a scalar ndarray; earlier versions may return a
    # 1-element array.  Flatten defensively.
    bpm = float(np.atleast_1d(tempo_arr)[0])
    logger.debug("BPM: %.1f", bpm)

    # --- Key + Camelot ---
    key_name, camelot_code = _detect_key(y, sr)
    logger.debug("Key: %s  Camelot: %s", key_name, camelot_code)

    # --- Energy (RMS → dBFS → 1-10) ---
    rms = librosa.feature.rms(y=y)[0]  # shape (frames,)
    # Guard against silence to avoid log(0)
    mean_rms = float(np.mean(rms))
    if mean_rms < 1e-9:
        energy = 1
    else:
        rms_db = float(librosa.amplitude_to_db(np.array([mean_rms]))[0])
        energy = _db_to_scale(rms_db)
    logger.debug("Energy: %d", energy)

    # --- Brightness (spectral centroid → 1-10) ---
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]  # (frames,)
    mean_centroid = float(np.mean(centroid))
    brightness = _centroid_to_scale(mean_centroid)
    logger.debug("Brightness: %d  (centroid %.1f Hz)", brightness, mean_centroid)

    # --- Danceability ---
    danceability = _compute_danceability(y, sr, bpm)
    logger.debug("Danceability: %d", danceability)

    features = AudioFeatures(
        bpm=round(bpm, 2),
        key=key_name,
        camelot_code=camelot_code,
        energy=energy,
        brightness=brightness,
        danceability=danceability,
    )
    logger.info("Analysis complete for %s: %s", os.path.basename(file_path), features)
    return features


# ---------------------------------------------------------------------------
# Public async interface
# ---------------------------------------------------------------------------


async def analyze_audio(file_path: str) -> AudioFeatures:
    """Analyze an audio file and return its perceptual features.

    Runs the CPU-bound librosa work in a thread-pool executor so the FastAPI
    event loop remains responsive.

    Args:
        file_path: Absolute or relative path to the audio file.

    Returns:
        AudioFeatures dataclass with bpm, key, camelot_code, energy,
        brightness, and danceability.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file cannot be decoded or is silent/zero-length.
    """
    loop = asyncio.get_event_loop()
    features: AudioFeatures = await loop.run_in_executor(
        None, _analyze_sync, file_path
    )
    return features
