"""Tests for beat_analyzer — beat detection and crossfade point selection.

Two test suites:
- TestBeatInfo / TestCrossfadePoint: dataclass construction and field types.
- TestFindCrossfadePoints: pure-function unit tests for find_crossfade_points.
- TestDetectBeatsAsync: async tests with a synthetic audio signal (no real file I/O).
- TestSerializationHelpers: round-trip through beat_info_to_dict / beat_info_from_dict.
- TestEdgeCases: empty BeatInfo, very short audio, no beats, missing beat_strength.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from beat_analyzer import (
    BeatInfo,
    CrossfadePoint,
    beat_info_from_dict,
    beat_info_to_dict,
    detect_beats,
    find_crossfade_points,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_beat_info(n_beats: int = 8, tempo: float = 120.0, beats_per_bar: int = 4) -> BeatInfo:
    """Construct a synthetic BeatInfo for testing.

    Beats are evenly spaced at the given tempo starting from beat 0.
    Downbeats every beats_per_bar beats.
    """
    beat_interval = 60.0 / tempo
    beat_times = [round(i * beat_interval, 4) for i in range(n_beats)]
    downbeat_times = [beat_times[i] for i in range(0, n_beats, beats_per_bar)]
    beat_strength = [0.8 if i % beats_per_bar == 0 else 0.5 for i in range(n_beats)]
    return BeatInfo(
        beat_times=beat_times,
        downbeat_times=downbeat_times,
        tempo=tempo,
        beat_strength=beat_strength,
    )


# ---------------------------------------------------------------------------
# TestBeatInfo — dataclass basics
# ---------------------------------------------------------------------------

class TestBeatInfo:
    def test_fields_accessible(self):
        info = _make_beat_info()
        assert isinstance(info.beat_times, list)
        assert isinstance(info.downbeat_times, list)
        assert isinstance(info.tempo, float)
        assert isinstance(info.beat_strength, list)

    def test_beat_times_are_floats(self):
        info = _make_beat_info(n_beats=4)
        assert all(isinstance(t, float) for t in info.beat_times)

    def test_tempo_positive(self):
        info = _make_beat_info(tempo=90.0)
        assert info.tempo > 0

    def test_downbeats_subset_of_beats(self):
        info = _make_beat_info(n_beats=8, beats_per_bar=4)
        beat_set = set(info.beat_times)
        for dt in info.downbeat_times:
            assert dt in beat_set

    def test_beat_strength_length_matches_beats(self):
        info = _make_beat_info(n_beats=6)
        assert len(info.beat_strength) == len(info.beat_times)

    def test_beat_strength_values_in_range(self):
        info = _make_beat_info(n_beats=8)
        assert all(0.0 <= s <= 1.0 for s in info.beat_strength)


# ---------------------------------------------------------------------------
# TestCrossfadePoint — dataclass basics
# ---------------------------------------------------------------------------

class TestCrossfadePoint:
    def test_fields_accessible(self):
        cp = CrossfadePoint(time=1.5, beat_index=3, is_downbeat=True, confidence=0.9)
        assert cp.time == 1.5
        assert cp.beat_index == 3
        assert cp.is_downbeat is True
        assert cp.confidence == 0.9


# ---------------------------------------------------------------------------
# TestFindCrossfadePoints — pure-function unit tests
# ---------------------------------------------------------------------------

class TestFindCrossfadePoints:
    """find_crossfade_points returns correctly filtered and sorted candidates."""

    def test_returns_list(self):
        info = _make_beat_info()
        result = find_crossfade_points(info, position_sec=1.0)
        assert isinstance(result, list)

    def test_empty_beat_info_returns_empty(self):
        empty = BeatInfo(beat_times=[], downbeat_times=[], tempo=0.0, beat_strength=[])
        result = find_crossfade_points(empty, position_sec=2.0)
        assert result == []

    def test_candidates_are_crossfade_points(self):
        info = _make_beat_info(n_beats=8, tempo=120.0)
        result = find_crossfade_points(info, position_sec=1.0, window_sec=4.0)
        assert all(isinstance(cp, CrossfadePoint) for cp in result)

    def test_candidates_within_window(self):
        info = _make_beat_info(n_beats=16, tempo=120.0)
        position = 2.0
        window = 1.0
        result = find_crossfade_points(info, position_sec=position, window_sec=window)
        for cp in result:
            assert abs(cp.time - position) <= window + 1e-9, (
                f"Candidate at {cp.time:.4f}s is outside window [{position - window}, {position + window}]"
            )

    def test_sorted_by_proximity(self):
        info = _make_beat_info(n_beats=16, tempo=120.0)
        position = 3.0
        result = find_crossfade_points(info, position_sec=position, window_sec=4.0)
        distances = [abs(cp.time - position) for cp in result]
        assert distances == sorted(distances), "Results should be sorted nearest-first"

    def test_nearest_candidate_closest_to_position(self):
        info = _make_beat_info(n_beats=8, tempo=120.0)
        # With 120 BPM, beats are at 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5
        position = 1.0
        result = find_crossfade_points(info, position_sec=position, window_sec=4.0)
        assert len(result) > 0
        nearest = result[0]
        # The nearest beat to 1.0 s should be at 1.0 s itself
        assert abs(nearest.time - position) < 0.01

    def test_downbeats_are_flagged(self):
        info = _make_beat_info(n_beats=8, tempo=120.0, beats_per_bar=4)
        result = find_crossfade_points(info, position_sec=0.0, window_sec=4.0)
        downbeat_candidates = [cp for cp in result if cp.is_downbeat]
        # There should be at least one downbeat (position 0.0 is a downbeat)
        assert len(downbeat_candidates) > 0

    def test_downbeat_candidates_have_higher_confidence(self):
        info = _make_beat_info(n_beats=8, tempo=120.0, beats_per_bar=4)
        result = find_crossfade_points(info, position_sec=2.0, window_sec=4.0)
        downbeats = [cp for cp in result if cp.is_downbeat]
        non_downbeats = [cp for cp in result if not cp.is_downbeat]
        if downbeats and non_downbeats:
            avg_db_conf = sum(cp.confidence for cp in downbeats) / len(downbeats)
            avg_ndb_conf = sum(cp.confidence for cp in non_downbeats) / len(non_downbeats)
            assert avg_db_conf >= avg_ndb_conf, (
                "Downbeat candidates should have >= confidence vs non-downbeats"
            )

    def test_confidence_in_range(self):
        info = _make_beat_info(n_beats=8)
        result = find_crossfade_points(info, position_sec=2.0, window_sec=4.0)
        for cp in result:
            assert 0.0 <= cp.confidence <= 1.0, (
                f"Confidence {cp.confidence} out of [0, 1]"
            )

    def test_beat_index_matches_beat_times(self):
        info = _make_beat_info(n_beats=8, tempo=120.0)
        result = find_crossfade_points(info, position_sec=2.0, window_sec=4.0)
        for cp in result:
            assert info.beat_times[cp.beat_index] == cp.time

    def test_no_candidates_outside_window(self):
        info = _make_beat_info(n_beats=4, tempo=120.0)
        # Beats at 0.0, 0.5, 1.0, 1.5; position far away with tiny window
        result = find_crossfade_points(info, position_sec=100.0, window_sec=0.1)
        assert result == []

    def test_missing_beat_strength_uses_fallback(self):
        """If beat_strength is shorter than beat_times, fallback confidence is used."""
        info = BeatInfo(
            beat_times=[0.0, 0.5, 1.0, 1.5],
            downbeat_times=[0.0],
            tempo=120.0,
            beat_strength=[],   # intentionally empty
        )
        result = find_crossfade_points(info, position_sec=0.5, window_sec=2.0)
        assert len(result) > 0
        for cp in result:
            assert 0.0 <= cp.confidence <= 1.0

    def test_window_zero_returns_at_most_one_exact_beat(self):
        info = _make_beat_info(n_beats=8, tempo=120.0)
        # With a 0-width window only beats exactly at position pass
        result = find_crossfade_points(info, position_sec=0.5, window_sec=0.0)
        for cp in result:
            assert cp.time == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# TestDetectBeatsAsync — async beat detection with synthetic audio
# ---------------------------------------------------------------------------

# Skip these tests when numpy/librosa/soundfile are not installed so the
# test suite remains runnable in minimal environments.
try:
    import numpy as np
    import soundfile as sf
    HAS_AUDIO_LIBS = True
except ImportError:
    HAS_AUDIO_LIBS = False


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_AUDIO_LIBS, reason="numpy/soundfile not installed")
class TestDetectBeatsAsync:
    """Async detect_beats tests using a synthetic click-track WAV written to a
    temporary file, so no real music files are required."""

    @staticmethod
    def _write_click_track(path: str, bpm: float = 120.0, duration_sec: float = 4.0) -> None:
        """Write a synthetic click track: short impulses at each beat."""
        sr = 22050
        n_samples = int(duration_sec * sr)
        audio = np.zeros(n_samples, dtype=np.float32)

        beat_interval = sr * 60.0 / bpm
        t = 0.0
        while t < n_samples:
            idx = int(t)
            # 10 ms impulse
            end = min(idx + int(0.01 * sr), n_samples)
            audio[idx:end] = 0.9
            t += beat_interval

        sf.write(path, audio, sr)

    async def test_detect_beats_returns_beat_info(self, tmp_path):
        wav = str(tmp_path / "click.wav")
        self._write_click_track(wav, bpm=120.0, duration_sec=4.0)
        result = await detect_beats(wav)
        assert isinstance(result, BeatInfo)

    async def test_detect_beats_tempo_positive(self, tmp_path):
        wav = str(tmp_path / "click.wav")
        self._write_click_track(wav, bpm=120.0, duration_sec=4.0)
        result = await detect_beats(wav)
        assert result.tempo > 0.0

    async def test_detect_beats_returns_some_beats(self, tmp_path):
        wav = str(tmp_path / "click.wav")
        self._write_click_track(wav, bpm=120.0, duration_sec=4.0)
        result = await detect_beats(wav)
        assert len(result.beat_times) > 0, "Expected at least one beat in a 4-second click track"

    async def test_detect_beats_beat_strength_matches_beats(self, tmp_path):
        wav = str(tmp_path / "click.wav")
        self._write_click_track(wav, bpm=120.0, duration_sec=4.0)
        result = await detect_beats(wav)
        assert len(result.beat_strength) == len(result.beat_times)

    async def test_detect_beats_downbeats_subset_of_beats(self, tmp_path):
        wav = str(tmp_path / "click.wav")
        self._write_click_track(wav, bpm=120.0, duration_sec=8.0)
        result = await detect_beats(wav)
        beat_set = set(result.beat_times)
        for dt in result.downbeat_times:
            assert dt in beat_set, f"Downbeat {dt} not in beat_times"

    async def test_detect_beats_missing_file_returns_empty(self):
        result = await detect_beats("/tmp/does_not_exist_xyz_12345.wav")
        assert result.beat_times == []
        assert result.tempo == 0.0

    async def test_crossfade_points_from_detected_beats(self, tmp_path):
        wav = str(tmp_path / "click.wav")
        self._write_click_track(wav, bpm=120.0, duration_sec=6.0)
        beat_info = await detect_beats(wav)
        if len(beat_info.beat_times) == 0:
            pytest.skip("Beat detection found no beats in synthetic audio")
        mid = beat_info.beat_times[len(beat_info.beat_times) // 2]
        points = find_crossfade_points(beat_info, position_sec=mid, window_sec=2.0)
        assert len(points) > 0
        # All returned points must be within window
        for cp in points:
            assert abs(cp.time - mid) <= 2.0 + 1e-9


# ---------------------------------------------------------------------------
# TestSerializationHelpers — round-trip dict conversion
# ---------------------------------------------------------------------------

class TestSerializationHelpers:
    def test_round_trip_preserves_beat_times(self):
        original = _make_beat_info(n_beats=8)
        d = beat_info_to_dict(original)
        restored = beat_info_from_dict(d)
        assert restored.beat_times == original.beat_times

    def test_round_trip_preserves_downbeat_times(self):
        original = _make_beat_info(n_beats=8)
        d = beat_info_to_dict(original)
        restored = beat_info_from_dict(d)
        assert restored.downbeat_times == original.downbeat_times

    def test_round_trip_preserves_tempo(self):
        original = _make_beat_info(tempo=98.5)
        d = beat_info_to_dict(original)
        restored = beat_info_from_dict(d)
        assert restored.tempo == pytest.approx(98.5)

    def test_round_trip_preserves_beat_strength(self):
        original = _make_beat_info(n_beats=8)
        d = beat_info_to_dict(original)
        restored = beat_info_from_dict(d)
        assert restored.beat_strength == original.beat_strength

    def test_to_dict_keys(self):
        d = beat_info_to_dict(_make_beat_info())
        assert set(d.keys()) == {"beat_times", "downbeat_times", "tempo", "beat_strength"}

    def test_from_dict_empty(self):
        restored = beat_info_from_dict({})
        assert restored.beat_times == []
        assert restored.downbeat_times == []
        assert restored.tempo == 0.0
        assert restored.beat_strength == []

    def test_to_dict_values_are_python_types(self):
        """Ensure no numpy scalars slip through into the dict (would fail JSON)."""
        d = beat_info_to_dict(_make_beat_info())
        for t in d["beat_times"]:
            assert isinstance(t, float), f"Expected float, got {type(t)}"
        assert isinstance(d["tempo"], float)


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_beat_info_to_crossfade_returns_empty(self):
        empty = BeatInfo(beat_times=[], downbeat_times=[], tempo=0.0, beat_strength=[])
        result = find_crossfade_points(empty, position_sec=5.0, window_sec=10.0)
        assert result == []

    def test_single_beat_within_window(self):
        info = BeatInfo(
            beat_times=[2.0],
            downbeat_times=[2.0],
            tempo=30.0,
            beat_strength=[0.9],
        )
        result = find_crossfade_points(info, position_sec=2.0, window_sec=1.0)
        assert len(result) == 1
        assert result[0].time == pytest.approx(2.0)
        assert result[0].is_downbeat is True

    def test_single_beat_outside_window_returns_empty(self):
        info = BeatInfo(
            beat_times=[10.0],
            downbeat_times=[10.0],
            tempo=30.0,
            beat_strength=[0.9],
        )
        result = find_crossfade_points(info, position_sec=2.0, window_sec=1.0)
        assert result == []

    def test_beat_exactly_at_window_boundary_included(self):
        """Beat at position ± window should be included (boundary is inclusive)."""
        info = BeatInfo(
            beat_times=[0.0, 4.0],
            downbeat_times=[0.0],
            tempo=15.0,
            beat_strength=[0.8, 0.8],
        )
        result = find_crossfade_points(info, position_sec=0.0, window_sec=4.0)
        times = [cp.time for cp in result]
        assert 4.0 in times

    def test_large_beat_count_performance(self):
        """find_crossfade_points should handle a large beat list without error."""
        n = 10000
        beat_times = [round(i * 0.5, 6) for i in range(n)]
        info = BeatInfo(
            beat_times=beat_times,
            downbeat_times=beat_times[::4],
            tempo=120.0,
            beat_strength=[0.7] * n,
        )
        result = find_crossfade_points(info, position_sec=100.0, window_sec=2.0)
        assert isinstance(result, list)
        for cp in result:
            assert abs(cp.time - 100.0) <= 2.0 + 1e-9
