"""Unit tests for backend/audio_analyzer.py — Sprint 11 Audio Intelligence.

Test suites
-----------
TestCamelotWheel
    Validates every key → Camelot code mapping without loading any audio.

TestKeyDetectionSynth
    Uses a synthetically generated pure-tone signal to verify that the
    Krumhansl-Schmuckler detector can identify a known key.

TestEnergyNormalization
    Verifies the RMS-dBFS → 1-10 energy scale logic at the boundaries.

TestBrightnessNormalization
    Verifies the spectral-centroid → 1-10 brightness scale logic.

TestAudioFeatures
    Confirms that analyze_audio returns a valid AudioFeatures instance with
    all fields in their expected ranges using a short synthetic signal.
"""

from __future__ import annotations

import sys
import asyncio
import math
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Make backend root importable without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from audio_analyzer import (
    AudioFeatures,
    _CAMELOT,
    _PITCH_CLASSES,
    _analyze_sync,
    _centroid_to_scale,
    _db_to_scale,
    analyze_audio,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wav(path: str, y: np.ndarray, sr: int = 22050) -> None:
    """Write a mono PCM WAV file using soundfile (no extra dependencies)."""
    import soundfile as sf
    sf.write(path, y.astype(np.float32), sr, subtype="PCM_16")


def _sine(freq_hz: float, duration_s: float = 3.0, sr: int = 22050) -> np.ndarray:
    """Return a pure sine wave at *freq_hz*."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return np.sin(2.0 * math.pi * freq_hz * t).astype(np.float32)


def _c_major_chord(duration_s: float = 3.0, sr: int = 22050) -> np.ndarray:
    """C major triad: C4 + E4 + G4 (261.63, 329.63, 392.00 Hz)."""
    freqs = [261.63, 329.63, 392.00]
    signal = sum(_sine(f, duration_s, sr) for f in freqs)
    # Normalise to [-1, 1]
    peak = np.max(np.abs(signal))
    if peak > 0:
        signal = signal / peak * 0.9
    return signal.astype(np.float32)


def _a_minor_chord(duration_s: float = 3.0, sr: int = 22050) -> np.ndarray:
    """A minor triad: A4 + C5 + E5 (440, 523.25, 659.25 Hz)."""
    freqs = [440.00, 523.25, 659.25]
    signal = sum(_sine(f, duration_s, sr) for f in freqs)
    peak = np.max(np.abs(signal))
    if peak > 0:
        signal = signal / peak * 0.9
    return signal.astype(np.float32)


def _click_track(bpm: float = 120.0, duration_s: float = 4.0, sr: int = 22050) -> np.ndarray:
    """A periodic click train at *bpm* — unlike a continuous sine tone, this
    has real percussive onsets for librosa's beat tracker to lock onto."""
    n_samples = int(sr * duration_s)
    signal = np.zeros(n_samples, dtype=np.float32)
    interval = sr * 60.0 / bpm
    click_len = int(sr * 0.02)  # 20ms click
    t_click = np.linspace(0, 0.02, click_len, endpoint=False)
    click = (np.sin(2.0 * math.pi * 1000.0 * t_click) * np.exp(-t_click * 80)).astype(np.float32)
    beat = 0
    while (start := int(beat * interval)) < n_samples:
        end = min(start + click_len, n_samples)
        signal[start:end] += click[: end - start]
        beat += 1
    return signal


# ---------------------------------------------------------------------------
# TestCamelotWheel — mapping completeness + spot-checks
# ---------------------------------------------------------------------------

class TestCamelotWheel:
    """Validate that the Camelot lookup table is complete and correct."""

    def test_all_24_keys_present(self):
        """Every pitch class in both modes must have a Camelot code."""
        for pc in range(12):
            assert (pc, "major") in _CAMELOT, f"Missing major key for pc={pc}"
            assert (pc, "minor") in _CAMELOT, f"Missing minor key for pc={pc}"

    def test_camelot_codes_format(self):
        """All codes must match pattern: digits followed by 'A' or 'B'."""
        import re
        pattern = re.compile(r"^(1[0-2]|[1-9])[AB]$")
        for (pc, mode), code in _CAMELOT.items():
            assert pattern.match(code), (
                f"Invalid Camelot code '{code}' for pc={pc} mode={mode}"
            )

    def test_c_major_is_8B(self):
        assert _CAMELOT[(0, "major")] == "8B"

    def test_a_minor_is_8A(self):
        assert _CAMELOT[(9, "minor")] == "8A"

    def test_g_major_is_9B(self):
        assert _CAMELOT[(7, "major")] == "9B"

    def test_e_minor_is_9A(self):
        assert _CAMELOT[(4, "minor")] == "9A"

    def test_f_major_is_7B(self):
        assert _CAMELOT[(5, "major")] == "7B"

    def test_d_minor_is_7A(self):
        assert _CAMELOT[(2, "minor")] == "7A"

    def test_b_major_is_1B(self):
        assert _CAMELOT[(11, "major")] == "1B"

    def test_ab_minor_is_1A(self):
        # G# == A♭ (enharmonic), pc index 8
        assert _CAMELOT[(8, "minor")] == "1A"

    def test_unique_codes(self):
        """No two different keys should share the same Camelot code."""
        codes = list(_CAMELOT.values())
        assert len(codes) == len(set(codes)), "Duplicate Camelot codes detected"

    def test_major_codes_end_with_B(self):
        for (_, mode), code in _CAMELOT.items():
            if mode == "major":
                assert code.endswith("B"), f"Major key has non-B code: {code}"

    def test_minor_codes_end_with_A(self):
        for (_, mode), code in _CAMELOT.items():
            if mode == "minor":
                assert code.endswith("A"), f"Minor key has non-A code: {code}"


# ---------------------------------------------------------------------------
# TestKeyDetectionSynth — synthetic signal → expected key
# ---------------------------------------------------------------------------

class TestKeyDetectionSynth:
    """Key detection with synthetic chords verifies the K-S algorithm."""

    def _features_from_signal(self, signal: np.ndarray, sr: int = 22050) -> AudioFeatures:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, signal, sr)
            return _analyze_sync(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_c_major_chord_detected_as_major(self):
        """A C major triad should be detected as some major key."""
        feats = self._features_from_signal(_c_major_chord())
        assert "major" in feats.key, f"Expected major key, got: {feats.key}"

    def test_c_major_chord_camelot_ends_B(self):
        """Major key results must have Camelot codes ending in 'B'."""
        feats = self._features_from_signal(_c_major_chord())
        assert feats.camelot_code.endswith("B"), (
            f"Expected B-suffix Camelot code for major key, got: {feats.camelot_code}"
        )

    def test_a_minor_chord_detected_as_minor(self):
        """An A minor triad should be detected as some minor key."""
        feats = self._features_from_signal(_a_minor_chord())
        assert "minor" in feats.key, f"Expected minor key, got: {feats.key}"

    def test_a_minor_chord_camelot_ends_A(self):
        """Minor key results must have Camelot codes ending in 'A'."""
        feats = self._features_from_signal(_a_minor_chord())
        assert feats.camelot_code.endswith("A"), (
            f"Expected A-suffix Camelot code for minor key, got: {feats.camelot_code}"
        )

    def test_key_name_contains_pitch_class(self):
        """Key name must start with a recognised pitch-class symbol."""
        feats = self._features_from_signal(_c_major_chord())
        root = feats.key.split()[0]
        assert root in _PITCH_CLASSES, f"Unrecognised pitch class in key name: {feats.key}"


# ---------------------------------------------------------------------------
# TestEnergyNormalization — RMS dBFS → 1-10 scale
# ---------------------------------------------------------------------------

class TestEnergyNormalization:
    """Verify _db_to_scale boundary behaviour without real audio files."""

    def test_silence_maps_to_1(self):
        """Values well below the lower threshold must return 1."""
        assert _db_to_scale(-60.0) == 1

    def test_very_loud_maps_to_10(self):
        """Values well above the upper threshold must return 10."""
        assert _db_to_scale(0.0) == 10

    def test_lower_boundary_is_1(self):
        assert _db_to_scale(-35.0) == 1

    def test_upper_boundary_is_10(self):
        assert _db_to_scale(-5.0) == 10

    def test_mid_value_in_range(self):
        """A mid-range dB value should map to a mid-range energy score."""
        score = _db_to_scale(-20.0)
        assert 3 <= score <= 8, f"Mid dB value gave unexpected energy {score}"

    def test_monotone_increasing(self):
        """Higher dB values must yield equal or higher energy scores."""
        values = np.linspace(-40.0, 0.0, 20)
        scores = [_db_to_scale(v) for v in values]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], (
                f"Non-monotone at index {i}: {scores[i]} > {scores[i+1]}"
            )

    def test_always_in_1_to_10(self):
        """Output must always be clamped to [1, 10]."""
        for db in np.linspace(-80.0, 10.0, 50):
            score = _db_to_scale(float(db))
            assert 1 <= score <= 10, f"Out-of-range energy {score} for db={db}"

    def test_very_quiet_signal_energy_is_low(self):
        """A near-silent audio file should have energy 1 or 2."""
        sr = 22050
        duration_s = 2.0
        silence = np.zeros(int(sr * duration_s), dtype=np.float32) + 1e-7
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, silence, sr)
            feats = _analyze_sync(tmp_path)
            assert feats.energy <= 2, f"Silent signal should have energy ≤ 2, got {feats.energy}"
        finally:
            os.unlink(tmp_path)

    def test_loud_signal_energy_is_high(self):
        """A full-amplitude sine wave should have high energy."""
        sr = 22050
        signal = _sine(440.0, duration_s=2.0, sr=sr)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, signal, sr)
            feats = _analyze_sync(tmp_path)
            assert feats.energy >= 7, f"Loud signal should have energy ≥ 7, got {feats.energy}"
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# TestBrightnessNormalization — spectral centroid → 1-10 scale
# ---------------------------------------------------------------------------

