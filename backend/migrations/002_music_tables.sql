-- Migration 002: Music tables for Herakles Play
-- All changes ADDITIVE — existing tables untouched

-- Tracks table (parallel to videos)
CREATE TABLE IF NOT EXISTS tracks (
    id SERIAL PRIMARY KEY,
    url TEXT,
    source VARCHAR(20) NOT NULL DEFAULT 'upload' CHECK (source IN ('upload', 'jamendo', 'openverse', 'incompetech', 'ccmixter')),
    source_id VARCHAR(100),
    title VARCHAR(500) NOT NULL,
    artist VARCHAR(300),
    album VARCHAR(300),
    duration_sec INTEGER,
    file_path TEXT,
    hls_path TEXT,
    artwork_path TEXT,
    fingerprint TEXT,
    musicbrainz_id VARCHAR(36),
    file_hash VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'ready', 'error')),
    license VARCHAR(50),
    attribution TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_file_hash ON tracks(file_hash) WHERE file_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_source_id ON tracks(source, source_id) WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tracks_source ON tracks(source);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);

-- Track tags (parallel to video_tags, with audio-specific fields)
CREATE TABLE IF NOT EXISTS track_tags (
    id SERIAL PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    energy INTEGER CHECK (energy BETWEEN 1 AND 10),
    bpm INTEGER,
    musical_key VARCHAR(10),
    danceability INTEGER CHECK (danceability BETWEEN 1 AND 10),
    acousticness INTEGER CHECK (acousticness BETWEEN 1 AND 10),
    vibe VARCHAR(100),
    mood_tags TEXT[] DEFAULT '{}',
    genre_tags TEXT[] DEFAULT '{}',
    claude_summary TEXT,
    tagged_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(track_id)
);

CREATE INDEX IF NOT EXISTS idx_track_tags_mood ON track_tags USING GIN(mood_tags);
CREATE INDEX IF NOT EXISTS idx_track_tags_genre ON track_tags USING GIN(genre_tags);
CREATE INDEX IF NOT EXISTS idx_track_tags_energy ON track_tags(energy);
CREATE INDEX IF NOT EXISTS idx_track_tags_vibe ON track_tags(vibe);

-- Media embeddings (unified for video + track, extending pattern from video_embeddings)
CREATE TABLE IF NOT EXISTS media_embeddings (
    id SERIAL PRIMARY KEY,
    media_type VARCHAR(10) NOT NULL CHECK (media_type IN ('video', 'track')),
    video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
    track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
    embedding vector(768),
    model VARCHAR(100) DEFAULT 'gemini-embedding-001',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (
        (media_type = 'video' AND video_id IS NOT NULL AND track_id IS NULL) OR
        (media_type = 'track' AND track_id IS NOT NULL AND video_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_media_embeddings_track ON media_embeddings USING hnsw (embedding vector_cosine_ops) WHERE media_type = 'track';
CREATE INDEX IF NOT EXISTS idx_media_embeddings_video ON media_embeddings USING hnsw (embedding vector_cosine_ops) WHERE media_type = 'video';

-- Playlists
CREATE TABLE IF NOT EXISTS playlists (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL DEFAULT 'default',
    name VARCHAR(300) NOT NULL,
    description TEXT,
    auto_generated BOOLEAN DEFAULT FALSE,
    mood_tags TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_playlists_user ON playlists(user_id);

-- Playlist tracks (ordered)
CREATE TABLE IF NOT EXISTS playlist_tracks (
    id SERIAL PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(playlist_id, position)
);

-- Extend playback_log for music (additive columns)
ALTER TABLE playback_log ADD COLUMN IF NOT EXISTS content_type VARCHAR(10) DEFAULT 'video';
ALTER TABLE playback_log ADD COLUMN IF NOT EXISTS track_id INTEGER REFERENCES tracks(id);

-- Extend user_preferences for music (additive columns)
ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS preferred_genres TEXT[] DEFAULT '{}';
ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS audio_quality VARCHAR(10) DEFAULT '128k';
