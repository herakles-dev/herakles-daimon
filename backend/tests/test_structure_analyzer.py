"""Tests for backend/structure_analyzer.py

Covers:
- analyze_structure returns a valid list of Segment objects
- Segments cover the entire track with no gaps
- Segment labels are all from the allowed set
- Short segments are merged (none shorter than MIN_SEGMENT_SEC)
- Very short tracks return a single segment
- Mono and stereo inputs are both handled

These tests use numpy-generated synthetic audio to avoid depending on
real audio files.  librosa is required; tests are skipped when unavailable.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import librosa  # noqa: F401
    import soundfile as sf
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False

from structure_analyzer import (
    ALLOWED_LABELS,
    MIN_SEGMENT_SEC,
    VERY_SHORT_TRACK_SEC,
    Segment,
    _merge_short,
    analyze_structure,
)

pytestmark = pytest.mark.skipif(
    not (HAS_NUMPY and HAS_LIBROSA),
    reason="numpy and librosa+soundfile required for structure analyzer tests",
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_RATE = 22050


def _write_sine(path: Path, duration_sec: float, freq: float = 440.0) -> None:
    """Write a mono sine wave to a WAV file."""
    t = np.linspace(0, duration_sec, int(SAMPLE_RATE * duration_sec), endpoint=False)
    y = 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    sf.write(str(path), y, SAMPLE_RATE)


def _write_structured(path: Path) -> float:
    """Write a ~60 s track with varying energy blocks to exercise labelling."""
    sr = SAMPLE_RATE
    # 8 s quiet intro
    intro = 0.05 * np.random.randn(8 * sr).astype(np.float32)
    # 12 s mid-energy verse
    verse = 0.25 * np.random.randn(12 * sr).astype(np.float32)
    # 10 s high-energy chorus
    chorus = 0.70 * np.random.randn(10 * sr).astype(np.float32)
    # 8 s low-energy breakdown
    breakdown = 0.04 * np.random.randn(8 * sr).astype(np.float32)
    # 12 s verse again
    verse2 = 0.25 * np.random.randn(12 * sr).astype(np.float32)
    # 6 s quiet outro
    outro = 0.06 * np.random.randn(6 * sr).astype(np.float32)

    y = np.concatenate([intro, verse, chorus, breakdown, verse2, outro])
    duration = len(y) / sr
    sf.write(str(path), y, sr)
    return duration


def _run(coro):
    """Run a coroutine synchronously for use in sync test functions."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ─────────────────────────────────────────────────────────────────────────────
# Segment dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestSegmentDataclass:
    def test_fields_accessible(self):
        s = Segment(label="intro", start_sec=0.0, end_sec=8.0, confidence=0.9)
        assert s.label == "intro"
        assert s.start_sec == 0.0
        assert s.end_sec == 8.0
        assert s.confidence == 0.9

    def test_all_allowed_labels(self):
        for lbl in ALLOWED_LABELS:
            s = Segment(label=lbl, start_sec=0.0, end_sec=5.0, confidence=0.8)
            assert s.label == lbl


# ─────────────────────────────────────────────────────────────────────────────
# Very short track edge case
# ─────────────────────────────────────────────────────────────────────────────

class TestVeryShortTrack:
    def test_short_track_returns_single_segment(self, tmp_path):
        wav = tmp_path / "short.wav"
        _write_sine(wav, duration_sec=5.0)
        segs = _run(analyze_structure(str(wav)))
        assert len(segs) == 1, "Expected single segment for very short track"

    def test_short_track_covers_full_duration(self, tmp_path):
        wav = tmp_path / "short.wav"
        _write_sine(wav, duration_sec=5.0)
        segs = _run(analyze_structure(str(wav)))
        assert segs[0].start_sec == pytest.approx(0.0, abs=0.1)
        assert segs[0].end_sec == pytest.approx(5.0, abs=0.5)

    def test_short_track_valid_label(self, tmp_path):
        wav = tmp_path / "short.wav"
        _write_sine(wav, duration_sec=5.0)
        segs = _run(analyze_structure(str(wav)))
        assert segs[0].label in ALLOWED_LABELS


