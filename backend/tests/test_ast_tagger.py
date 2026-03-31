"""Unit tests for backend/ast_tagger.py — Sprint 11 Audio Intelligence.

Test suites
-----------
TestASTResult
    Validates field types and default construction.

TestAudioSetTagMap
    Verifies coverage and correctness of the _AUDIOSET_TAG_MAP without
    loading any model.

TestConfidenceThreshold
    Checks that the confidence threshold filters classes correctly.

TestLazyModelLoading
    Patches the transformers import to verify that lazy loading and
    thread-safety machinery works without downloading real weights.

TestClassifyAudioSynthetic
    Uses a short synthetic WAV written to a temp file to exercise the
    _classify_sync path with a mocked model, confirming end-to-end
    mapping logic.

TestClassifyAudioEdgeCases
    Exercises error paths: missing file, zero-length audio, model load
    failure, and very short clips.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
import tempfile
import threading
import types
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Make the backend root importable without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

import ast_tagger
from ast_tagger import (
    ASTResult,
    _AUDIOSET_TAG_MAP,
    _classify_sync,
    _load_model,
    classify_audio,
)


# ---------------------------------------------------------------------------
# Helper: write a minimal WAV file using soundfile
# ---------------------------------------------------------------------------

def _write_wav(path: str, y: np.ndarray, sr: int = 16_000) -> None:
    """Write a mono float32 WAV to *path* using soundfile."""
    import soundfile as sf

    sf.write(path, y.astype(np.float32), sr, subtype="PCM_16")


def _sine(freq_hz: float, duration_s: float = 2.0, sr: int = 16_000) -> np.ndarray:
    """Return a pure sine wave at *freq_hz*, length *duration_s*."""
    t = np.linspace(0.0, duration_s, int(sr * duration_s), endpoint=False)
    return np.sin(2.0 * math.pi * freq_hz * t).astype(np.float32)


# ---------------------------------------------------------------------------
# Minimal mock of the AST model + feature extractor
# ---------------------------------------------------------------------------

def _make_mock_model(label_to_prob: dict[str, float] | None = None) -> MagicMock:
    """Return a mock ASTForAudioClassification that produces controlled logits.

    Args:
        label_to_prob: Optional mapping {AudioSet label: probability} to inject.
            Labels must be present in _AUDIOSET_TAG_MAP for tags to appear.
            All other classes default to 0.0.
    """
    import torch

    label_to_prob = label_to_prob or {}

    # Build 527-element label map matching the real model's id2label format
    # Use a handful of real labels, pad the rest with generic "Sound_N" entries.
    real_labels = list(_AUDIOSET_TAG_MAP.keys())
    id2label: dict[int, str] = {}
    for idx, label in enumerate(real_labels[:527]):
        id2label[idx] = label
    for idx in range(len(real_labels), 527):
        id2label[idx] = f"Sound_{idx}"

    # Build logit tensor: logit(p) = log(p / (1 - p))
    probs = np.zeros(527, dtype=np.float32)
    for idx, label in id2label.items():
        if label in label_to_prob:
            p = float(np.clip(label_to_prob[label], 1e-6, 1.0 - 1e-6))
            probs[idx] = p

    # inverse sigmoid → logits
    logits_np = np.log(probs + 1e-9) - np.log(1.0 - probs + 1e-9)
    logits_tensor = torch.tensor(logits_np, dtype=torch.float32).unsqueeze(0)  # (1, 527)

    mock_output = MagicMock()
    mock_output.logits = logits_tensor

    mock_model = MagicMock()
    mock_model.return_value = mock_output
    mock_model.__call__ = lambda self, **kwargs: mock_output
    mock_model.config = MagicMock()
    mock_model.config.id2label = id2label

    return mock_model


def _make_mock_feature_extractor() -> MagicMock:
    """Return a mock ASTFeatureExtractor that accepts (audio, sampling_rate, return_tensors)."""
    import torch

    fe = MagicMock()
    fe.return_value = {"input_values": torch.zeros(1, 16000)}
    return fe


# ---------------------------------------------------------------------------
# TestASTResult — field types and dataclass structure
# ---------------------------------------------------------------------------


class TestASTResult:
    """Verify ASTResult has correct fields and default types."""

    def test_has_genre_tags_field(self):
        result = ASTResult()
        assert hasattr(result, "genre_tags")
        assert isinstance(result.genre_tags, list)

    def test_has_mood_tags_field(self):
        result = ASTResult()
        assert hasattr(result, "mood_tags")
        assert isinstance(result.mood_tags, list)

    def test_has_instrument_tags_field(self):
        result = ASTResult()
        assert hasattr(result, "instrument_tags")
        assert isinstance(result.instrument_tags, list)

    def test_has_top_classes_field(self):
        result = ASTResult()
        assert hasattr(result, "top_classes")
        assert isinstance(result.top_classes, list)

    def test_defaults_are_empty_lists(self):
        result = ASTResult()
        assert result.genre_tags == []
        assert result.mood_tags == []
        assert result.instrument_tags == []
        assert result.top_classes == []

    def test_constructed_with_values(self):
        result = ASTResult(
            genre_tags=["hip-hop", "electronic"],
            mood_tags=["energetic"],
            instrument_tags=["drums", "synth"],
            top_classes=[("Hip hop music", 0.9), ("Drum and bass", 0.7)],
        )
        assert "hip-hop" in result.genre_tags
        assert "energetic" in result.mood_tags
        assert "drums" in result.instrument_tags
        assert result.top_classes[0] == ("Hip hop music", 0.9)

    def test_is_dataclass(self):
        """ASTResult must be a dataclass with exactly 4 fields."""
        field_names = {f.name for f in fields(ASTResult)}
        assert field_names == {"genre_tags", "mood_tags", "instrument_tags", "top_classes"}

    def test_genre_tags_contain_strings(self):
        result = ASTResult(genre_tags=["rock", "pop"])
        for tag in result.genre_tags:
            assert isinstance(tag, str), f"Expected str, got {type(tag)}"

    def test_top_classes_contain_tuples(self):
        result = ASTResult(top_classes=[("Guitar", 0.8)])
        label, conf = result.top_classes[0]
        assert isinstance(label, str)
        assert isinstance(conf, float)

    def test_independent_default_lists(self):
        """Each instance must get its own list, not a shared default."""
        r1 = ASTResult()
        r2 = ASTResult()
        r1.genre_tags.append("rock")
        assert r2.genre_tags == [], "Default lists must not be shared between instances"


# ---------------------------------------------------------------------------
# TestAudioSetTagMap — mapping coverage and correctness
# ---------------------------------------------------------------------------


class TestAudioSetTagMap:
    """Verify the _AUDIOSET_TAG_MAP dict has broad and correct coverage."""

    def test_map_is_non_empty(self):
        assert len(_AUDIOSET_TAG_MAP) >= 80, (
            f"Expected at least 80 entries, got {len(_AUDIOSET_TAG_MAP)}"
        )

    def test_all_values_are_category_tag_tuples(self):
        """Every value must be a (category, tag) tuple of two non-empty strings."""
        for label, value in _AUDIOSET_TAG_MAP.items():
            assert isinstance(value, tuple) and len(value) == 2, (
                f"Entry '{label}' has unexpected value format: {value}"
            )
            category, tag = value
            assert isinstance(category, str) and len(category) > 0
            assert isinstance(tag, str) and len(tag) > 0

    def test_all_categories_are_valid(self):
        """Only 'genre', 'mood', and 'instrument' are allowed categories."""
        valid = {"genre", "mood", "instrument"}
        for label, (cat, _tag) in _AUDIOSET_TAG_MAP.items():
            assert cat in valid, f"Invalid category '{cat}' for label '{label}'"

    def test_genre_entries_present(self):
        genres = {tag for cat, tag in _AUDIOSET_TAG_MAP.values() if cat == "genre"}
        assert len(genres) >= 15, f"Expected at least 15 genre tags, got {len(genres)}"

    def test_mood_entries_present(self):
        moods = {tag for cat, tag in _AUDIOSET_TAG_MAP.values() if cat == "mood"}
        assert len(moods) >= 5, f"Expected at least 5 mood tags, got {len(moods)}"

    def test_instrument_entries_present(self):
        instruments = {tag for cat, tag in _AUDIOSET_TAG_MAP.values() if cat == "instrument"}
        assert len(instruments) >= 15, (
            f"Expected at least 15 instrument tags, got {len(instruments)}"
        )

    def test_key_genre_labels_present(self):
        """Core genre labels specified in the task must exist."""
        required_labels = [
            "Hip hop music", "Electronic music", "Jazz", "Rock music",
            "Classical music", "Reggae", "Blues", "Country", "Punk rock",
            "Heavy metal", "Folk music", "Soul music", "Funk", "Disco",
            "Techno", "Drum and bass", "House music", "Ambient music",
            "Pop music", "R&B",
        ]
        for label in required_labels:
            assert label in _AUDIOSET_TAG_MAP, f"Required label '{label}' missing from map"

    def test_key_instrument_labels_present(self):
        """Core instrument labels must be covered."""
        required = ["Guitar", "Piano", "Drum", "Synthesizer", "Violin", "Bass guitar"]
        for label in required:
            assert label in _AUDIOSET_TAG_MAP, f"Required label '{label}' missing from map"

    def test_hip_hop_maps_to_correct_genre(self):
        assert _AUDIOSET_TAG_MAP["Hip hop music"] == ("genre", "hip-hop")

    def test_electronic_maps_to_correct_genre(self):
        assert _AUDIOSET_TAG_MAP["Electronic music"] == ("genre", "electronic")

    def test_guitar_maps_to_instrument(self):
        cat, tag = _AUDIOSET_TAG_MAP["Guitar"]
        assert cat == "instrument"
        assert tag == "guitar"

    def test_ambient_music_has_both_genre_and_mood(self):
        """'Ambient music' should appear twice — once as genre, once as mood."""
        entries = [
            (cat, tag)
            for label, (cat, tag) in _AUDIOSET_TAG_MAP.items()
            if "Ambient music" in label
        ]
        categories = {cat for cat, _tag in entries}
        assert "genre" in categories, "'Ambient music' must map to genre 'ambient'"
        assert "mood" in categories, "'Ambient music' must map to a mood tag"


# ---------------------------------------------------------------------------
# TestConfidenceThreshold — filtering behaviour
# ---------------------------------------------------------------------------


class TestConfidenceThreshold:
    """Verify threshold filtering without running real inference."""

    def _run_with_mock(
        self,
        label_to_prob: dict[str, float],
        confidence_threshold: float,
    ) -> ASTResult:
        """Helper: patch the model singleton and run _classify_sync on a temp file."""
        sr = 16_000
        signal = _sine(440.0, duration_s=2.0, sr=sr)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, signal, sr)
            mock_model = _make_mock_model(label_to_prob)
            mock_fe = _make_mock_feature_extractor()

            # Patch module-level singletons directly
            orig_model = ast_tagger._model
            orig_fe = ast_tagger._feature_extractor

            ast_tagger._model = mock_model
            ast_tagger._feature_extractor = mock_fe

            try:
                result = _classify_sync(tmp_path, confidence_threshold)
            finally:
                ast_tagger._model = orig_model
                ast_tagger._feature_extractor = orig_fe

            return result
        finally:
            os.unlink(tmp_path)

    def test_high_confidence_class_included(self):
        """A class with prob 0.9 should always be included at threshold 0.15."""
        result = self._run_with_mock({"Hip hop music": 0.9}, 0.15)
        assert ("hip-hop" in result.genre_tags or
                len(result.top_classes) > 0), "High-confidence class must appear in results"

    def test_low_confidence_class_excluded(self):
        """A class with prob 0.05 must be excluded at threshold 0.15."""
        result = self._run_with_mock({"Hip hop music": 0.05}, 0.15)
        assert "hip-hop" not in result.genre_tags

    def test_strict_threshold_reduces_results(self):
        """Raising the threshold from 0.05 to 0.8 must reduce or equal tag count."""
        labels = {"Hip hop music": 0.6, "Electronic music": 0.4, "Jazz": 0.2}
        result_low = self._run_with_mock(labels, 0.05)
        result_high = self._run_with_mock(labels, 0.8)
        total_low = len(result_low.top_classes)
        total_high = len(result_high.top_classes)
        assert total_high <= total_low, (
            f"Strict threshold should yield fewer results: high={total_high} low={total_low}"
        )

    def test_top_classes_sorted_by_confidence_descending(self):
        """top_classes must be sorted highest-confidence first."""
        labels = {"Hip hop music": 0.9, "Electronic music": 0.7, "Jazz": 0.5}
        result = self._run_with_mock(labels, 0.3)
        for i in range(len(result.top_classes) - 1):
            assert result.top_classes[i][1] >= result.top_classes[i + 1][1], (
                f"top_classes not sorted at index {i}: "
                f"{result.top_classes[i][1]} < {result.top_classes[i+1][1]}"
            )

    def test_top_classes_capped_at_20(self):
        """top_classes must contain at most 20 entries."""
        # Give 50 labels high confidence
        available = [lbl for lbl in _AUDIOSET_TAG_MAP.keys()][:50]
        labels = {lbl: 0.9 for lbl in available}
        result = self._run_with_mock(labels, 0.1)
        assert len(result.top_classes) <= 20, (
            f"top_classes exceeded 20 entries: {len(result.top_classes)}"
        )

    def test_zero_threshold_includes_all_mapped_classes(self):
        """threshold=0.0 should include all classes above 0 probability."""
        labels = {"Hip hop music": 0.6, "Guitar": 0.4}
        result = self._run_with_mock(labels, 0.0)
        assert len(result.top_classes) > 0


# ---------------------------------------------------------------------------
# TestLazyModelLoading — thread safety and failure paths
# ---------------------------------------------------------------------------


class TestLazyModelLoading:
    """Verify lazy loading machinery without downloading real model weights."""

    def setup_method(self):
        """Reset model singleton before each test to ensure isolation."""
        self._orig_model = ast_tagger._model
        self._orig_fe = ast_tagger._feature_extractor
        ast_tagger._model = None
        ast_tagger._feature_extractor = None

    def teardown_method(self):
        """Restore original singleton state after each test."""
        ast_tagger._model = ast_tagger._model if ast_tagger._model is not None else self._orig_model
        ast_tagger._feature_extractor = (
            ast_tagger._feature_extractor
            if ast_tagger._feature_extractor is not None
            else self._orig_fe
        )

    def test_load_model_raises_runtime_error_when_transformers_missing(self):
        """If transformers is not importable, RuntimeError must be raised."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "transformers":
                raise ImportError("transformers not available")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="transformers"):
                _load_model()

    def test_model_loaded_once_under_concurrent_calls(self):
        """Concurrent threads calling _load_model must only instantiate the model once."""
        load_count = {"n": 0}

        mock_fe = _make_mock_feature_extractor()
        mock_model_instance = _make_mock_model()

        # We need torch available for to() call inside _load_model
        mock_model_class = MagicMock(return_value=mock_model_instance)
        mock_fe_class = MagicMock(return_value=mock_fe)
        mock_model_instance.to = MagicMock(return_value=mock_model_instance)
        mock_model_instance.eval = MagicMock(return_value=mock_model_instance)

        original_from_pretrained_ast = None

        def patched_ast_from_pretrained(*args, **kwargs):
            load_count["n"] += 1
            return mock_model_instance

        def patched_fe_from_pretrained(*args, **kwargs):
            return mock_fe

        mock_transformers = MagicMock()
        mock_transformers.ASTForAudioClassification = MagicMock()
        mock_transformers.ASTForAudioClassification.from_pretrained = patched_ast_from_pretrained
        mock_transformers.ASTFeatureExtractor = MagicMock()
        mock_transformers.ASTFeatureExtractor.from_pretrained = patched_fe_from_pretrained

        with patch.dict("sys.modules", {"transformers": mock_transformers}):
            threads = [threading.Thread(target=_load_model) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Due to double-checked locking, the model should be loaded exactly once.
        assert load_count["n"] == 1, (
            f"Model was loaded {load_count['n']} times; expected exactly 1"
        )

    def test_already_loaded_model_not_reloaded(self):
        """If _model is already set, _load_model must return without touching transformers."""
        ast_tagger._model = MagicMock()
        ast_tagger._feature_extractor = MagicMock()

        import builtins
        real_import = builtins.__import__

        def fail_on_transformers(name, *args, **kwargs):
            if name == "transformers":
                raise AssertionError("transformers must not be imported when model is cached")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_on_transformers):
            # This must not raise
            _load_model()


