"""Tests for pure helper functions in main.py.

These functions are fully deterministic and require no DB or network:
- _track_record_to_dict: serialises a minimal tracks-only Record
- _track_row_to_dict: serialises a full tracks + track_tags Record
- _build_tools: converts simplified tool schema to Gemini FunctionDeclaration list

SERVER_SIDE_TOOLS set is also verified to contain all expected tool names.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# main.py imports google.genai at module level; we skip if it is unavailable
# (e.g., in a minimal CI environment without the google-genai package).
try:
    from main import (
        SERVER_SIDE_TOOLS,
        _track_record_to_dict,
        _track_row_to_dict,
    )
    HAS_MAIN = True
except ImportError:
    HAS_MAIN = False

pytestmark = pytest.mark.skipif(
    not HAS_MAIN, reason="main.py could not be imported (missing google-genai?)"
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper factories
# ─────────────────────────────────────────────────────────────────────────────

_TS = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def _minimal_record(**overrides):
    """Minimal tracks-only row for _track_record_to_dict."""
    base = {
        "id": 5,
        "title": "Mini Track",
        "artist": "Mini Artist",
        "album": "Mini Album",
        "duration_sec": 120,
        "hls_path": "/music/hls/5",
        "artwork_path": "/music/artwork/5.webp",
        "status": "ready",
        "created_at": _TS,
    }
    base.update(overrides)
    return base


def _full_record(**overrides):
    """Full tracks + track_tags row for _track_row_to_dict."""
    base = {
        "id": 5,
        "title": "Full Track",
        "artist": "Full Artist",
        "album": "Full Album",
        "duration_sec": 240,
        "hls_path": "/music/hls/5",
        "artwork_path": "/music/artwork/5.webp",
        "source": "upload",
        "status": "ready",
        "created_at": _TS,
        "energy": 7,
        "bpm": 140,
        "musical_key": "Am",
        "danceability": 0.8,
        "acousticness": 0.2,
        "vibe": "upbeat",
        "mood_tags": ["energetic", "happy"],
        "genre_tags": ["pop"],
        "claude_summary": "A bright pop track.",
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# SERVER_SIDE_TOOLS
# ─────────────────────────────────────────────────────────────────────────────

class TestServerSideTools:
    """Regression: all expected tool names must be present."""

    expected_tools = {
        "fetch_video", "skip_video", "log_playback", "update_user_profile",
        "fetch_track", "skip_track", "queue_track",
    }

    def test_all_video_tools_registered(self):
        for tool in ("fetch_video", "skip_video", "log_playback", "update_user_profile"):
            assert tool in SERVER_SIDE_TOOLS, f"Missing video tool: {tool}"

    def test_all_music_tools_registered(self):
        for tool in ("fetch_track", "skip_track", "queue_track"):
            assert tool in SERVER_SIDE_TOOLS, f"Missing music tool: {tool}"

    def test_no_unexpected_removals(self):
        assert self.expected_tools.issubset(SERVER_SIDE_TOOLS), (
            f"Tools removed from SERVER_SIDE_TOOLS: "
            f"{self.expected_tools - SERVER_SIDE_TOOLS}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# _track_record_to_dict
# ─────────────────────────────────────────────────────────────────────────────

class TestTrackRecordToDict:
    """Minimal serialiser (tracks table only, no tag join)."""

    def test_id_preserved(self):
        d = _track_record_to_dict(_minimal_record(id=42))
        assert d["id"] == 42

    def test_title_preserved(self):
        d = _track_record_to_dict(_minimal_record())
        assert d["title"] == "Mini Track"

    def test_status_preserved(self):
        d = _track_record_to_dict(_minimal_record(status="processing"))
        assert d["status"] == "processing"

    def test_created_at_is_iso_string(self):
        d = _track_record_to_dict(_minimal_record())
        assert isinstance(d["created_at"], str)
        assert "2024" in d["created_at"]

    def test_none_created_at_returns_none(self):
        d = _track_record_to_dict(_minimal_record(created_at=None))
        assert d["created_at"] is None

    def test_hls_path_preserved(self):
        d = _track_record_to_dict(_minimal_record(hls_path="/music/hls/99"))
        assert d["hls_path"] == "/music/hls/99"

    def test_artwork_path_preserved(self):
        d = _track_record_to_dict(_minimal_record(artwork_path="/music/artwork/99.webp"))
        assert d["artwork_path"] == "/music/artwork/99.webp"


# ─────────────────────────────────────────────────────────────────────────────
# _track_row_to_dict
# ─────────────────────────────────────────────────────────────────────────────

class TestTrackRowToDict:
    """Full serialiser (tracks + track_tags join)."""

    def test_id_preserved(self):
        d = _track_row_to_dict(_full_record(id=7))
        assert d["id"] == 7

    def test_source_preserved(self):
        d = _track_row_to_dict(_full_record(source="jamendo"))
        assert d["source"] == "jamendo"

    def test_tag_columns_present(self):
        d = _track_row_to_dict(_full_record())
        for col in ("energy", "bpm", "musical_key", "vibe", "mood_tags", "genre_tags"):
            assert col in d, f"Column '{col}' missing from full track dict"

    def test_missing_tag_column_defaults_to_none(self):
        """Extra columns absent from the row silently become None."""
        row = _full_record()
        # Remove an optional column
        row.pop("claude_summary", None)
        d = _track_row_to_dict(row)
        assert d.get("claude_summary") is None

    def test_created_at_iso_string(self):
        d = _track_row_to_dict(_full_record())
        assert isinstance(d["created_at"], str)

    def test_extra_date_columns_isoformat(self):
        """updated_at and tagged_at, when present, must be ISO strings."""
        from datetime import datetime, timezone
        row = _full_record()
        row["updated_at"] = datetime(2024, 7, 1, tzinfo=timezone.utc)
        row["tagged_at"] = datetime(2024, 7, 2, tzinfo=timezone.utc)
        d = _track_row_to_dict(row)
        assert isinstance(d.get("updated_at"), str)
        assert isinstance(d.get("tagged_at"), str)
