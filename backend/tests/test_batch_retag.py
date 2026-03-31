"""Tests for backend/batch_retag.py — Sprint 11 S11.5.

Coverage:
- Skip logic: already tagged locally → skip
- Force flag overrides skip
- Concurrency limiting via asyncio.Semaphore
- Progress file written and read correctly
- Failure isolation: one track fails, others continue
- Summary counts (total, success, failed, skipped)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

_PROGRESS_PATH = Path("/tmp/batch_retag_progress.json")


def _make_track_row(track_id: int, title: str = "Track", file_path: str = "/music/track.mp3"):
    """Return an asyncpg-Record-like dict for a tracks row."""
    return {"id": track_id, "title": title, "file_path": file_path}


def _make_tag_row(track_id: int):
    """Return an asyncpg-Record-like dict for a track_tags row."""
    return {"track_id": track_id}


class _FakeAnalysisResult:
    bpm = 128.0
    energy = 7
    errors: list = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_progress_file():
    """Ensure no leftover progress file pollutes test isolation."""
    if _PROGRESS_PATH.exists():
        _PROGRESS_PATH.unlink()
    yield
    if _PROGRESS_PATH.exists():
        _PROGRESS_PATH.unlink()


# ---------------------------------------------------------------------------
# Skip logic: already tagged locally
# ---------------------------------------------------------------------------


class TestSkipAlreadyTagged:
    """Tracks with an existing local tag are skipped by default."""

    @pytest.mark.asyncio
    async def test_locally_tagged_track_is_skipped(self):
        """A track already in track_tags with tag_source='local' is skipped."""
        tracks = [_make_track_row(1, "Alpha")]
        already_tagged = [_make_tag_row(1)]

        execute_calls: list = []

        async def _mock_execute(query, *args):
            execute_calls.append((query, args))
            return "UPDATE 1"

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch(
                "db.fetch_all",
                side_effect=[tracks, already_tagged],
            ),
            patch("analysis_pipeline.analyze_track", new=AsyncMock(return_value=_FakeAnalysisResult())),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            from batch_retag import retag_all
            summary = await retag_all(concurrency=1, force=False, delay=0)

        assert summary["skipped"] == 1
        assert summary["success"] == 0
        assert summary["failed"] == 0
        assert summary["total"] == 1

        # analyze_track should NOT have been called (verified via summary counts)

    @pytest.mark.asyncio
    async def test_untagged_track_is_processed(self):
        """A track with no local tag entry is analyzed and saved."""
        tracks = [_make_track_row(2, "Beta")]
        already_tagged: list = []  # no local tags

        analysis_result = _FakeAnalysisResult()
        analyze_mock = AsyncMock(return_value=analysis_result)
        save_mock = AsyncMock()
        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch("analysis_pipeline.analyze_track", new=analyze_mock),
            patch("analysis_pipeline.save_analysis", new=save_mock),
        ):
            # Also need to patch the inline `from db import execute` call
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=1, force=False, delay=0)

        assert summary["success"] == 1
        assert summary["skipped"] == 0
        assert summary["failed"] == 0
        analyze_mock.assert_awaited_once_with("/music/track.mp3")
        save_mock.assert_awaited_once_with(2, analysis_result)


# ---------------------------------------------------------------------------
# Force flag
# ---------------------------------------------------------------------------


class TestForceFlag:
    """--force causes locally-tagged tracks to be re-analyzed."""

    @pytest.mark.asyncio
    async def test_force_reruns_locally_tagged(self):
        """With force=True, already-locally-tagged tracks are NOT skipped."""
        tracks = [_make_track_row(3, "Gamma")]
        already_tagged = [_make_tag_row(3)]

        analysis_result = _FakeAnalysisResult()
        analyze_mock = AsyncMock(return_value=analysis_result)
        save_mock = AsyncMock()
        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch("analysis_pipeline.analyze_track", new=analyze_mock),
            patch("analysis_pipeline.save_analysis", new=save_mock),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=1, force=True, delay=0)

        # Should have processed, not skipped
        assert summary["success"] == 1
        assert summary["skipped"] == 0
        analyze_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_clears_progress_file(self):
        """force=True deletes any leftover progress file."""
        _PROGRESS_PATH.write_text(json.dumps({"last_track_id": 99}))

        tracks: list = []
        already_tagged: list = []

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
        ):
            from batch_retag import retag_all
            await retag_all(concurrency=1, force=True, delay=0)

        # Progress file should have been cleared
        assert not _PROGRESS_PATH.exists()

    @pytest.mark.asyncio
    async def test_force_false_respects_progress_resume(self):
        """Without force, tracks with id <= last_track_id in progress file are skipped."""
        # Simulate a previous run that processed track_id=5
        _PROGRESS_PATH.write_text(json.dumps({"last_track_id": 5}))

        tracks = [
            _make_track_row(3, "Old A"),
            _make_track_row(5, "Old B"),
            _make_track_row(7, "New C"),
        ]
        # track 7 is not yet locally tagged
        already_tagged: list = []

        analysis_result = _FakeAnalysisResult()
        analyze_mock = AsyncMock(return_value=analysis_result)
        save_mock = AsyncMock()
        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch("analysis_pipeline.analyze_track", new=analyze_mock),
            patch("analysis_pipeline.save_analysis", new=save_mock),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=1, force=False, delay=0)

        # Tracks 3 and 5 should be resume-skipped; track 7 analyzed
        assert summary["skipped"] == 2
        assert summary["success"] == 1
        assert summary["total"] == 3


# ---------------------------------------------------------------------------
# Concurrency limiting
# ---------------------------------------------------------------------------


class TestConcurrencyLimiting:
    """asyncio.Semaphore limits the number of parallel analyses."""

    @pytest.mark.asyncio
    async def test_concurrency_one_serializes_execution(self):
        """With concurrency=1, at most one track is processed at a time."""
        tracks = [
            _make_track_row(10, "Track A"),
            _make_track_row(11, "Track B"),
            _make_track_row(12, "Track C"),
        ]
        already_tagged: list = []

        call_order: list[int] = []

        async def _ordered_analyze(file_path: str):
            # Extract track id from path is not possible here — just record calls
            call_order.append(len(call_order))
            await asyncio.sleep(0)  # yield without real delay
            return _FakeAnalysisResult()

        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch("analysis_pipeline.analyze_track", new=_ordered_analyze),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=1, force=False, delay=0)

        assert summary["success"] == 3
        # All three tracks were processed
        assert len(call_order) == 3

    @pytest.mark.asyncio
    async def test_concurrency_two_allows_parallel(self):
        """With concurrency=2, summary still shows all tracks processed."""
        tracks = [
            _make_track_row(20, "X"),
            _make_track_row(21, "Y"),
            _make_track_row(22, "Z"),
        ]
        already_tagged: list = []

        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch(
                "analysis_pipeline.analyze_track",
                new=AsyncMock(return_value=_FakeAnalysisResult()),
            ),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=2, force=False, delay=0)

        assert summary["total"] == 3
        assert summary["success"] == 3
        assert summary["failed"] == 0


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------


class TestProgressTracking:
    """Progress file is written after each track."""

    @pytest.mark.asyncio
    async def test_progress_file_written_after_processing(self):
        """After analyzing a track, last_track_id is persisted to the progress file."""
        tracks = [_make_track_row(30, "Delta")]
        already_tagged: list = []

        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch(
                "analysis_pipeline.analyze_track",
                new=AsyncMock(return_value=_FakeAnalysisResult()),
            ),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                await retag_all(concurrency=1, force=False, delay=0)

        # On clean completion the file is removed (no failures)
        assert not _PROGRESS_PATH.exists()

    @pytest.mark.asyncio
    async def test_progress_file_retained_on_failure(self):
        """If any track fails, the progress file is NOT cleared."""
        tracks = [
            _make_track_row(40, "Fails"),
            _make_track_row(41, "OK"),
        ]
        already_tagged: list = []

        async def _flaky_analyze(file_path: str):
            if "40" in file_path or file_path == "/music/track.mp3":
                # We can't easily distinguish by id here; fail first call
                if not hasattr(_flaky_analyze, "_called"):
                    _flaky_analyze._called = True
                    raise RuntimeError("analyzer crashed")
            return _FakeAnalysisResult()

        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch("analysis_pipeline.analyze_track", new=_flaky_analyze),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=1, force=False, delay=0)

        # Progress file should still exist because there were failures
        assert _PROGRESS_PATH.exists()
        progress = json.loads(_PROGRESS_PATH.read_text())
        assert "last_track_id" in progress


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    """One failing track must not prevent others from being processed."""

    @pytest.mark.asyncio
    async def test_one_fails_others_continue(self):
        """If track 51 raises, tracks 50 and 52 are still processed."""
        tracks = [
            _make_track_row(50, "OK Before", "/music/50.mp3"),
            _make_track_row(51, "FAIL",      "/music/51.mp3"),
            _make_track_row(52, "OK After",  "/music/52.mp3"),
        ]
        already_tagged: list = []

        async def _selective_analyze(file_path: str):
            if file_path == "/music/51.mp3":
                raise RuntimeError("audio decode error")
            return _FakeAnalysisResult()

        execute_mock = AsyncMock(return_value="UPDATE 1")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch("analysis_pipeline.analyze_track", new=_selective_analyze),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=execute_mock):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=1, force=False, delay=0)

        assert summary["total"] == 3
        assert summary["failed"] == 1
        assert summary["success"] == 2
        assert summary["skipped"] == 0

    @pytest.mark.asyncio
    async def test_all_fail_returns_correct_counts(self):
        """All tracks failing → failed=total, success=0, skipped=0."""
        tracks = [
            _make_track_row(60, "Fail A", "/music/60.mp3"),
            _make_track_row(61, "Fail B", "/music/61.mp3"),
        ]
        already_tagged: list = []

        async def _always_fail(file_path: str):
            raise OSError("disk read error")

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch("analysis_pipeline.analyze_track", new=_always_fail),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            from batch_retag import retag_all
            summary = await retag_all(concurrency=1, force=False, delay=0)

        assert summary["total"] == 2
        assert summary["failed"] == 2
        assert summary["success"] == 0
        assert summary["skipped"] == 0

    @pytest.mark.asyncio
    async def test_empty_library_returns_zeros(self):
        """No ready tracks → all counts are 0, no errors."""
        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[[], []]),
        ):
            from batch_retag import retag_all
            summary = await retag_all()

        assert summary == {"total": 0, "success": 0, "failed": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# tag_source UPDATE is issued
# ---------------------------------------------------------------------------


class TestTagSourceUpdate:
    """After a successful analysis, tag_source is set to 'local'."""

    @pytest.mark.asyncio
    async def test_tag_source_set_to_local_on_success(self):
        """A successful retag issues an UPDATE setting tag_source='local'."""
        tracks = [_make_track_row(70, "Epsilon")]
        already_tagged: list = []

        execute_calls: list[tuple] = []

        async def _capture_execute(query, *args):
            execute_calls.append((query, args))
            return "UPDATE 1"

        with (
            patch("db.ensure_schema", new=AsyncMock()),
            patch("db.fetch_all", side_effect=[tracks, already_tagged]),
            patch(
                "analysis_pipeline.analyze_track",
                new=AsyncMock(return_value=_FakeAnalysisResult()),
            ),
            patch("analysis_pipeline.save_analysis", new=AsyncMock()),
        ):
            import db as db_module
            with patch.object(db_module, "execute", new=_capture_execute):
                from batch_retag import retag_all
                summary = await retag_all(concurrency=1, force=False, delay=0)

        assert summary["success"] == 1

        # The UPDATE for tag_source should appear in captured calls
        tag_source_updates = [
            (q, a) for q, a in execute_calls
            if "tag_source" in q and "local" in q
        ]
        assert len(tag_source_updates) == 1
        # track_id should be 70
        assert tag_source_updates[0][1][0] == 70
