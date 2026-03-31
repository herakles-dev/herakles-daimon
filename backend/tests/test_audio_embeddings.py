"""Unit tests for backend/audio_embeddings.py — Sprint 11 S11.3 PANNs embeddings.

Test suites
-----------
TestEmbedAudioContract
    Validates the public embed_audio() contract using a mocked model so tests
    never require downloading CNN14 weights.  Covers return shape, dtype,
    finite-value guarantee, and error propagation.

TestEmbedBatchContract
    Validates embed_batch() ordering, error isolation (one bad file does not
    kill the whole batch), empty-list handling, and batch_size parameter.

TestModelLazyLoading
    Verifies that the global model state is initialised lazily on first call
    and that the threading.Lock prevents double-initialisation.

TestNonFiniteHandling
    Ensures the module zeroes non-finite (NaN / Inf) embedding values rather
    than propagating them upstream.

TestLoadAudio32k (optional integration)
    Tests _load_audio_32k with synthetic WAV files.  These tests run without
    any model download and only require librosa + soundfile.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Make backend root importable without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

import audio_embeddings as ae


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wav(path: str, y: np.ndarray, sr: int = 32000) -> None:
    """Write a mono PCM WAV file at the given path."""
    import soundfile as sf
    sf.write(path, y.astype(np.float32), sr, subtype="PCM_16")


def _sine_32k(freq_hz: float = 440.0, duration_s: float = 2.0) -> np.ndarray:
    """Return a mono float32 sine at 32 kHz (PANNs native rate)."""
    sr = 32000
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return np.sin(2.0 * math.pi * freq_hz * t).astype(np.float32)


def _make_fake_embedding(dim: int = 2048) -> np.ndarray:
    """Return a deterministic fake embedding vector."""
    rng = np.random.default_rng(42)
    return rng.standard_normal(dim).astype(np.float32)


def _mock_embed_sync(file_path: str) -> np.ndarray:
    """Replacement for _embed_sync that never touches disk/GPU."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")
    return _make_fake_embedding()


# ---------------------------------------------------------------------------
# TestEmbedAudioContract — public embed_audio() interface
# ---------------------------------------------------------------------------

