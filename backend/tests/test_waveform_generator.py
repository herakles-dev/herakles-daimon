"""Tests for backend/waveform_generator.py.

Two test suites:
- TestWaveformGeneratorInterface: guards the public API contract.
- TestWaveformGeneratorPureFunctions: unit tests that do not require audio
  files or librosa — they stub numpy/librosa to keep the suite fast and
  dependency-free.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the backend package is importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Interface guard
# ---------------------------------------------------------------------------

class TestWaveformGeneratorInterface:
    """Guard: the public callable must exist with the expected signature."""

    def test_generate_waveform_callable(self):
        from waveform_generator import generate_waveform
        assert callable(generate_waveform)

    def test_generate_waveform_is_coroutine(self):
        """generate_waveform must be an async function."""
        import inspect
        from waveform_generator import generate_waveform
        assert inspect.iscoroutinefunction(generate_waveform)


# ---------------------------------------------------------------------------
# Unit tests — stub out librosa and numpy so no audio files are needed
# ---------------------------------------------------------------------------

class TestWaveformGeneratorPureFunctions:
    """Unit-test the normalisation and windowing logic via mocked audio."""

    # ------------------------------------------------------------------
    # Helper: build a fake numpy array shaped like a mono audio signal
    # ------------------------------------------------------------------

    @staticmethod
    def _make_fake_audio(num_samples: int) -> "np.ndarray":  # type: ignore[name-defined]
        """Return a simple ramp signal 0 → 1 with *num_samples* points."""
        import numpy as np
        return np.linspace(0.0, 1.0, num=num_samples, dtype=np.float32)

    # ------------------------------------------------------------------
    # FileNotFoundError for missing files
    # ------------------------------------------------------------------

    def test_raises_file_not_found_for_missing_file(self):
        from waveform_generator import generate_waveform
        with pytest.raises(FileNotFoundError, match="not found"):
            asyncio.get_event_loop().run_until_complete(
                generate_waveform("/nonexistent/path/audio.mp3")
            )

    # ------------------------------------------------------------------
    # Output shape: returns exactly num_points values
    # ------------------------------------------------------------------

    def test_output_length_matches_num_points(self, tmp_path):
        """generate_waveform must return exactly num_points floats."""
        fake_file = tmp_path / "test.wav"
        fake_file.write_bytes(b"\x00" * 4)  # content irrelevant — librosa is mocked

        num_samples = 44100  # one second at 22 050 × 2
        num_points = 200

        fake_audio = self._make_fake_audio(num_samples)

        import numpy as np

        with patch("waveform_generator.Path") as mock_path_cls:
            mock_path_cls.return_value.exists.return_value = True
            with patch.dict("sys.modules", {"librosa": _make_librosa_mock(fake_audio)}):
                with patch.dict("sys.modules", {"numpy": np}):
                    from importlib import reload
                    import waveform_generator
                    reload(waveform_generator)
                    result = asyncio.get_event_loop().run_until_complete(
                        waveform_generator.generate_waveform(str(fake_file), num_points=num_points)
                    )

        assert len(result) == num_points

    # ------------------------------------------------------------------
    # Output range: all values in [0.0, 1.0]
    # ------------------------------------------------------------------

    def test_output_values_normalised_to_0_1(self, tmp_path):
        """All returned values must be in [0.0, 1.0]."""
        fake_file = tmp_path / "test.wav"
        fake_file.write_bytes(b"\x00" * 4)

        import numpy as np

        # Signal with arbitrary large amplitudes — after normalisation must be ≤ 1.0
        fake_audio = np.array([0.1, 0.5, 0.3, 0.9, 0.2] * 100, dtype=np.float32)

        with patch("waveform_generator.Path") as mock_path_cls:
            mock_path_cls.return_value.exists.return_value = True
            with patch.dict("sys.modules", {"librosa": _make_librosa_mock(fake_audio)}):
                with patch.dict("sys.modules", {"numpy": np}):
                    from importlib import reload
                    import waveform_generator
                    reload(waveform_generator)
                    result = asyncio.get_event_loop().run_until_complete(
                        waveform_generator.generate_waveform(str(fake_file), num_points=100)
                    )

        for value in result:
            assert 0.0 <= value <= 1.0, f"Value {value} out of [0, 1] range"

    # ------------------------------------------------------------------
    # Silent audio: all zeros → returns zeros (no division by zero)
    # ------------------------------------------------------------------

    def test_silent_audio_returns_zeros(self, tmp_path):
        """A completely silent file must return all-zero waveform without error."""
        fake_file = tmp_path / "silent.wav"
        fake_file.write_bytes(b"\x00" * 4)

        import numpy as np

        silent_audio = np.zeros(22050, dtype=np.float32)

        with patch("waveform_generator.Path") as mock_path_cls:
            mock_path_cls.return_value.exists.return_value = True
            with patch.dict("sys.modules", {"librosa": _make_librosa_mock(silent_audio)}):
                with patch.dict("sys.modules", {"numpy": np}):
                    from importlib import reload
                    import waveform_generator
                    reload(waveform_generator)
                    result = asyncio.get_event_loop().run_until_complete(
                        waveform_generator.generate_waveform(str(fake_file), num_points=50)
                    )

        assert all(v == 0.0 for v in result)

    # ------------------------------------------------------------------
    # Maximum value: the peak bar must reach 1.0
    # ------------------------------------------------------------------

    def test_peak_bar_reaches_1(self, tmp_path):
        """After normalisation the maximum value in the output must be 1.0."""
        fake_file = tmp_path / "test.wav"
        fake_file.write_bytes(b"\x00" * 4)

        import numpy as np

        # One very loud spike in the middle, rest quiet
        audio = np.zeros(22050, dtype=np.float32)
        audio[11000] = 0.8  # only one non-zero sample

        with patch("waveform_generator.Path") as mock_path_cls:
            mock_path_cls.return_value.exists.return_value = True
            with patch.dict("sys.modules", {"librosa": _make_librosa_mock(audio)}):
                with patch.dict("sys.modules", {"numpy": np}):
                    from importlib import reload
                    import waveform_generator
                    reload(waveform_generator)
                    result = asyncio.get_event_loop().run_until_complete(
                        waveform_generator.generate_waveform(str(fake_file), num_points=100)
                    )

        assert max(result) == pytest.approx(1.0, abs=1e-6), "Normalised peak must be 1.0"

    # ------------------------------------------------------------------
    # Default num_points = 800
    # ------------------------------------------------------------------

    def test_default_num_points_is_800(self, tmp_path):
        fake_file = tmp_path / "test.wav"
        fake_file.write_bytes(b"\x00" * 4)

        import numpy as np
        fake_audio = np.linspace(0.0, 1.0, 22050, dtype=np.float32)

        with patch("waveform_generator.Path") as mock_path_cls:
            mock_path_cls.return_value.exists.return_value = True
            with patch.dict("sys.modules", {"librosa": _make_librosa_mock(fake_audio)}):
                with patch.dict("sys.modules", {"numpy": np}):
                    from importlib import reload
                    import waveform_generator
                    reload(waveform_generator)
                    result = asyncio.get_event_loop().run_until_complete(
                        waveform_generator.generate_waveform(str(fake_file))
                    )

        assert len(result) == 800


# ---------------------------------------------------------------------------
# Helper: build a minimal librosa mock
# ---------------------------------------------------------------------------

def _make_librosa_mock(fake_audio):
    """Return a MagicMock shaped like librosa with librosa.load stubbed."""
    mock_librosa = MagicMock()
    mock_librosa.load.return_value = (fake_audio, 22050)
    return mock_librosa
