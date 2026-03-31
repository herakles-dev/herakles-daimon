-- Migration 003: Schema remediation from adversarial review
-- All statements use IF NOT EXISTS / IF EXISTS for idempotency

-- F23: UNIQUE constraints on media_embeddings to prevent duplicate embeddings
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_embeddings_track_unique
    ON media_embeddings(track_id) WHERE track_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_embeddings_video_unique
    ON media_embeddings(video_id) WHERE video_id IS NOT NULL;

-- Fingerprint UNIQUE index for cross-source dedup
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_fingerprint_unique
    ON tracks(fingerprint) WHERE fingerprint IS NOT NULL;

-- Trigram indexes for ILIKE search performance
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_tracks_title_trgm ON tracks USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_trgm ON tracks USING GIN (artist gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tracks_album_trgm ON tracks USING GIN (album gin_trgm_ops);

-- Composite indexes for playback_log music queries
CREATE INDEX IF NOT EXISTS idx_playback_log_music
    ON playback_log(user_id, content_type, played_at DESC)
    WHERE content_type = 'track';
CREATE INDEX IF NOT EXISTS idx_playback_log_music_skip
    ON playback_log(user_id, content_type, skipped, played_at DESC)
    WHERE content_type = 'track' AND skipped = TRUE;

-- Composite index for tracks status+created_at (library default sort)
CREATE INDEX IF NOT EXISTS idx_tracks_status_created
    ON tracks(status, created_at DESC) WHERE status = 'ready';

-- Fingerprint index for pipeline dedup queries
CREATE INDEX IF NOT EXISTS idx_tracks_fingerprint
    ON tracks(fingerprint) WHERE fingerprint IS NOT NULL;

-- Constraint: ready tracks must have hls_path
-- Use DO block for idempotent ALTER TABLE ADD CONSTRAINT
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_ready_has_hls') THEN
        ALTER TABLE tracks ADD CONSTRAINT chk_ready_has_hls
            CHECK (status != 'ready' OR hls_path IS NOT NULL);
    END IF;
END$$;

-- Constraint: source_id must not be empty string (normalize to NULL)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_source_id_not_empty') THEN
        ALTER TABLE tracks ADD CONSTRAINT chk_source_id_not_empty
            CHECK (source_id IS NULL OR length(source_id) > 0);
    END IF;
END$$;