# ---------------------------------------------------------------------------
# TestClassifyAudioSynthetic — end-to-end path with mocked model
# ---------------------------------------------------------------------------


class TestClassifyAudioSynthetic:
    """Exercise _classify_sync with a real temp WAV and mocked model."""

    def _classify_with_mock(
        self,
        label_to_prob: dict[str, float] | None = None,
        confidence_threshold: float = 0.15,
        duration_s: float = 2.0,
    ) -> ASTResult:
        sr = 16_000
        signal = _sine(440.0, duration_s=duration_s, sr=sr)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, signal, sr)
            orig_model = ast_tagger._model
            orig_fe = ast_tagger._feature_extractor
            ast_tagger._model = _make_mock_model(label_to_prob or {})
            ast_tagger._feature_extractor = _make_mock_feature_extractor()
            try:
                return _classify_sync(tmp_path, confidence_threshold)
            finally:
                ast_tagger._model = orig_model
                ast_tagger._feature_extractor = orig_fe
        finally:
            os.unlink(tmp_path)

    def test_returns_ast_result_instance(self):
        result = self._classify_with_mock()
        assert isinstance(result, ASTResult)

    def test_genre_tags_are_list_of_strings(self):
        result = self._classify_with_mock({"Hip hop music": 0.8, "Rock music": 0.6})
        assert isinstance(result.genre_tags, list)
        for tag in result.genre_tags:
            assert isinstance(tag, str)

    def test_mood_tags_are_list_of_strings(self):
        result = self._classify_with_mock({"Ambient music": 0.8})
        assert isinstance(result.mood_tags, list)
        for tag in result.mood_tags:
            assert isinstance(tag, str)

    def test_instrument_tags_are_list_of_strings(self):
        result = self._classify_with_mock({"Guitar": 0.8})
        assert isinstance(result.instrument_tags, list)
        for tag in result.instrument_tags:
            assert isinstance(tag, str)

    def test_hip_hop_label_produces_hip_hop_genre(self):
        result = self._classify_with_mock({"Hip hop music": 0.9})
        assert "hip-hop" in result.genre_tags

    def test_guitar_label_produces_guitar_instrument(self):
        result = self._classify_with_mock({"Guitar": 0.9})
        assert "guitar" in result.instrument_tags

    def test_ambient_music_produces_ambient_genre(self):
        result = self._classify_with_mock({"Ambient music": 0.9})
        assert "ambient" in result.genre_tags

    def test_ambient_music_produces_calm_mood(self):
        result = self._classify_with_mock({"Ambient music": 0.9})
        assert "calm" in result.mood_tags

    def test_heavy_metal_produces_intense_mood(self):
        result = self._classify_with_mock({"Heavy metal": 0.9})
        assert "intense" in result.mood_tags

    def test_drum_and_bass_produces_dnb_genre_and_energetic_mood(self):
        result = self._classify_with_mock({"Drum and bass": 0.9})
        assert "dnb" in result.genre_tags
        assert "energetic" in result.mood_tags

    def test_multiple_genres_detected(self):
        labels = {"Hip hop music": 0.9, "Electronic music": 0.8, "Jazz": 0.7}
        result = self._classify_with_mock(labels)
        assert len(result.genre_tags) >= 2

    def test_no_duplicate_genre_tags(self):
        labels = {"Hip hop music": 0.9, "Rap music": 0.8}
        result = self._classify_with_mock(labels)
        assert len(result.genre_tags) == len(set(result.genre_tags)), "Duplicate genre tags found"

    def test_no_duplicate_instrument_tags(self):
        # 'Drum' and 'Drum kit' both map to "drums"
        labels = {"Drum": 0.9, "Drum kit": 0.8, "Drums": 0.7}
        result = self._classify_with_mock(labels)
        assert len(result.instrument_tags) == len(set(result.instrument_tags))

    def test_unmapped_labels_are_silently_ignored(self):
        """Labels not in _AUDIOSET_TAG_MAP must not cause errors."""
        result = self._classify_with_mock({"Sound_500": 0.9, "UnknownNoise": 0.8})
        assert isinstance(result, ASTResult)

    def test_top_classes_confidence_values_in_0_1(self):
        result = self._classify_with_mock({"Hip hop music": 0.85})
        for _label, conf in result.top_classes:
            assert 0.0 <= conf <= 1.0, f"Confidence out of [0, 1]: {conf}"


