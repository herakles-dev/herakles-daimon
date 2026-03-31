"""
Waveform Generator — downsample audio to amplitude envelope.

Uses librosa to load audio and compute a normalized peak-amplitude
envelope suitable for canvas-based waveform visualization.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import List

logger = logging.getLogger("play-backend.waveform")

# Target sample rate — low enough to be fast, high enough for accuracy.
_SAMPLE_RATE = 22050


async def generate_waveform(file_path: str, num_points: int = 800) -> List[float]:
    """Downsample audio file to an amplitude envelope.

    Loads the audio file with librosa (mono, sr=22050), splits it into
    *num_points* equal-length windows, takes the peak absolute amplitude
    per window, then normalises the whole array to the range [0.0, 1.0].

    Args:
        file_path: Absolute or relative path to the audio file.  Any
                   format supported by librosa / soundfile / audioread
                   is accepted (mp3, flac, wav, ogg, …).
        num_points: Number of data-points in the returned envelope.
                    Defaults to 800 (one point per display pixel at
                    typical 1× scaling).

    Returns:
        A list of *num_points* floats in [0.0, 1.0].  The list is
        guaranteed to have exactly *num_points* entries — trailing
        windows that fall outside the audio are filled with 0.0.

    Raises:
        FileNotFoundError: If *file_path* does not exist.
        RuntimeError: If librosa is not installed or audio decoding fails.
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "librosa and numpy are required for waveform generation. "
            "Install them via: pip install librosa numpy"
        ) from exc

    try:
        # Load mono at 22 050 Hz — res_type='kaiser_fast' trades a little
        # quality for significantly faster resampling.
        y, _ = librosa.load(file_path, sr=_SAMPLE_RATE, mono=True, res_type="kaiser_fast")
    except Exception as exc:
        raise RuntimeError(f"Failed to decode audio '{file_path}': {exc}") from exc

    total_samples = len(y)
    if total_samples == 0:
        logger.warning("Audio file '%s' contains no samples; returning zeros", file_path)
        return [0.0] * num_points

    # Window size — may be fractional, so we track accumulation with floats.
    window_size = total_samples / num_points

    peaks: List[float] = []
    for i in range(num_points):
        start = int(math.floor(i * window_size))
        end = int(math.floor((i + 1) * window_size))
        if start >= total_samples:
            peaks.append(0.0)
        else:
            end = min(end, total_samples)
            window = y[start:end]
            peaks.append(float(np.max(np.abs(window)))) if len(window) else peaks.append(0.0)

    # Normalise to [0.0, 1.0]
    max_val = max(peaks) if peaks else 0.0
    if max_val > 0.0:
        peaks = [p / max_val for p in peaks]

    return peaks
