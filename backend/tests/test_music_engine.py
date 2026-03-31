"""Tests for the music engine module.

Three suites:
- TestMusicEngineInterface: imports-only checks that ensure every public
  function exists with the expected callable signature.  These guard against
  renames or accidental deletions.
- TestMusicEnginePureFunctions: unit tests for the pure (no-DB) helpers
  exposed by the module: _merge_excludes, _no_music_sentinel, _row_to_dict,
  _library_row_to_dict.
- TestCamelotCompatibility: unit tests for get_compatible_keys covering
  normal cases, boundary wrapping, and invalid inputs.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import music_engine
from music_engine import (
    _library_row_to_dict,
    _merge_excludes,
    _no_music_sentinel,
    _row_to_dict,
    get_compatible_keys,
)


# ─────────────────────────────────────────────────────────────────────────────
# Interface guard
# ─────────────────────────────────────────────────────────────────────────────

class TestMusicEngineInterface:
    """Guard: all public async callables must exist."""

    def test_fetch_track_callable(self):
        assert callable(music_engine.fetch_track)

    def test_skip_track_callable(self):
        assert callable(music_engine.skip_track)

    def test_log_track_playback_callable(self):
        assert callable(music_engine.log_track_playback)

    def test_get_library_callable(self):
        assert callable(music_engine.get_library)

    def test_get_track_stats_callable(self):
        assert callable(music_engine.get_track_stats)

    def test_get_compatible_keys_callable(self):
        assert callable(music_engine.get_compatible_keys)


# ─────────────────────────────────────────────────────────────────────────────
# _merge_excludes — pure function, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestMergeExcludes:
    """Unit tests for the vibe-exclusion merge helper."""

    def test_no_request_excludes_returns_pref_excludes(self):
        result = _merge_excludes(None, ["dark", "aggressive"])
        assert set(result) == {"dark", "aggressive"}

    def test_no_pref_excludes_returns_request_excludes(self):
        result = _merge_excludes(["happy"], [])
        assert "happy" in result

    def test_deduplication(self):
        """Same vibe appearing in both sources should appear only once."""
        result = _merge_excludes(["dark"], ["dark", "loud"])
        assert result.count("dark") == 1
        assert "loud" in result

    def test_both_none_empty_returns_empty(self):
        result = _merge_excludes(None, [])
        assert result == []

    def test_returns_union(self):
        result = _merge_excludes(["a", "b"], ["c", "d"])
        assert set(result) == {"a", "b", "c", "d"}

    def test_empty_request_excludes_uses_prefs(self):
        result = _merge_excludes([], ["chill"])
        assert "chill" in result


# ─────────────────────────────────────────────────────────────────────────────
# _no_music_sentinel — pure function
# ─────────────────────────────────────────────────────────────────────────────

class TestNoMusicSentinel:
    """Sentinel dict must conform to the expected contract."""

    def test_sentinel_has_no_content_flag(self):
        s = _no_music_sentinel()
        assert s["no_content"] is True

    def test_sentinel_id_is_none(self):
        assert _no_music_sentinel()["id"] is None

    def test_sentinel_hls_url_is_none(self):
        assert _no_music_sentinel()["hls_url"] is None

    def test_sentinel_mood_tags_is_list(self):
        assert isinstance(_no_music_sentinel()["mood_tags"], list)

    def test_sentinel_genre_tags_is_list(self):
        assert isinstance(_no_music_sentinel()["genre_tags"], list)

    def test_sentinel_has_message_key(self):
        s = _no_music_sentinel()
        assert "message" in s
        assert len(s["message"]) > 0

    def test_sentinel_title_is_human_readable(self):
        """title must not be None so callers can display it."""
        assert _no_music_sentinel()["title"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# _row_to_dict — pure function (mocks asyncpg Record with a plain dict)
# ─────────────────────────────────────────────────────────────────────────────

class TestRowToDict:
    """_row_to_dict converts an asyncpg-style Record to a JSON-safe dict."""

    def _make_row(self, **overrides):
        """Return a minimal mock row compatible with _row_to_dict."""
        base = {
            "id": 42,
            "title": "Chill Beat",
            "artist": "DJ Test",
            "album": "Test Album",
            "duration_sec": 200,
            "source": "upload",
            "energy": 6,
            "vibe": "chill-vibes",
            "mood_tags": ["chill", "ambient"],
            "genre_tags": ["electronic"],
            "bpm": 90,
            "musical_key": "Cm",
            "camelot_code": "8A",
        }
        base.update(overrides)
        return base

    def test_id_preserved(self):
        row = self._make_row(id=7)
        d = _row_to_dict(row)
        assert d["id"] == 7

    def test_hls_url_uses_id(self):
        row = self._make_row(id=99)
        d = _row_to_dict(row)
        assert "99" in d["hls_url"]
        assert d["hls_url"].endswith("stream.m3u8")

    def test_artwork_url_uses_id(self):
        row = self._make_row(id=99)
        d = _row_to_dict(row)
        assert "99" in d["artwork_url"]
        assert "artwork" in d["artwork_url"]

    def test_none_duration_remains_none(self):
        row = self._make_row(duration_sec=None)
        d = _row_to_dict(row)
        assert d["duration_sec"] is None

    def test_mood_tags_none_becomes_empty_list(self):
        row = self._make_row(mood_tags=None)
        d = _row_to_dict(row)
        assert d["mood_tags"] == []

    def test_genre_tags_none_becomes_empty_list(self):
        row = self._make_row(genre_tags=None)
        d = _row_to_dict(row)
        assert d["genre_tags"] == []

    def test_energy_coerced_to_int(self):
        row = self._make_row(energy=7)
        d = _row_to_dict(row)
        assert isinstance(d["energy"], int)

    def test_none_energy_remains_none(self):
        row = self._make_row(energy=None)
        d = _row_to_dict(row)
        assert d["energy"] is None


# ─────────────────────────────────────────────────────────────────────────────
# _library_row_to_dict — pure function
# ─────────────────────────────────────────────────────────────────────────────

class TestLibraryRowToDict:
    """_library_row_to_dict covers the library listing shape."""

    def _make_row(self, **overrides):
        from datetime import datetime, timezone
        base = {
            "id": 10,
            "title": "Library Track",
            "artist": "Artist A",
            "album": "Compilation",
            "duration_sec": 180,
            "source": "jamendo",
            "status": "ready",
            "created_at": datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            "energy": 4,
            "vibe": "upbeat",
            "mood_tags": ["happy", "energetic"],
            "genre_tags": ["jazz"],
            "bpm": 120,
            "musical_key": "G",
        }
        base.update(overrides)
        return base

    def test_has_hls_url(self):
        d = _library_row_to_dict(self._make_row())
        assert "stream.m3u8" in d["hls_url"]

    def test_has_artwork_url(self):
        d = _library_row_to_dict(self._make_row())
        assert "artwork" in d["artwork_url"]

    def test_created_at_is_iso_string(self):
        d = _library_row_to_dict(self._make_row())
        assert isinstance(d["created_at"], str)
        # Basic ISO 8601 format check
        assert "2024" in d["created_at"]

    def test_none_created_at_returns_none(self):
        d = _library_row_to_dict(self._make_row(created_at=None))
        assert d["created_at"] is None

    def test_bpm_coerced_to_int(self):
        d = _library_row_to_dict(self._make_row(bpm=128))
        assert isinstance(d["bpm"], int)

    def test_none_bpm_returns_none(self):
        d = _library_row_to_dict(self._make_row(bpm=None))
        assert d["bpm"] is None

    def test_status_included(self):
        d = _library_row_to_dict(self._make_row(status="ready"))
        assert d["status"] == "ready"


# ─────────────────────────────────────────────────────────────────────────────
# get_compatible_keys — pure function, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCompatibleKeys:
    """Unit tests for the Camelot wheel compatibility function."""

    def test_typical_major_key_returns_four_codes(self):
        """8B should yield exactly 4 compatible codes."""
        result = get_compatible_keys("8B")
        assert len(result) == 4

    def test_typical_major_key_identity(self):
        """8B must include itself."""
        assert "8B" in get_compatible_keys("8B")

    def test_typical_major_key_minus_one(self):
        """8B → 7B (one step anti-clockwise)."""
        assert "7B" in get_compatible_keys("8B")

    def test_typical_major_key_plus_one(self):
        """8B → 9B (one step clockwise)."""
        assert "9B" in get_compatible_keys("8B")

    def test_typical_major_key_parallel(self):
        """8B → 8A (relative minor)."""
        assert "8A" in get_compatible_keys("8B")

    def test_typical_minor_key(self):
        """5A should return [5A, 4A, 6A, 5B]."""
        result = get_compatible_keys("5A")
        assert set(result) == {"5A", "4A", "6A", "5B"}

    def test_wrap_downward_12b(self):
        """12B wraps: -1 should be 11B, +1 should be 1B, parallel 12A."""
        result = get_compatible_keys("12B")
        assert "12B" in result
        assert "11B" in result
        assert "1B" in result   # 12 + 1 wraps to 1
        assert "12A" in result

    def test_wrap_upward_1a(self):
        """1A wraps: -1 should be 12A, +1 should be 2A, parallel 1B."""
        result = get_compatible_keys("1A")
        assert "1A" in result
        assert "12A" in result  # 1 - 1 wraps to 12
        assert "2A" in result
        assert "1B" in result

    def test_lowercase_mode_is_normalised(self):
        """Keys supplied as '8b' or '8a' should be treated case-insensitively."""
        lower_result = get_compatible_keys("8b")
        upper_result = get_compatible_keys("8B")
        assert set(lower_result) == set(upper_result)

    def test_invalid_code_empty_string_returns_empty(self):
        result = get_compatible_keys("")
        assert result == []

    def test_invalid_code_bad_mode_returns_empty(self):
        result = get_compatible_keys("8C")
        assert result == []

    def test_invalid_code_zero_number_returns_empty(self):
        result = get_compatible_keys("0A")
        assert result == []

    def test_invalid_code_thirteen_returns_empty(self):
        result = get_compatible_keys("13B")
        assert result == []

    def test_invalid_code_text_returns_empty(self):
        result = get_compatible_keys("not-a-key")
        assert result == []

    def test_no_duplicate_codes(self):
        """Each compatible code should appear only once."""
        for num in range(1, 13):
            for mode in ("A", "B"):
                code = f"{num}{mode}"
                result = get_compatible_keys(code)
                assert len(result) == len(set(result)), (
                    f"Duplicates in compatible keys for {code}: {result}"
                )

    def test_all_returned_codes_are_valid_camelot(self):
        """Every code returned must itself be a valid Camelot code."""
        from music_engine import _parse_camelot
        result = get_compatible_keys("6B")
        for code in result:
            assert _parse_camelot(code) is not None, f"Invalid code returned: {code}"


# ─────────────────────────────────────────────────────────────────────────────
# fetch_track — async, mocked DB
# ─────────────────────────────────────────────────────────────────────────────

def _make_track_record(
    id: int = 1,
    title: str = "Test Track",
    artist: str = "Test Artist",
    album: str = "Test Album",
    duration_sec: int = 180,
    source: str = "upload",
    energy: int = 5,
    vibe: str = "chill",
    mood_tags: list | None = None,
    genre_tags: list | None = None,
    bpm: int = 120,
    musical_key: str = "G major",
    camelot_code: str | None = "8B",
) -> dict:
    """Build a minimal fake asyncpg-style record for fetch_track tests."""
    return {
        "id": id,
        "title": title,
        "artist": artist,
        "album": album,
        "duration_sec": duration_sec,
        "source": source,
        "energy": energy,
        "vibe": vibe,
        "mood_tags": mood_tags or ["chill", "ambient"],
        "genre_tags": genre_tags or ["electronic"],
        "bpm": bpm,
        "musical_key": musical_key,
        "camelot_code": camelot_code,
    }


class TestFetchTrackBehavior:
    """Integration-style tests for fetch_track using mocked DB helpers."""

    def _patch_db(self, primary_row=None, fallback_row=None, recent_ids=None, user_prefs=None):
        """
        Return a context-manager stack that patches the DB helpers used by fetch_track.

        primary_row:  what _primary_query returns
        fallback_row: what _fallback_query returns (used if primary returns None)
        """
        from unittest.mock import patch, AsyncMock

        patches = []

        # _get_user_prefs
        prefs = user_prefs or {"exclude_vibes": []}
        patches.append(
            patch("music_engine._get_user_prefs", new=AsyncMock(return_value=prefs))
        )

        # _recent_track_ids
        patches.append(
            patch("music_engine._recent_track_ids", new=AsyncMock(return_value=recent_ids or []))
        )

        # _primary_query
        patches.append(
            patch("music_engine._primary_query", new=AsyncMock(return_value=primary_row))
        )

        # _fallback_query
        patches.append(
            patch("music_engine._fallback_query", new=AsyncMock(return_value=fallback_row))
        )

        # _vector_fallback_query — never call real Gemini in tests
        patches.append(
            patch("music_engine._vector_fallback_query", new=AsyncMock(return_value=None))
        )

        return patches

    @pytest.mark.asyncio
    async def test_fetch_track_without_camelot_returns_track(self):
        """Baseline: fetch_track works with no current_camelot (backward compat)."""
        row = _make_track_record()
        patches = self._patch_db(primary_row=row)
        for p in patches:
            p.start()
        try:
            result = await music_engine.fetch_track(
                mood_tags=["chill"],
                energy=5,
            )
            assert result["id"] == 1
            assert result.get("no_content") is not True
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_fetch_track_with_valid_camelot_calls_primary_with_compatible_keys(self):
        """When current_camelot is valid, _primary_query is called with compatible_keys."""
        row = _make_track_record(camelot_code="8B")
        primary_mock = AsyncMock(return_value=row)

        patches = self._patch_db(primary_row=row)
        # Replace the _primary_query patch with our own so we can inspect calls
        patches[2].stop() if hasattr(patches[2], "stop") else None

        with patch("music_engine._get_user_prefs", new=AsyncMock(return_value={"exclude_vibes": []})), \
             patch("music_engine._recent_track_ids", new=AsyncMock(return_value=[])), \
             patch("music_engine._primary_query", new=primary_mock), \
             patch("music_engine._fallback_query", new=AsyncMock(return_value=None)), \
             patch("music_engine._vector_fallback_query", new=AsyncMock(return_value=None)):

            result = await music_engine.fetch_track(
                mood_tags=["energetic"],
                energy=7,
                current_camelot="8B",
            )

        # _primary_query must have been called with compatible_keys containing "8B"
        call_kwargs = primary_mock.call_args.kwargs
        compat = call_kwargs.get("compatible_keys", [])
        assert "8B" in compat
        assert "7B" in compat
        assert "9B" in compat
        assert "8A" in compat

    @pytest.mark.asyncio
    async def test_fetch_track_with_invalid_camelot_disables_preference(self):
        """An unrecognised Camelot code should not crash — preference is just disabled."""
        row = _make_track_record()
        primary_mock = AsyncMock(return_value=row)

        with patch("music_engine._get_user_prefs", new=AsyncMock(return_value={"exclude_vibes": []})), \
             patch("music_engine._recent_track_ids", new=AsyncMock(return_value=[])), \
             patch("music_engine._primary_query", new=primary_mock), \
             patch("music_engine._fallback_query", new=AsyncMock(return_value=None)), \
             patch("music_engine._vector_fallback_query", new=AsyncMock(return_value=None)):

            result = await music_engine.fetch_track(
                mood_tags=["chill"],
                energy=4,
                current_camelot="INVALID",
            )

        assert result.get("no_content") is not True
        call_kwargs = primary_mock.call_args.kwargs
        # compatible_keys should be None when the code is invalid
        assert call_kwargs.get("compatible_keys") is None

    @pytest.mark.asyncio
    async def test_fetch_track_returns_sentinel_when_no_rows(self):
        """All query tiers returning None should produce the no-music sentinel."""
        with patch("music_engine._get_user_prefs", new=AsyncMock(return_value={"exclude_vibes": []})), \
             patch("music_engine._recent_track_ids", new=AsyncMock(return_value=[])), \
             patch("music_engine._primary_query", new=AsyncMock(return_value=None)), \
             patch("music_engine._fallback_query", new=AsyncMock(return_value=None)), \
             patch("music_engine._vector_fallback_query", new=AsyncMock(return_value=None)):

            result = await music_engine.fetch_track(
                mood_tags=["chill"],
                energy=5,
            )

        assert result["no_content"] is True
        assert result["id"] is None