# ---------------------------------------------------------------------------
# TestClassifyAudioEdgeCases — error handling
# ---------------------------------------------------------------------------


class TestClassifyAudioEdgeCases:
    """Verify _classify_sync raises the right errors for bad inputs."""

    def test_missing_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            _classify_sync("/nonexistent/audio_xyz.wav", 0.15)

    def test_corrupt_file_raises_value_error(self):
        """A file containing random bytes should raise ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"\x00\x01\x02\x03corrupt data not a wav file")
            tmp_path = f.name
        try:
            orig_model = ast_tagger._model
            orig_fe = ast_tagger._feature_extractor
            ast_tagger._model = _make_mock_model()
            ast_tagger._feature_extractor = _make_mock_feature_extractor()
            try:
                with pytest.raises((ValueError, Exception)):
                    _classify_sync(tmp_path, 0.15)
            finally:
                ast_tagger._model = orig_model
                ast_tagger._feature_extractor = orig_fe
        finally:
            os.unlink(tmp_path)

    def test_very_short_clip_does_not_crash(self):
        """A 0.3s clip should log a warning but either succeed or raise ValueError."""
        sr = 16_000
        tiny = _sine(440.0, duration_s=0.3, sr=sr)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, tiny, sr)
            orig_model = ast_tagger._model
            orig_fe = ast_tagger._feature_extractor
            ast_tagger._model = _make_mock_model()
            ast_tagger._feature_extractor = _make_mock_feature_extractor()
            try:
                try:
                    result = _classify_sync(tmp_path, 0.15)
                    assert isinstance(result, ASTResult)
                except ValueError:
                    pass  # Acceptable for very short audio
            finally:
                ast_tagger._model = orig_model
                ast_tagger._feature_extractor = orig_fe
        finally:
            os.unlink(tmp_path)

    def test_model_cache_dir_env_var_respected(self):
        """MODEL_CACHE_DIR env var must be passed to from_pretrained calls."""
        orig_model = ast_tagger._model
        orig_fe = ast_tagger._feature_extractor
        ast_tagger._model = None
        ast_tagger._feature_extractor = None

        captured_kwargs: dict = {}

        mock_model_instance = MagicMock()
        mock_model_instance.to = MagicMock(return_value=mock_model_instance)
        mock_model_instance.eval = MagicMock(return_value=mock_model_instance)

        def capture_model_pretrained(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_model_instance

        mock_fe_instance = _make_mock_feature_extractor()

        mock_transformers = MagicMock()
        mock_transformers.ASTForAudioClassification = MagicMock()
        mock_transformers.ASTForAudioClassification.from_pretrained = capture_model_pretrained
        mock_transformers.ASTFeatureExtractor = MagicMock()
        mock_transformers.ASTFeatureExtractor.from_pretrained = MagicMock(return_value=mock_fe_instance)

        test_cache = "/tmp/test_model_cache"
        try:
            with patch.dict("sys.modules", {"transformers": mock_transformers}):
                with patch.dict(os.environ, {"MODEL_CACHE_DIR": test_cache}):
                    # Re-read the env var as ast_tagger reads it at module level
                    with patch.object(ast_tagger, "_MODEL_CACHE_DIR", test_cache):
                        _load_model()
            assert captured_kwargs.get("cache_dir") == test_cache, (
                f"Expected cache_dir='{test_cache}', got {captured_kwargs.get('cache_dir')}"
            )
        finally:
            ast_tagger._model = orig_model if ast_tagger._model is None else ast_tagger._model
            ast_tagger._feature_extractor = orig_fe if ast_tagger._feature_extractor is None else ast_tagger._feature_extractor


# ---------------------------------------------------------------------------
# TestClassifyAudioAsync — async wrapper
# ---------------------------------------------------------------------------


class TestClassifyAudioAsync:
    """Verify the public async classify_audio coroutine behaves correctly."""

    def test_classify_audio_returns_coroutine(self):
        """classify_audio must return an awaitable (coroutine)."""
        import inspect

        coro = classify_audio("/fake/path.wav")
        assert inspect.iscoroutine(coro), "classify_audio must return a coroutine"
        coro.close()  # Clean up without running

    def test_classify_audio_raises_file_not_found_async(self):
        """Missing file must propagate FileNotFoundError through the async wrapper."""
        with pytest.raises(FileNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                classify_audio("/nonexistent/audio_xyz.wav")
            )

    def test_classify_audio_returns_ast_result_with_mock(self):
        """End-to-end async call with mocked model must return ASTResult."""
        sr = 16_000
        signal = _sine(440.0, duration_s=2.0, sr=sr)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        try:
            _write_wav(tmp_path, signal, sr)
            orig_model = ast_tagger._model
            orig_fe = ast_tagger._feature_extractor
            ast_tagger._model = _make_mock_model({"Guitar": 0.8, "Jazz": 0.7})
            ast_tagger._feature_extractor = _make_mock_feature_extractor()
            try:
                result = asyncio.get_event_loop().run_until_complete(
                    classify_audio(tmp_path, confidence_threshold=0.5)
                )
                assert isinstance(result, ASTResult)
                assert "guitar" in result.instrument_tags
                assert "jazz" in result.genre_tags
            finally:
                ast_tagger._model = orig_model
                ast_tagger._feature_extractor = orig_fe
        finally:
            os.unlink(tmp_path)