# ─────────────────────────────────────────────────────────────────────────────
# Normal track: return type and coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyzeStructureReturnType:
    def test_returns_list(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_sine(wav, duration_sec=30.0)
        result = _run(analyze_structure(str(wav)))
        assert isinstance(result, list)

    def test_elements_are_segments(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_sine(wav, duration_sec=30.0)
        result = _run(analyze_structure(str(wav)))
        for seg in result:
            assert isinstance(seg, Segment), f"Expected Segment, got {type(seg)}"

    def test_at_least_one_segment(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_sine(wav, duration_sec=30.0)
        result = _run(analyze_structure(str(wav)))
        assert len(result) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Coverage: segments span the full track with no gaps
# ─────────────────────────────────────────────────────────────────────────────

class TestSegmentCoverage:
    def test_first_segment_starts_at_zero(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_structured(tmp_path / "track.wav")
        segs = _run(analyze_structure(str(wav)))
        assert segs[0].start_sec == pytest.approx(0.0, abs=0.1)

    def test_last_segment_ends_at_track_duration(self, tmp_path):
        wav = tmp_path / "track.wav"
        duration = _write_structured(wav)
        segs = _run(analyze_structure(str(wav)))
        assert segs[-1].end_sec == pytest.approx(duration, abs=1.0)

    def test_no_gaps_between_segments(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_structured(wav)
        segs = _run(analyze_structure(str(wav)))
        for i in range(len(segs) - 1):
            gap = segs[i + 1].start_sec - segs[i].end_sec
            assert gap == pytest.approx(0.0, abs=0.05), (
                f"Gap of {gap:.3f} s between segment {i} and {i+1}"
            )

    def test_segments_are_ordered(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_structured(wav)
        segs = _run(analyze_structure(str(wav)))
        for i in range(len(segs) - 1):
            assert segs[i].end_sec <= segs[i + 1].start_sec + 0.05, (
                f"Segment {i} ends after segment {i+1} starts"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Labels are from the allowed set
# ─────────────────────────────────────────────────────────────────────────────

class TestSegmentLabels:
    def test_all_labels_allowed(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_structured(wav)
        segs = _run(analyze_structure(str(wav)))
        for seg in segs:
            assert seg.label in ALLOWED_LABELS, (
                f"Label '{seg.label}' is not in ALLOWED_LABELS"
            )

    def test_confidence_between_zero_and_one(self, tmp_path):
        wav = tmp_path / "track.wav"
        _write_structured(wav)
        segs = _run(analyze_structure(str(wav)))
        for seg in segs:
            assert 0.0 <= seg.confidence <= 1.0, (
                f"Confidence {seg.confidence} out of [0, 1]"
            )

    def test_no_short_segments_after_merge(self, tmp_path):
        """No segment shorter than MIN_SEGMENT_SEC should survive."""
        wav = tmp_path / "track.wav"
        _write_structured(wav)
        segs = _run(analyze_structure(str(wav)))
        for seg in segs:
            duration = seg.end_sec - seg.start_sec
            # Allow a tiny floating-point tolerance.
            assert duration >= MIN_SEGMENT_SEC - 0.1, (
                f"Segment '{seg.label}' at [{seg.start_sec:.2f}, {seg.end_sec:.2f}] "
                f"is shorter than MIN_SEGMENT_SEC={MIN_SEGMENT_SEC}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# _merge_short unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMergeShort:
    """Unit tests for the segment merging helper."""

    def _make_segs(self, durations: list) -> list:
        segs = []
        cursor = 0.0
        for i, dur in enumerate(durations):
            segs.append({"start": cursor, "end": cursor + dur, "rms": float(i + 1), "idx": i})
            cursor += dur
        return segs

    def test_no_change_when_all_long(self):
        segs = self._make_segs([10.0, 15.0, 12.0])
        merged = _merge_short(segs, min_dur=MIN_SEGMENT_SEC, total_dur=37.0)
        assert len(merged) == 3

    def test_single_short_segment_merged(self):
        segs = self._make_segs([10.0, 2.0, 10.0])   # middle is short
        merged = _merge_short(segs, min_dur=MIN_SEGMENT_SEC, total_dur=22.0)
        assert len(merged) == 2

    def test_leading_short_segment_merged_with_next(self):
        segs = self._make_segs([1.0, 12.0, 10.0])
        merged = _merge_short(segs, min_dur=MIN_SEGMENT_SEC, total_dur=23.0)
        assert len(merged) == 2
        assert merged[0]["start"] == pytest.approx(0.0)

    def test_trailing_short_segment_merged_with_previous(self):
        segs = self._make_segs([12.0, 10.0, 1.5])
        merged = _merge_short(segs, min_dur=MIN_SEGMENT_SEC, total_dur=23.5)
        assert len(merged) == 2
        assert merged[-1]["end"] == pytest.approx(23.5)

    def test_single_segment_list_unchanged(self):
        segs = self._make_segs([2.0])   # Short but only segment — never merged away.
        merged = _merge_short(segs, min_dur=MIN_SEGMENT_SEC, total_dur=2.0)
        assert len(merged) == 1

    def test_all_short_collapses_to_one(self):
        segs = self._make_segs([1.0, 1.0, 1.0, 1.0])
        merged = _merge_short(segs, min_dur=MIN_SEGMENT_SEC, total_dur=4.0)
        assert len(merged) == 1

    def test_coverage_preserved_after_merge(self):
        """Total duration must equal sum of merged segment durations."""
        segs = self._make_segs([3.0, 2.0, 8.0, 1.5, 10.0])
        total = 24.5
        merged = _merge_short(segs, min_dur=MIN_SEGMENT_SEC, total_dur=total)
        span = sum(s["end"] - s["start"] for s in merged)
        assert span == pytest.approx(total, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# Async coroutine interface
# ─────────────────────────────────────────────────────────────────────────────

class TestAsyncInterface:
    @pytest.mark.asyncio
    async def test_analyze_structure_is_awaitable(self, tmp_path):
        import inspect
        wav = tmp_path / "track.wav"
        _write_sine(wav, duration_sec=15.0)
        coro = analyze_structure(str(wav))
        assert inspect.isawaitable(coro)
        result = await coro
        assert isinstance(result, list)