class TestBrightnessNormalization:
    """Verify _centroid_to_scale boundary behaviour."""

    def test_low_centroid_maps_to_1(self):
        assert _centroid_to_scale(100.0) == 1

    def test_high_centroid_maps_to_10(self):
        assert _centroid_to_scale(12000.0) == 10

    def test_lower_boundary_is_1(self):
        assert _centroid_to_scale(800.0) == 1

    def test_upper_boundary_is_10(self):
        assert _centroid_to_scale(8000.0) == 10

    def test_mid_range_value(self):
        score = _centroid_to_scale(4000.0)
        assert 3 <= score <= 8, f"Mid-range centroid gave unexpected brightness {score}"

    def test_always_in_1_to_10(self):
        for hz in np.linspace(0.0, 20000.0, 50):
            score = _centroid_to_scale(float(hz))
            assert 1 <= score <= 10, f"Out-of-range brightness {score} for hz={hz}"

    def test_bass_heavy_signal_low_brightness(self):
        """A low-frequency sine (bass) should have low brightness."""
        sr = 22050
        signal = _sine(80.0, duration_s=2.0, sr=sr)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, signal, sr)
            feats = _analyze_sync(tmp_path)
            assert feats.brightness <= 4, (
                f"Bass signal should have low brightness, got {feats.brightness}"
            )
        finally:
            os.unlink(tmp_path)

    def test_high_freq_signal_high_brightness(self):
        """A high-frequency sine should have high brightness."""
        sr = 22050
        signal = _sine(6000.0, duration_s=2.0, sr=sr)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, signal, sr)
            feats = _analyze_sync(tmp_path)
            assert feats.brightness >= 7, (
                f"High-freq signal should have high brightness, got {feats.brightness}"
            )
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# TestAnalyzeAudio — valid AudioFeatures returned by analyze_audio
# ---------------------------------------------------------------------------

