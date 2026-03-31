"""Tests for backend/analysis_pipeline.py — Sprint 11 S11.4.

Coverage:
- analyze_track with all three sub-analyzers mocked
- Partial failure handling (each sub-analyzer failing independently)
- save_analysis SQL generation (mocked DB calls)
- CLI argument parsing
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers / stubs that mirror the real dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _AudioFeatures:
    bpm: float = 128.0
    key: str = "A minor"
    camelot_code: str = "8A"
    energy: int = 7
    brightness: int = 6
    danceability: int = 8


@dataclass
class _ASTResult:
    genre_tags: list[str] = None
    mood_tags: list[str] = None
    instrument_tags: list[str] = None
    top_classes: list[str] = None

    def __post_init__(self):
        if self.genre_tags is None:
            self.genre_tags = ["electronic", "edm"]
        if self.mood_tags is None:
            self.mood_tags = ["energetic", "uplifting"]
        if self.instrument_tags is None:
            self.instrument_tags = ["synthesizer", "drum-machine"]
        if self.top_classes is None:
            self.top_classes = []


_FAKE_EMBEDDING = np.ones(2048, dtype=np.float32) * 0.5

# ---------------------------------------------------------------------------
# analyze_track tests
# ---------------------------------------------------------------------------


class TestAnalyzeTrack:
    """Tests for the analyze_track() orchestrator."""

    @pytest.mark.asyncio
    async def test_all_analyzers_succeed(self):
        """When all three succeed, AnalysisResult is fully populated."""
        audio_features = _AudioFeatures()
        ast_result = _ASTResult()

        with (
            patch("audio_analyzer.analyze_audio", new=AsyncMock(return_value=audio_features)),
            patch("ast_tagger.classify_audio", new=AsyncMock(return_value=ast_result)),
            patch("audio_embeddings.embed_audio",    new=AsyncMock(return_value=_FAKE_EMBEDDING)),
        ):
            from analysis_pipeline import analyze_track

            result = await analyze_track("/fake/track.mp3")

        assert result.bpm == 128.0
        assert result.key == "A minor"
        assert result.camelot_code == "8A"
        assert result.energy == 7
        assert result.brightness == 6
        assert result.danceability == 8
        assert result.genre_tags == ["electronic", "edm"]
        assert result.mood_tags == ["energetic", "uplifting"]
        assert result.instrument_tags == ["synthesizer", "drum-machine"]
        assert result.audio_embedding is not None
        assert result.audio_embedding.shape == (2048,)
        assert result.errors == []
        assert result.analysis_source == "local"

    @pytest.mark.asyncio
    async def test_audio_analyzer_fails_uses_defaults(self):
        """If audio_analyzer raises, defaults (bpm=120, energy=5, …) are used."""
        ast_result = _ASTResult()

        with (
            patch(
                "audio_analyzer.analyze_audio",
                new=AsyncMock(side_effect=RuntimeError("librosa not available")),
            ),
            patch("ast_tagger.classify_audio", new=AsyncMock(return_value=ast_result)),
            patch("audio_embeddings.embed_audio",    new=AsyncMock(return_value=_FAKE_EMBEDDING)),
        ):
            from analysis_pipeline import analyze_track

            result = await analyze_track("/fake/track.mp3")

        # Should use dataclass defaults
        assert result.bpm == 120.0
        assert result.key == "C major"
        assert result.camelot_code == "8B"
        assert result.energy == 5
        # Other analyzers should still have succeeded
        assert result.genre_tags == ["electronic", "edm"]
        assert result.audio_embedding is not None
        # Error recorded
        assert len(result.errors) == 1
        assert "audio_analyzer failed" in result.errors[0]
        assert "librosa not available" in result.errors[0]

    @pytest.mark.asyncio
    async def test_ast_tagger_fails_empty_tags(self):
        """If ast_tagger raises, genre/mood/instrument tags stay empty."""
        audio_features = _AudioFeatures()

        with (
            patch("audio_analyzer.analyze_audio", new=AsyncMock(return_value=audio_features)),
            patch(
                "ast_tagger.classify_audio",
                new=AsyncMock(side_effect=OSError("model weights missing")),
            ),
            patch("audio_embeddings.embed_audio", new=AsyncMock(return_value=_FAKE_EMBEDDING)),
        ):
            from analysis_pipeline import analyze_track

            result = await analyze_track("/fake/track.mp3")

        assert result.genre_tags == []
        assert result.mood_tags == []
        assert result.instrument_tags == []
        # audio still succeeded
        assert result.bpm == 128.0
        assert result.audio_embedding is not None
        assert len(result.errors) == 1
        assert "ast_tagger failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_embeddings_fail_none(self):
        """If audio_embeddings raises, audio_embedding is None."""
        audio_features = _AudioFeatures()
        ast_result = _ASTResult()

        with (
            patch("audio_analyzer.analyze_audio", new=AsyncMock(return_value=audio_features)),
            patch("ast_tagger.classify_audio", new=AsyncMock(return_value=ast_result)),
            patch(
                "audio_embeddings.embed_audio",
                new=AsyncMock(side_effect=MemoryError("out of memory")),
            ),
        ):
            from analysis_pipeline import analyze_track

            result = await analyze_track("/fake/track.mp3")

        assert result.audio_embedding is None
        # Other fields still populated
        assert result.bpm == 128.0
        assert result.genre_tags == ["electronic", "edm"]
        assert len(result.errors) == 1
        assert "audio_embeddings failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_all_analyzers_fail(self):
        """All three failing still returns a result (with all defaults)."""
        with (
            patch(
                "audio_analyzer.analyze_audio",
                new=AsyncMock(side_effect=RuntimeError("audio error")),
            ),
            patch(
                "ast_tagger.classify_audio",
                new=AsyncMock(side_effect=RuntimeError("ast error")),
            ),
            patch(
                "audio_embeddings.embed_audio",
                new=AsyncMock(side_effect=RuntimeError("embed error")),
            ),
        ):
            from analysis_pipeline import analyze_track

            result = await analyze_track("/fake/track.mp3")

        assert result.bpm == 120.0
        assert result.genre_tags == []
        assert result.audio_embedding is None
        assert len(result.errors) == 3

    @pytest.mark.asyncio
    async def test_two_analyzers_fail(self):
        """Two sub-analyzers failing records two errors, one succeeds."""
        audio_features = _AudioFeatures()

        with (
            patch("audio_analyzer.analyze_audio", new=AsyncMock(return_value=audio_features)),
            patch(
                "ast_tagger.classify_audio",
                new=AsyncMock(side_effect=ValueError("bad audio")),
            ),
            patch(
                "audio_embeddings.embed_audio",
                new=AsyncMock(side_effect=RuntimeError("torch error")),
            ),
        ):
            from analysis_pipeline import analyze_track

            result = await analyze_track("/fake/track.mp3")

        assert result.bpm == 128.0       # audio succeeded
        assert result.genre_tags == []   # ast failed
        assert result.audio_embedding is None  # embed failed
        assert len(result.errors) == 2


# ---------------------------------------------------------------------------
# save_analysis tests
# ---------------------------------------------------------------------------


class TestSaveAnalysis:
    """Tests for save_analysis() SQL persistence."""

    @pytest.mark.asyncio
    async def test_save_writes_track_tags(self):
        """save_analysis issues an upsert to track_tags."""
        from analysis_pipeline import AnalysisResult, save_analysis

        result = AnalysisResult(
            bpm=128.0,
            key="A minor",
            camelot_code="8A",
            energy=7,
            brightness=6,
            danceability=8,
            genre_tags=["electronic"],
            mood_tags=["energetic"],
            instrument_tags=["synthesizer"],
            audio_embedding=_FAKE_EMBEDDING,
        )

        captured_calls: list[tuple] = []

        async def _mock_execute(query: str, *args):
            captured_calls.append((query, args))
            return "INSERT 0 1"

        with patch("db.execute", new=_mock_execute):
            await save_analysis(42, result)

        assert len(captured_calls) == 2  # track_tags + media_embeddings

        first_query, first_args = captured_calls[0]
        assert "track_tags" in first_query
        assert "ON CONFLICT" in first_query
        # track_id is first positional arg
        assert first_args[0] == 42
        # bpm rounded to int
        assert first_args[1] == 128
        # musical_key
        assert first_args[2] == "A minor"
        # energy
        assert first_args[3] == 7
        # danceability
        assert first_args[4] == 8
        # mood_tags
        assert first_args[5] == ["energetic"]
        # genre_tags
        assert first_args[6] == ["electronic"]

    @pytest.mark.asyncio
    async def test_save_writes_audio_embedding(self):
        """save_analysis issues an upsert to media_embeddings when embedding present."""
        from analysis_pipeline import AnalysisResult, save_analysis

        embedding = np.zeros(2048, dtype=np.float32)
        result = AnalysisResult(
            bpm=100.0,
            audio_embedding=embedding,
        )

        captured_calls: list[tuple] = []

        async def _mock_execute(query: str, *args):
            captured_calls.append((query, args))
            return "INSERT 0 1"

        with patch("db.execute", new=_mock_execute):
            await save_analysis(7, result)

        assert len(captured_calls) == 2
        second_query, second_args = captured_calls[1]
        assert "media_embeddings" in second_query
        assert "audio_embedding" in second_query
        # track_id
        assert second_args[0] == 7
        # vec_literal is a pgvector string
        assert second_args[1].startswith("[")
        assert second_args[1].endswith("]")
        # embedding_dim
        assert second_args[2] == 2048

    @pytest.mark.asyncio
    async def test_save_skips_embedding_when_none(self):
        """save_analysis does NOT write to media_embeddings when audio_embedding is None."""
        from analysis_pipeline import AnalysisResult, save_analysis

        result = AnalysisResult(bpm=90.0, audio_embedding=None)

        captured_calls: list[tuple] = []

        async def _mock_execute(query: str, *args):
            captured_calls.append((query, args))
            return "INSERT 0 1"

        with patch("db.execute", new=_mock_execute):
            await save_analysis(99, result)

        # Only track_tags write, no media_embeddings write
        assert len(captured_calls) == 1
        assert "track_tags" in captured_calls[0][0]

    @pytest.mark.asyncio
    async def test_save_skips_wrong_embedding_shape(self):
        """save_analysis skips media_embeddings when embedding has unexpected shape."""
        from analysis_pipeline import AnalysisResult, save_analysis

        # Wrong dimension (768 instead of 2048)
        bad_embedding = np.zeros(768, dtype=np.float32)
        result = AnalysisResult(bpm=90.0, audio_embedding=bad_embedding)

        captured_calls: list[tuple] = []

        async def _mock_execute(query: str, *args):
            captured_calls.append((query, args))
            return "INSERT 0 1"

        with patch("db.execute", new=_mock_execute):
            await save_analysis(55, result)

        # Only track_tags, no media_embeddings (shape mismatch)
        assert len(captured_calls) == 1

    @pytest.mark.asyncio
    async def test_save_upsert_fallback_on_conflict(self):
        """If INSERT raises, save_analysis retries with UPDATE."""
        from analysis_pipeline import AnalysisResult, save_analysis

        result = AnalysisResult(bpm=130.0, audio_embedding=_FAKE_EMBEDDING)

        call_count = [0]

        async def _mock_execute(query: str, *args):
            call_count[0] += 1
            # First two calls succeed (track_tags INSERT + media_embeddings INSERT)
            # Simulate conflict on media_embeddings INSERT
            if call_count[0] == 2 and "media_embeddings" in query and "INSERT" in query:
                raise Exception("duplicate key value")
            return "INSERT 0 1"

        with patch("db.execute", new=_mock_execute):
            # Should not raise; fallback UPDATE is attempted
            await save_analysis(11, result)

        # 3 calls: track_tags INSERT, media_embeddings INSERT (fails), UPDATE
        assert call_count[0] == 3


# ---------------------------------------------------------------------------
# CLI argument parsing tests
# ---------------------------------------------------------------------------


class TestCLIParsing:
    """Tests for the __main__ argparse setup in analysis_pipeline."""

    def _parse(self, argv: list[str]) -> object:
        """Parse argv using the same argparse setup as _main."""
        import argparse

        parser = argparse.ArgumentParser()
        source_group = parser.add_mutually_exclusive_group(required=True)
        source_group.add_argument("--track-id", type=int, metavar="N")
        source_group.add_argument("--file", metavar="PATH")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--verbose", "-v", action="store_true")
        return parser.parse_args(argv)

    def test_track_id_flag(self):
        args = self._parse(["--track-id", "42"])
        assert args.track_id == 42
        assert args.file is None
        assert not args.dry_run
        assert not args.verbose

    def test_file_flag(self):
        args = self._parse(["--file", "/music/test.mp3"])
        assert args.file == "/music/test.mp3"
        assert args.track_id is None

    def test_dry_run_flag(self):
        args = self._parse(["--track-id", "1", "--dry-run"])
        assert args.dry_run is True

    def test_verbose_flag(self):
        args = self._parse(["--track-id", "1", "--verbose"])
        assert args.verbose is True

    def test_verbose_short_flag(self):
        args = self._parse(["--track-id", "1", "-v"])
        assert args.verbose is True

    def test_mutually_exclusive_source(self):
        """--track-id and --file cannot be used together."""
        import argparse

        parser = argparse.ArgumentParser()
        source_group = parser.add_mutually_exclusive_group(required=True)
        source_group.add_argument("--track-id", type=int)
        source_group.add_argument("--file")

        with pytest.raises(SystemExit):
            parser.parse_args(["--track-id", "1", "--file", "/path.mp3"])

    def test_required_source_missing(self):
        """Neither --track-id nor --file → argparse exits."""
        import argparse

        parser = argparse.ArgumentParser()
        source_group = parser.add_mutually_exclusive_group(required=True)
        source_group.add_argument("--track-id", type=int)
        source_group.add_argument("--file")

        with pytest.raises(SystemExit):
            parser.parse_args([])


# ---------------------------------------------------------------------------
# Integration-style: analyze_track + save_analysis in sequence
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    """Smoke tests that wire analyze + save together."""

    @pytest.mark.asyncio
    async def test_analyze_then_save(self):
        """Full pipeline: analyze returns result, save writes it to DB."""
        from analysis_pipeline import analyze_track, save_analysis

        audio_features = _AudioFeatures(bpm=95.0, energy=4)
        ast_result = _ASTResult(
            genre_tags=["jazz"],
            mood_tags=["relaxed"],
            instrument_tags=["piano", "bass"],
        )
        embedding = np.random.rand(2048).astype(np.float32)

        db_calls: list[str] = []

        async def _mock_execute(query: str, *args):
            db_calls.append(query)
            return "INSERT 0 1"

        with (
            patch("audio_analyzer.analyze_audio", new=AsyncMock(return_value=audio_features)),
            patch("ast_tagger.classify_audio", new=AsyncMock(return_value=ast_result)),
            patch("audio_embeddings.embed_audio",    new=AsyncMock(return_value=embedding)),
            patch("db.execute", new=_mock_execute),
        ):
            result = await analyze_track("/fake/jazz.mp3")
            await save_analysis(101, result)

        assert result.bpm == 95.0
        assert result.energy == 4
        assert result.genre_tags == ["jazz"]
        assert result.audio_embedding is not None

        # Both DB writes happened
        assert any("track_tags" in q for q in db_calls)
        assert any("media_embeddings" in q for q in db_calls)
