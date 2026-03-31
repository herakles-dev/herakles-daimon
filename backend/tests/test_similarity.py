"""Tests for music_engine.find_similar_tracks — Sprint 11 S11.7 Audio Similarity.

Test suites
-----------
TestFindSimilarTracksInterface
    Guard: find_similar_tracks exists and is an async callable.

TestHybridScoringCalculation
    Pure-logic tests for the 0.6/0.2/0.2 hybrid scoring formula, verified
    without any DB round-trips.

TestFindSimilarTracksWithMockDB
    Functional tests using mocked fetch_one / fetch_all so every code path
    (audio embedding, text-embedding fallback, empty DB, no embedding) is
    covered without a live PostgreSQL instance.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make backend root importable without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

import music_engine
from music_engine import find_similar_tracks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_source_row(
    audio_embedding=None,
    embedding=None,
    embedding_dim=768,
    energy=5,
    bpm=120,
) -> MagicMock:
    """Build a fake asyncpg Record for the source track's media_embeddings row."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "audio_embedding": audio_embedding,
        "embedding": embedding,
        "embedding_dim": embedding_dim,
        "energy": energy,
        "bpm": bpm,
    }[key]
    return row


def _make_candidate_row(
    track_id: int,
    cosine_distance: float = 0.1,
    energy: int = 5,
    bpm: float = 120,
    title: str = "Candidate Track",
) -> MagicMock:
    """Build a fake asyncpg Record representing a candidate track from the DB."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": track_id,
        "title": title,
        "artist": "Artist",
        "album": "Album",
        "duration_sec": 240,
        "source": "upload",
        "energy": energy,
        "vibe": "chill-vibes",
        "mood_tags": ["chill"],
        "genre_tags": ["electronic"],
        "bpm": bpm,
        "musical_key": "Am",
        "camelot_code": "8A",
        "cosine_distance": cosine_distance,
    }[key]
    return row


# ---------------------------------------------------------------------------
# Interface guard
# ---------------------------------------------------------------------------

class TestFindSimilarTracksInterface:
    """Guard: find_similar_tracks must exist and be an async callable."""

    def test_callable_exists(self):
        assert callable(find_similar_tracks)

    def test_is_coroutine_function(self):
        import inspect
        assert inspect.iscoroutinefunction(find_similar_tracks)

    def test_signature_has_required_params(self):
        import inspect
        sig = inspect.signature(find_similar_tracks)
        params = sig.parameters
        assert "track_id" in params
        assert "limit" in params
        assert "user_id" in params
        assert "exclude_recent" in params


# ---------------------------------------------------------------------------
# Hybrid scoring — pure formula, no DB
# ---------------------------------------------------------------------------

class TestHybridScoringCalculation:
    """Verify the 0.6/0.2/0.2 hybrid scoring logic in isolation."""

    def _compute_score(
        self,
        cosine_distance: float,
        source_energy: float | None,
        candidate_energy: float | None,
        source_bpm: float | None,
        candidate_bpm: float | None,
    ) -> float:
        """Replicate the formula from find_similar_tracks."""
        audio_sim = 1.0 - cosine_distance

        if source_energy is not None and candidate_energy is not None:
            energy_closeness = 1.0 - abs(source_energy - candidate_energy) / 10.0
        else:
            energy_closeness = 0.5

        if source_bpm is not None and candidate_bpm is not None:
            bpm_closeness = 1.0 - min(abs(source_bpm - candidate_bpm) / 60.0, 1.0)
        else:
            bpm_closeness = 0.5

        return 0.6 * audio_sim + 0.2 * energy_closeness + 0.2 * bpm_closeness

    def test_perfect_match_scores_one(self):
        """Identical track (distance=0, same energy, same BPM) should score 1.0."""
        score = self._compute_score(0.0, 5.0, 5.0, 120.0, 120.0)
        assert abs(score - 1.0) < 1e-9

    def test_worst_case_scores_zero(self):
        """Maximum distance, opposite energy, max BPM difference scores near 0."""
        score = self._compute_score(1.0, 1.0, 10.0, 60.0, 180.0)
        # audio_sim=0, energy_closeness=0.1, bpm_closeness=0 → 0*0.6 + 0.1*0.2 + 0*0.2 = 0.02
        assert abs(score - 0.02) < 1e-9

    def test_missing_energy_uses_neutral(self):
        """When energy is None, energy_closeness defaults to 0.5."""
        score_with = self._compute_score(0.0, 5.0, 5.0, 120.0, 120.0)
        score_without = self._compute_score(0.0, None, None, 120.0, 120.0)
        # With perfect match: 0.6*1 + 0.2*1 + 0.2*1 = 1.0
        # Without energy:     0.6*1 + 0.2*0.5 + 0.2*1 = 0.9
        assert score_without < score_with
        assert abs(score_without - 0.9) < 1e-9

    def test_missing_bpm_uses_neutral(self):
        """When BPM is None, bpm_closeness defaults to 0.5."""
        score = self._compute_score(0.0, 5.0, 5.0, None, None)
        # 0.6*1 + 0.2*1 + 0.2*0.5 = 0.9
        assert abs(score - 0.9) < 1e-9

    def test_bpm_closeness_clamped_at_zero(self):
        """BPM delta > 60 should produce bpm_closeness=0, not negative."""
        score = self._compute_score(0.0, 5.0, 5.0, 60.0, 200.0)
        # delta=140 > 60 → clamped to 1.0 → bpm_closeness=0
        assert abs(score - (0.6 + 0.2)) < 1e-9

    def test_audio_weight_dominates(self):
        """Audio similarity carries 60% weight — closer vectors rank higher."""
        score_close = self._compute_score(0.05, 5.0, 7.0, 120.0, 150.0)
        score_far = self._compute_score(0.5, 5.0, 5.0, 120.0, 120.0)
        # score_far has perfect energy/bpm but a much worse audio distance
        # score_close: 0.6*0.95 + 0.2*0.8 + 0.2*(1-30/60) = 0.57+0.16+0.1 = 0.83
        # score_far:   0.6*0.5  + 0.2*1.0 + 0.2*1.0        = 0.30+0.20+0.20 = 0.70
        assert score_close > score_far


# ---------------------------------------------------------------------------
# Functional tests with mocked DB
# ---------------------------------------------------------------------------

class TestFindSimilarTracksWithMockDB:
    """Cover all code paths in find_similar_tracks using mocked DB helpers."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_returns_list_of_dicts_with_similarity_score(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """Happy path: returns a list of track dicts each containing similarity_score."""
        mock_recent.return_value = []

        audio_vec = [0.1] * 2048
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=audio_vec, energy=5, bpm=120
        )

        candidates = [
            _make_candidate_row(2, cosine_distance=0.1, energy=5, bpm=120),
            _make_candidate_row(3, cosine_distance=0.3, energy=7, bpm=140),
        ]
        mock_fetch_all.return_value = candidates

        results = self._run(find_similar_tracks(track_id=1, limit=10, user_id="default"))

        assert isinstance(results, list)
        assert len(results) == 2
        for r in results:
            assert "similarity_score" in r
            assert isinstance(r["similarity_score"], float)
            assert 0.0 <= r["similarity_score"] <= 1.0

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_excludes_source_track(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """The SQL query must exclude the source track_id."""
        mock_recent.return_value = []

        audio_vec = [0.0] * 2048
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=audio_vec, energy=5, bpm=120
        )
        mock_fetch_all.return_value = []

        self._run(find_similar_tracks(track_id=42, limit=5))

        # Inspect the SQL call: the exclude list must contain the source track_id
        call_args = mock_fetch_all.call_args
        # Second positional arg is $2 (exclude_ids list)
        exclude_ids_arg = call_args[0][2]  # positional args: (query, vec_literal, exclude_ids, limit*2)
        assert 42 in exclude_ids_arg

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_results_sorted_by_similarity_score_descending(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """Results must be sorted highest similarity_score first."""
        mock_recent.return_value = []
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=[0.0] * 2048, energy=5, bpm=120
        )
        mock_fetch_all.return_value = [
            _make_candidate_row(2, cosine_distance=0.5, energy=5, bpm=120),  # lower
            _make_candidate_row(3, cosine_distance=0.1, energy=5, bpm=120),  # higher
            _make_candidate_row(4, cosine_distance=0.3, energy=5, bpm=120),  # middle
        ]

        results = self._run(find_similar_tracks(track_id=1, limit=10))

        scores = [r["similarity_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_fallback_to_text_embedding_when_no_audio_embedding(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """When audio_embedding is None, uses the 768-dim text embedding instead."""
        mock_recent.return_value = []
        text_vec = [0.05] * 768
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=None, embedding=text_vec, energy=5, bpm=120
        )
        mock_fetch_all.return_value = [
            _make_candidate_row(2, cosine_distance=0.2, energy=5, bpm=120),
        ]

        results = self._run(find_similar_tracks(track_id=1, limit=5))

        assert len(results) == 1
        assert "similarity_score" in results[0]
        # Confirm the query string used me.embedding (text) not me.audio_embedding
        sql_query = mock_fetch_all.call_args[0][0]
        assert "me.embedding" in sql_query
        assert "me.audio_embedding" not in sql_query

    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_returns_empty_when_no_media_embeddings_row(
        self, mock_recent, mock_fetch_one
    ):
        """When the source track has no media_embeddings row, return []."""
        mock_recent.return_value = []
        mock_fetch_one.return_value = None  # no embeddings row

        results = self._run(find_similar_tracks(track_id=99, limit=10))

        assert results == []

    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_returns_empty_when_both_embeddings_are_none(
        self, mock_recent, mock_fetch_one
    ):
        """When both audio_embedding and embedding are None, return []."""
        mock_recent.return_value = []
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=None, embedding=None
        )

        results = self._run(find_similar_tracks(track_id=5, limit=10))

        assert results == []

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_returns_empty_when_no_candidates_in_db(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """When the vector query returns no rows, return []."""
        mock_recent.return_value = []
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=[0.0] * 2048, energy=5, bpm=120
        )
        mock_fetch_all.return_value = []  # empty DB

        results = self._run(find_similar_tracks(track_id=1, limit=10))

        assert results == []

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_respects_limit_parameter(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """Return at most `limit` results even if more candidates exist."""
        mock_recent.return_value = []
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=[0.0] * 2048, energy=5, bpm=120
        )
        # Provide 8 candidates but ask for limit=3
        mock_fetch_all.return_value = [
            _make_candidate_row(i, cosine_distance=0.1 * i)
            for i in range(2, 10)
        ]

        results = self._run(find_similar_tracks(track_id=1, limit=3))

        assert len(results) == 3

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_exclude_recent_adds_history_to_exclusion_list(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """When exclude_recent=True, recently played track IDs are excluded."""
        mock_recent.return_value = [10, 11, 12]
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=[0.0] * 2048, energy=5, bpm=120
        )
        mock_fetch_all.return_value = []

        self._run(find_similar_tracks(track_id=1, exclude_recent=True))

        call_args = mock_fetch_all.call_args[0]
        exclude_ids = call_args[2]  # $2 in query
        assert 10 in exclude_ids
        assert 11 in exclude_ids
        assert 12 in exclude_ids

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_exclude_recent_false_skips_history_lookup(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """When exclude_recent=False, _recent_track_ids must not be called."""
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=[0.0] * 2048, energy=5, bpm=120
        )
        mock_fetch_all.return_value = []

        self._run(find_similar_tracks(track_id=1, exclude_recent=False))

        mock_recent.assert_not_called()

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_never_raises_on_db_exception(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """DB errors must be swallowed and [] returned, not propagated."""
        mock_recent.return_value = []
        mock_fetch_one.side_effect = Exception("DB connection lost")

        results = self._run(find_similar_tracks(track_id=1, limit=5))

        assert results == []

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_audio_embedding_query_uses_2048_dim_cast(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """When a 2048-dim audio_embedding is used, the SQL cast must be vector(2048)."""
        mock_recent.return_value = []
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=[0.0] * 2048, energy=5, bpm=120
        )
        mock_fetch_all.return_value = []

        self._run(find_similar_tracks(track_id=1))

        sql = mock_fetch_all.call_args[0][0]
        assert "vector(2048)" in sql
        assert "me.audio_embedding" in sql

    @patch("music_engine.fetch_all", new_callable=AsyncMock)
    @patch("music_engine.fetch_one", new_callable=AsyncMock)
    @patch("music_engine._recent_track_ids", new_callable=AsyncMock)
    def test_missing_energy_and_bpm_uses_neutral_0_5(
        self, mock_recent, mock_fetch_one, mock_fetch_all
    ):
        """When source has no energy/bpm tags, scores must still be valid (neutral 0.5)."""
        mock_recent.return_value = []
        mock_fetch_one.return_value = _make_source_row(
            audio_embedding=[0.0] * 2048, energy=None, bpm=None
        )
        mock_fetch_all.return_value = [
            _make_candidate_row(2, cosine_distance=0.0, energy=None, bpm=None),
        ]

        results = self._run(find_similar_tracks(track_id=1, limit=5))

        assert len(results) == 1
        # audio_sim=1.0, energy_closeness=0.5, bpm_closeness=0.5
        # → 0.6*1.0 + 0.2*0.5 + 0.2*0.5 = 0.8
        assert abs(results[0]["similarity_score"] - 0.8) < 1e-4
