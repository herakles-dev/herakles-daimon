"""Tests for music source API clients.

Covers:
- JamendoClient: instantiation, _normalize_track (pure function)
- OpenverseClient: instantiation, _normalize (pure function, ms→sec conversion)
- IncompetechLoader: instantiation, _parse_duration (pure static method),
  _normalize (pure function), search_by_feel (in-memory filtering)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from sources import JamendoClient, IncompetechLoader, OpenverseClient


# ─────────────────────────────────────────────────────────────────────────────
# JamendoClient
# ─────────────────────────────────────────────────────────────────────────────

class TestJamendoClientInstantiation:
    def test_client_id_stored(self):
        client = JamendoClient(client_id="test_key")
        assert client.client_id == "test_key"

    def test_no_http_client_on_init(self):
        """HTTP client is created lazily — should be None at construction."""
        client = JamendoClient(client_id="test_key")
        assert client._client is None

    def test_empty_client_id_allowed_with_warning(self):
        """Empty client_id is accepted (logs a warning but doesn't raise)."""
        client = JamendoClient(client_id="")
        assert client.client_id == ""


class TestJamendoNormalizeTrack:
    """_normalize_track converts a raw Jamendo dict to our internal format."""

    def _client(self):
        return JamendoClient(client_id="test")

    def test_source_is_jamendo(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["source"] == "jamendo"

    def test_source_id_is_string(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["source_id"] == "12345"
        assert isinstance(result["source_id"], str)

    def test_title_mapped(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["title"] == "Test Song"

    def test_artist_mapped(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["artist"] == "Test Artist"

    def test_album_mapped(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["album"] == "Test Album"

    def test_duration_seconds(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["duration_sec"] == 180

    def test_genre_tags_from_musicinfo(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert "jazz" in result["genre_tags"]

    def test_mood_tags_from_vartags(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert "chill" in result["mood_tags"]

    def test_play_count_mapped(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["play_count"] == 1000

    def test_audio_url_mapped(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["audio_url"] == "https://example.com/audio.mp3"

    def test_download_url_mapped(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["download_url"] == "https://example.com/download.mp3"

    def test_artwork_url_mapped(self, sample_jamendo_raw):
        result = self._client()._normalize_track(sample_jamendo_raw)
        assert result["artwork_url"] == "https://example.com/art.jpg"

    def test_missing_musicinfo_handled_gracefully(self):
        raw = {
            "id": "999",
            "name": "Bare Track",
            "artist_name": "Bare Artist",
            "duration": 60,
        }
        result = self._client()._normalize_track(raw)
        assert result["source"] == "jamendo"
        assert result["genre_tags"] == []
        assert result["mood_tags"] == []

    def test_missing_stats_gives_zero_play_count(self):
        raw = {"id": "1", "name": "No Stats", "artist_name": "Artist"}
        result = self._client()._normalize_track(raw)
        assert result["play_count"] == 0

    def test_guaranteed_keys_present(self, sample_jamendo_raw):
        """All keys must be present regardless of input completeness."""
        result = self._client()._normalize_track(sample_jamendo_raw)
        for key in (
            "source", "source_id", "title", "artist", "album",
            "duration_sec", "audio_url", "download_url", "artwork_url",
            "license", "genre_tags", "mood_tags", "instrument_tags",
            "all_tags", "speed", "play_count",
        ):
            assert key in result, f"Key '{key}' missing from normalised Jamendo track"


# ─────────────────────────────────────────────────────────────────────────────
# OpenverseClient
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenverseClientInstantiation:
    def test_instantiation_no_token(self):
        client = OpenverseClient()
        assert client is not None

    def test_access_token_stored(self):
        client = OpenverseClient(access_token="tok123")
        assert client._access_token == "tok123"

    def test_no_http_client_on_init(self):
        client = OpenverseClient()
        assert client._client is None


class TestOpenverseNormalize:
    """_normalize converts an Openverse API dict to our internal format."""

    def _client(self):
        return OpenverseClient()

    def test_source_is_openverse(self, sample_openverse_raw):
        result = self._client()._normalize(sample_openverse_raw)
        assert result["source"] == "openverse"

    def test_source_id_mapped(self, sample_openverse_raw):
        result = self._client()._normalize(sample_openverse_raw)
        assert result["source_id"] == "abc-uuid-123"

    def test_title_mapped(self, sample_openverse_raw):
        result = self._client()._normalize(sample_openverse_raw)
        assert result["title"] == "Ambient Dream"

    def test_artist_mapped_from_creator(self, sample_openverse_raw):
        result = self._client()._normalize(sample_openverse_raw)
        assert result["artist"] == "SomeArtist"

    def test_duration_converted_from_ms(self, sample_openverse_raw):
        """180000 ms → 180 seconds."""
        result = self._client()._normalize(sample_openverse_raw)
        assert result["duration_sec"] == 180

    def test_tags_become_mood_tags(self, sample_openverse_raw):
        result = self._client()._normalize(sample_openverse_raw)
        assert "ambient" in result["mood_tags"]
        assert "chill" in result["mood_tags"]

    def test_genre_tags_always_empty_list(self, sample_openverse_raw):
        """Openverse has no structured genre field — genre_tags is always []."""
        result = self._client()._normalize(sample_openverse_raw)
        assert result["genre_tags"] == []

    def test_upstream_source_preserved(self, sample_openverse_raw):
        result = self._client()._normalize(sample_openverse_raw)
        assert result["upstream_source"] == "freesound"

    def test_none_duration_becomes_none(self):
        raw = {"id": "x", "title": "No Duration"}
        result = self._client()._normalize(raw)
        assert result["duration_sec"] is None

    def test_zero_duration_becomes_zero_seconds(self):
        raw = {"id": "x", "title": "Zero Duration", "duration": 0}
        result = self._client()._normalize(raw)
        assert result["duration_sec"] == 0

    def test_empty_tags_list_gives_empty_mood_tags(self):
        raw = {"id": "x", "title": "No Tags", "tags": []}
        result = self._client()._normalize(raw)
        assert result["mood_tags"] == []

    def test_malformed_tag_entries_ignored(self):
        """Tags that are not dicts with a 'name' key are silently skipped."""
        raw = {"id": "x", "title": "Bad Tags", "tags": [None, "string", {"name": "valid"}]}
        result = self._client()._normalize(raw)
        assert result["mood_tags"] == ["valid"]

    def test_guaranteed_keys_present(self, sample_openverse_raw):
        result = self._client()._normalize(sample_openverse_raw)
        for key in (
            "source", "source_id", "title", "artist", "album",
            "duration_sec", "audio_url", "download_url", "artwork_url",
            "license", "genre_tags", "mood_tags", "upstream_source",
        ):
            assert key in result, f"Key '{key}' missing from normalised Openverse track"


# ─────────────────────────────────────────────────────────────────────────────
# IncompetechLoader
# ─────────────────────────────────────────────────────────────────────────────

class TestIncompetechLoaderInstantiation:
    def test_instantiation(self):
        loader = IncompetechLoader()
        assert loader is not None

    def test_catalog_none_on_init(self):
        loader = IncompetechLoader()
        assert loader._catalog is None

    def test_http_client_none_on_init(self):
        loader = IncompetechLoader()
        assert loader._client is None


class TestIncompetechParseDuration:
    """_parse_duration: coerces duration strings to integer seconds."""

    def test_mm_ss_format(self):
        assert IncompetechLoader._parse_duration("3:45") == 225

    def test_h_mm_ss_format(self):
        assert IncompetechLoader._parse_duration("1:00:00") == 3600

    def test_h_mm_ss_non_zero_minutes(self):
        assert IncompetechLoader._parse_duration("1:02:30") == 3750

    def test_zero_minutes_thirty_seconds(self):
        assert IncompetechLoader._parse_duration("0:30") == 30

    def test_single_part_integer(self):
        assert IncompetechLoader._parse_duration("90") == 90

    def test_empty_string_returns_zero(self):
        assert IncompetechLoader._parse_duration("") == 0

    def test_non_string_returns_zero(self):
        assert IncompetechLoader._parse_duration(None) == 0  # type: ignore[arg-type]

    def test_invalid_string_returns_zero(self):
        assert IncompetechLoader._parse_duration("invalid") == 0

    def test_partial_invalid_returns_zero(self):
        assert IncompetechLoader._parse_duration("3:xx") == 0

    def test_leading_trailing_whitespace_handled(self):
        assert IncompetechLoader._parse_duration("  2:30  ") == 150


class TestIncompetechNormalize:
    """_normalize: converts a raw catalog entry to internal track format."""

    def _loader(self):
        return IncompetechLoader()

    def _raw(self, **overrides):
        base = {
            "name": "Chill Tune",
            "genre": "Ambient, Electronic",
            "feel": "Calm",
            "length": "4:00",
            "mp3": "https://incompetech.com/music/chill.mp3",
            "tempo": "Slow",
        }
        base.update(overrides)
        return base

    def test_source_is_incompetech(self):
        result = self._loader()._normalize(self._raw())
        assert result["source"] == "incompetech"

    def test_artist_is_kevin_macleod(self):
        result = self._loader()._normalize(self._raw())
        assert result["artist"] == "Kevin MacLeod"

    def test_license_is_cc_by_40(self):
        result = self._loader()._normalize(self._raw())
        assert result["license"] == "CC BY 4.0"

    def test_duration_parsed_from_length(self):
        result = self._loader()._normalize(self._raw(length="4:00"))
        assert result["duration_sec"] == 240

    def test_genre_tags_split_by_comma(self):
        result = self._loader()._normalize(self._raw(genre="Ambient, Electronic"))
        assert "ambient" in result["genre_tags"]
        assert "electronic" in result["genre_tags"]

    def test_feel_calm_maps_to_calm_mood_tags(self):
        result = self._loader()._normalize(self._raw(feel="Calm"))
        assert "calm" in result["mood_tags"] or "peaceful" in result["mood_tags"]

    def test_feel_dark_maps_to_dark_mood_tags(self):
        result = self._loader()._normalize(self._raw(feel="Dark"))
        assert "dark" in result["mood_tags"]

    def test_feel_happy_maps_to_happy_mood_tags(self):
        result = self._loader()._normalize(self._raw(feel="Happy"))
        assert "happy" in result["mood_tags"]

    def test_unrecognised_feel_becomes_verbatim_mood_tag(self):
        result = self._loader()._normalize(self._raw(feel="Quirky"))
        assert "quirky" in result["mood_tags"]

    def test_empty_feel_gives_empty_mood_tags(self):
        result = self._loader()._normalize(self._raw(feel=""))
        assert result["mood_tags"] == []

    def test_source_id_is_slug_of_title(self):
        result = self._loader()._normalize(self._raw(name="Chill Tune"))
        assert result["source_id"] == "chill-tune"

    def test_attribution_contains_title(self):
        result = self._loader()._normalize(self._raw(name="Epic Journey"))
        assert "Epic Journey" in result["attribution"]

    def test_attribution_contains_incompetech_url(self):
        result = self._loader()._normalize(self._raw())
        assert "incompetech.com" in result["attribution"]

    def test_album_is_none(self):
        result = self._loader()._normalize(self._raw())
        assert result["album"] is None

    def test_guaranteed_keys_present(self):
        result = self._loader()._normalize(self._raw())
        for key in (
            "source", "source_id", "title", "artist", "album",
            "duration_sec", "audio_url", "download_url", "artwork_url",
            "license", "attribution", "feel", "genre_tags", "mood_tags", "tempo",
        ):
            assert key in result, f"Key '{key}' missing from normalised Incompetech track"