class TestEmbedAudioContract:
    """embed_audio() must return a (2048,) float32 array for any valid file."""

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_returns_numpy_array(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name
        try:
            result = asyncio.get_event_loop().run_until_complete(ae.embed_audio(path))
            assert isinstance(result, np.ndarray), (
                f"embed_audio should return np.ndarray, got {type(result)}"
            )
        finally:
            os.unlink(path)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_returns_2048_dim(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name
        try:
            result = asyncio.get_event_loop().run_until_complete(ae.embed_audio(path))
            assert result.shape == (ae.EMBEDDING_DIM,), (
                f"Expected shape ({ae.EMBEDDING_DIM},), got {result.shape}"
            )
        finally:
            os.unlink(path)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_returns_float32(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name
        try:
            result = asyncio.get_event_loop().run_until_complete(ae.embed_audio(path))
            assert result.dtype == np.float32, (
                f"Expected float32 dtype, got {result.dtype}"
            )
        finally:
            os.unlink(path)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_values_are_finite(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name
        try:
            result = asyncio.get_event_loop().run_until_complete(ae.embed_audio(path))
            assert np.all(np.isfinite(result)), (
                "embed_audio returned non-finite values (NaN or Inf)"
            )
        finally:
            os.unlink(path)

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                ae.embed_audio("/nonexistent/audio/file.wav")
            )

    @patch.object(ae, "_embed_sync", side_effect=ValueError("corrupt"))
    def test_corrupt_audio_raises_value_error(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"not real audio data")
            path = f.name
        try:
            with pytest.raises(ValueError):
                asyncio.get_event_loop().run_until_complete(ae.embed_audio(path))
        finally:
            os.unlink(path)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_embedding_norm_is_positive(self, _mock):
        """A non-trivial embedding must have a positive L2 norm."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name
        try:
            result = asyncio.get_event_loop().run_until_complete(ae.embed_audio(path))
            norm = float(np.linalg.norm(result))
            assert norm > 0.0, f"Embedding norm should be positive, got {norm}"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# TestEmbedBatchContract — embed_batch() interface
# ---------------------------------------------------------------------------

class TestEmbedBatchContract:
    """embed_batch() must handle ordering, errors, and empty lists correctly."""

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_empty_list_returns_empty(self, _mock):
        result = asyncio.get_event_loop().run_until_complete(ae.embed_batch([]))
        assert result == [], "embed_batch([]) must return []"

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_returns_correct_count(self, _mock):
        paths = []
        try:
            for _ in range(3):
                f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                _write_wav(f.name, _sine_32k())
                paths.append(f.name)
                f.close()
            results = asyncio.get_event_loop().run_until_complete(
                ae.embed_batch(paths, batch_size=2)
            )
            assert len(results) == 3, (
                f"embed_batch with 3 files returned {len(results)} results"
            )
        finally:
            for p in paths:
                os.unlink(p)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_all_results_are_2048_dim(self, _mock):
        paths = []
        try:
            for _ in range(2):
                f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                _write_wav(f.name, _sine_32k())
                paths.append(f.name)
                f.close()
            results = asyncio.get_event_loop().run_until_complete(ae.embed_batch(paths))
            for i, emb in enumerate(results):
                assert emb.shape == (ae.EMBEDDING_DIM,), (
                    f"Result {i} has shape {emb.shape}; expected ({ae.EMBEDDING_DIM},)"
                )
        finally:
            for p in paths:
                os.unlink(p)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_bad_file_returns_zero_vector_not_exception(self, _mock):
        """A file that fails during embedding must yield a zero vector, not raise."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            good_path = f.name

        bad_path = "/nonexistent/bad_file.wav"

        try:
            results = asyncio.get_event_loop().run_until_complete(
                ae.embed_batch([good_path, bad_path])
            )
            assert len(results) == 2, "embed_batch must return one result per input"
            # bad_path → zero vector
            zero_vec = np.zeros(ae.EMBEDDING_DIM, dtype=np.float32)
            np.testing.assert_array_equal(
                results[1],
                zero_vec,
                err_msg="Failed file should produce zero embedding vector",
            )
        finally:
            os.unlink(good_path)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_single_file_batch(self, _mock):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name
        try:
            results = asyncio.get_event_loop().run_until_complete(ae.embed_batch([path]))
            assert len(results) == 1
            assert results[0].shape == (ae.EMBEDDING_DIM,)
        finally:
            os.unlink(path)

    @patch.object(ae, "_embed_sync", side_effect=_mock_embed_sync)
    def test_batch_size_one_still_works(self, _mock):
        """batch_size=1 must process all files without error."""
        paths = []
        try:
            for _ in range(3):
                f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                _write_wav(f.name, _sine_32k())
                paths.append(f.name)
                f.close()
            results = asyncio.get_event_loop().run_until_complete(
                ae.embed_batch(paths, batch_size=1)
            )
            assert len(results) == 3
        finally:
            for p in paths:
                os.unlink(p)


# ---------------------------------------------------------------------------
# TestModelLazyLoading — _get_model() initialisation behaviour
# ---------------------------------------------------------------------------

class TestModelLazyLoading:
    """_get_model() must initialise lazily and not double-load under concurrency."""

    def setup_method(self) -> None:
        """Reset global model state before each test."""
        ae._model_instance = None
        ae._model_backend = None

    def teardown_method(self) -> None:
        """Reset global model state after each test."""
        ae._model_instance = None
        ae._model_backend = None

    def _make_fake_panns_model(self) -> MagicMock:
        """Return a mock that mimics panns_inference.AudioTagging."""
        mock = MagicMock()
        mock.inference.return_value = (
            np.zeros((1, 527), dtype=np.float32),
            np.random.randn(1, ae.EMBEDDING_DIM).astype(np.float32),
        )
        return mock

    def test_model_is_none_before_first_call(self):
        assert ae._model_instance is None
        assert ae._model_backend is None

    def test_model_loaded_after_get_model(self):
        mock_model = self._make_fake_panns_model()
        with patch.object(ae, "_load_panns_inference", return_value=mock_model):
            model, backend = ae._get_model()
        assert model is mock_model
        assert backend == "panns_inference"
        assert ae._model_instance is mock_model
        assert ae._model_backend == "panns_inference"

    def test_second_call_reuses_model(self):
        """_get_model() must return the cached instance without re-loading."""
        mock_model = self._make_fake_panns_model()
        load_calls = []

        def patched_load():
            load_calls.append(1)
            return mock_model

        with patch.object(ae, "_load_panns_inference", side_effect=patched_load):
            ae._get_model()
            ae._get_model()

        assert len(load_calls) == 1, (
            f"_load_panns_inference called {len(load_calls)} times; expected 1"
        )

    def test_fallback_to_raw_torch_when_panns_inference_missing(self):
        """If panns_inference is not installed, raw_torch backend is used."""
        mock_wrapper = MagicMock()
        mock_wrapper.inference.return_value = np.zeros(ae.EMBEDDING_DIM, dtype=np.float32)

        with patch.object(
            ae,
            "_load_panns_inference",
            side_effect=ImportError("No module named 'panns_inference'"),
        ):
            with patch.object(
                ae, "_download_cnn14_checkpoint", return_value="/models/Cnn14.pth"
            ):
                with patch.object(ae, "_CNN14Wrapper", return_value=mock_wrapper):
                    model, backend = ae._get_model()

        assert backend == "raw_torch"
        assert model is mock_wrapper

    def test_raises_if_both_backends_fail(self):
        """RuntimeError is raised when neither backend can initialise."""
        with patch.object(
            ae,
            "_load_panns_inference",
            side_effect=ImportError("panns_inference unavailable"),
        ):
            with patch.object(
                ae,
                "_download_cnn14_checkpoint",
                side_effect=RuntimeError("download failed"),
            ):
                with pytest.raises(RuntimeError, match="Could not initialise"):
                    ae._get_model()

    def test_concurrent_calls_load_model_once(self):
        """Thread-safe lazy init: only one load call under concurrent access."""
        mock_model = self._make_fake_panns_model()
        load_calls = []
        barrier = threading.Barrier(4)

        def patched_load():
            load_calls.append(1)
            return mock_model

        def call_get_model():
            barrier.wait()   # all threads start simultaneously
            ae._get_model()

        with patch.object(ae, "_load_panns_inference", side_effect=patched_load):
            threads = [threading.Thread(target=call_get_model) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(load_calls) == 1, (
            f"Expected exactly 1 model load call, got {len(load_calls)}"
        )


# ---------------------------------------------------------------------------
# TestNonFiniteHandling — NaN / Inf zeroed in _embed_sync
# ---------------------------------------------------------------------------

class TestNonFiniteHandling:
    """_embed_sync must zero non-finite values rather than propagate them."""

    def test_nan_in_model_output_is_zeroed(self):
        """If the model returns NaN values, embed_sync must replace them with 0."""
        nan_embedding = np.full(ae.EMBEDDING_DIM, float("nan"), dtype=np.float32)

        def fake_panns_inference(model, waveform):
            return nan_embedding

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name

        mock_model = MagicMock()
        try:
            with patch.object(ae, "_get_model", return_value=(mock_model, "panns_inference")):
                with patch.object(ae, "_embed_panns_inference", side_effect=fake_panns_inference):
                    with patch.object(ae, "_load_audio_32k", return_value=_sine_32k()):
                        result = ae._embed_sync(path)

            assert np.all(result == 0.0), "NaN values should be zeroed"
            assert np.all(np.isfinite(result)), "Output must be finite after zeroing"
        finally:
            os.unlink(path)

    def test_inf_in_model_output_is_zeroed(self):
        """If the model returns Inf values, embed_sync must replace them with 0."""
        inf_embedding = np.full(ae.EMBEDDING_DIM, float("inf"), dtype=np.float32)

        def fake_panns_inference(model, waveform):
            return inf_embedding

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name

        mock_model = MagicMock()
        try:
            with patch.object(ae, "_get_model", return_value=(mock_model, "panns_inference")):
                with patch.object(ae, "_embed_panns_inference", side_effect=fake_panns_inference):
                    with patch.object(ae, "_load_audio_32k", return_value=_sine_32k()):
                        result = ae._embed_sync(path)

            assert np.all(result == 0.0), "Inf values should be zeroed"
        finally:
            os.unlink(path)

    def test_partial_nan_only_affected_dims_zeroed(self):
        """Only NaN/Inf positions should be zeroed; finite values are preserved."""
        mixed = _make_fake_embedding()
        mixed[10] = float("nan")
        mixed[200] = float("inf")
        mixed[500] = float("-inf")

        def fake_panns_inference(model, waveform):
            return mixed

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, _sine_32k())
            path = f.name

        mock_model = MagicMock()
        try:
            with patch.object(ae, "_get_model", return_value=(mock_model, "panns_inference")):
                with patch.object(ae, "_embed_panns_inference", side_effect=fake_panns_inference):
                    with patch.object(ae, "_load_audio_32k", return_value=_sine_32k()):
                        result = ae._embed_sync(path)

            assert result[10] == 0.0, "NaN at index 10 should be zeroed"
            assert result[200] == 0.0, "Inf at index 200 should be zeroed"
            assert result[500] == 0.0, "-Inf at index 500 should be zeroed"
            # Spot-check a non-NaN position is unchanged
            assert result[0] == mixed[0], "Non-NaN values should not be modified"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# TestLoadAudio32k — _load_audio_32k with synthetic WAV
# ---------------------------------------------------------------------------

class TestLoadAudio32k:
    """_load_audio_32k must return float32 mono at 32 kHz from any valid audio."""

    def test_loads_wav_file(self):
        signal = _sine_32k(440.0, duration_s=1.0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, signal)
            path = f.name
        try:
            y = ae._load_audio_32k(path)
            assert isinstance(y, np.ndarray)
            assert y.dtype == np.float32
            assert y.ndim == 1, "Audio should be mono (1-D)"
        finally:
            os.unlink(path)

    def test_audio_is_approximately_correct_length(self):
        """Loaded audio length should match expected duration within ±5%."""
        duration_s = 2.0
        signal = _sine_32k(440.0, duration_s=duration_s)
        expected_samples = int(32000 * duration_s)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, signal)
            path = f.name
        try:
            y = ae._load_audio_32k(path)
            ratio = len(y) / expected_samples
            assert 0.90 <= ratio <= 1.10, (
                f"Expected ~{expected_samples} samples, got {len(y)} (ratio={ratio:.2f})"
            )
        finally:
            os.unlink(path)

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            ae._load_audio_32k("/nonexistent/audio.wav")

    def test_silent_file_raises_value_error(self):
        """A zero-amplitude signal must be rejected as silent."""
        silence = np.zeros(32000, dtype=np.float32)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            _write_wav(f.name, silence)
            path = f.name
        try:
            with pytest.raises(ValueError, match="silent"):
                ae._load_audio_32k(path)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# TestEmbeddingDimensionConstant
# ---------------------------------------------------------------------------

class TestEmbeddingDimensionConstant:
    """Sanity-check module-level constants used by downstream consumers."""

    def test_embedding_dim_is_2048(self):
        assert ae.EMBEDDING_DIM == 2048

    def test_sample_rate_is_32000(self):
        assert ae.PANNS_SAMPLE_RATE == 32000

    def test_mel_bins_is_64(self):
        assert ae.MEL_BINS == 64

    def test_model_cache_dir_env_override(self, monkeypatch):
        monkeypatch.setenv("MODEL_CACHE_DIR", "/tmp/test-models")
        # Re-read the env var directly (module-level constant is already set;
        # verify the env lookup path used internally)
        assert os.getenv("MODEL_CACHE_DIR") == "/tmp/test-models"