class TestAnalyzeAudio:
    """Integration-level: analyze_audio must return valid AudioFeatures."""

    def _tmp_wav(self, signal: np.ndarray, sr: int = 22050) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        _write_wav(path, signal, sr)
        return path

    def test_returns_audio_features_instance(self):
        sr = 22050
        signal = _c_major_chord(duration_s=2.0, sr=sr)
        path = self._tmp_wav(signal, sr)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            assert isinstance(feats, AudioFeatures)
        finally:
            os.unlink(path)

    def test_bpm_is_positive_float(self):
        # A continuous, unmodulated sine tone has no rhythmic onsets, so
        # librosa's beat tracker has nothing to lock onto and 0.0 is the
        # honest answer for that input — use a click track with real
        # periodic onsets to exercise actual beat detection instead.
        signal = _click_track(bpm=120.0, duration_s=4.0)
        path = self._tmp_wav(signal)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            assert isinstance(feats.bpm, float)
            assert feats.bpm > 0.0, f"BPM must be positive, got {feats.bpm}"
        finally:
            os.unlink(path)

    def test_key_is_string(self):
        signal = _c_major_chord()
        path = self._tmp_wav(signal)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            assert isinstance(feats.key, str)
            assert len(feats.key) > 0
        finally:
            os.unlink(path)

    def test_camelot_code_is_string(self):
        signal = _c_major_chord()
        path = self._tmp_wav(signal)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            assert isinstance(feats.camelot_code, str)
            assert len(feats.camelot_code) > 0
        finally:
            os.unlink(path)

    def test_energy_in_range(self):
        signal = _c_major_chord()
        path = self._tmp_wav(signal)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            assert 1 <= feats.energy <= 10, f"Energy out of range: {feats.energy}"
        finally:
            os.unlink(path)

    def test_brightness_in_range(self):
        signal = _c_major_chord()
        path = self._tmp_wav(signal)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            assert 1 <= feats.brightness <= 10, f"Brightness out of range: {feats.brightness}"
        finally:
            os.unlink(path)

    def test_danceability_in_range(self):
        signal = _c_major_chord()
        path = self._tmp_wav(signal)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            assert 1 <= feats.danceability <= 10, (
                f"Danceability out of range: {feats.danceability}"
            )
        finally:
            os.unlink(path)

    def test_key_format_has_two_parts(self):
        """Key string must be '<PitchClass> <mode>'."""
        signal = _c_major_chord()
        path = self._tmp_wav(signal)
        try:
            feats = asyncio.get_event_loop().run_until_complete(analyze_audio(path))
            parts = feats.key.split()
            assert len(parts) == 2, f"Unexpected key format: '{feats.key}'"
            assert parts[1] in ("major", "minor"), (
                f"Key mode must be 'major' or 'minor', got '{parts[1]}'"
            )
        finally:
            os.unlink(path)

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                analyze_audio("/nonexistent/path/file.wav")
            )

    def test_very_short_file_raises_or_warns(self):
        """A sub-second file should either succeed or raise ValueError, not crash."""
        sr = 22050
        tiny = _sine(440.0, duration_s=0.3, sr=sr)
        path = self._tmp_wav(tiny, sr)
        try:
            try:
                asyncio.get_event_loop().run_until_complete(analyze_audio(path))
                # If it succeeds, that's acceptable — just verify the return type
            except ValueError:
                pass  # Expected for problematic short files
        finally:
            os.unlink(path)
