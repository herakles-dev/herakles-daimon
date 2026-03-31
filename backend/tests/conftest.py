"""Shared test fixtures for Herakles Play backend tests."""
import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_track():
    return {
        "id": 1,
        "title": "Test Track",
        "artist": "Test Artist",
        "album": "Test Album",
        "duration_sec": 240,
        "source": "upload",
        "hls_url": "/api/tracks/1/stream.m3u8",
        "artwork_url": "/api/tracks/1/artwork",
        "energy": 5,
        "vibe": "chill-vibes",
        "mood_tags": ["chill", "ambient"],
        "genre_tags": ["electronic"],
    }


@pytest.fixture
def sample_jamendo_raw():
    """Raw Jamendo API response dict for use in normalisation tests."""
    return {
        "id": "12345",
        "name": "Test Song",
        "artist_name": "Test Artist",
        "album_name": "Test Album",
        "duration": 180,
        "audio": "https://example.com/audio.mp3",
        "audiodownload": "https://example.com/download.mp3",
        "image": "https://example.com/art.jpg",
        "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
        "musicinfo": {
            "tags": {
                "genres": ["jazz"],
                "instruments": ["piano"],
                "vartags": ["chill"],
            },
            "speed": "medium",
        },
        "stats": {"rate_listened_total": 1000},
    }


@pytest.fixture
def sample_openverse_raw():
    """Raw Openverse API response dict for use in normalisation tests."""
    return {
        "id": "abc-uuid-123",
        "title": "Ambient Dream",
        "creator": "SomeArtist",
        "url": "https://cdn.openverse.org/abc.mp3",
        "thumbnail": "https://cdn.openverse.org/abc.jpg",
        "duration": 180000,  # milliseconds
        "license": "by",
        "license_version": "4.0",
        "attribution": "Ambient Dream by SomeArtist CC BY 4.0",
        "source": "freesound",
        "tags": [{"name": "ambient"}, {"name": "chill"}, {"name": "drone"}],
    }
