"""Tests for the HLS transcoding pipeline.

Covers validate_upload (all extension/size/MIME branches) and
compute_file_hash (determinism and content-sensitivity).
These are pure functions with no I/O side effects beyond filesystem
reads, so no DB or Docker is needed.
"""
import os
import sys
import pytest
from pathlib import Path

# Allow importing backend modules without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from transcoder import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_SIZE,
    compute_file_hash,
    validate_upload,
)


# ─────────────────────────────────────────────────────────────────────────────
# validate_upload
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateUpload:
    """Exhaustive branch tests for validate_upload."""

    # -- Happy paths -------------------------------------------------------

    def test_valid_mp3_returns_none(self):
        assert validate_upload("song.mp3", "audio/mpeg", 1024) is None

    def test_valid_flac_returns_none(self):
        assert validate_upload("song.flac", "audio/flac", 1024) is None

    def test_valid_wav_returns_none(self):
        assert validate_upload("recording.wav", "audio/wav", 1024) is None

    def test_valid_ogg_returns_none(self):
        assert validate_upload("track.ogg", "audio/ogg", 1024) is None

    def test_valid_m4a_returns_none(self):
        assert validate_upload("track.m4a", "audio/mp4", 1024) is None

    def test_valid_aac_returns_none(self):
        assert validate_upload("track.aac", "audio/aac", 1024) is None

    def test_valid_opus_returns_none(self):
        assert validate_upload("track.opus", "audio/opus", 1024) is None

    def test_valid_wma_returns_none(self):
        assert validate_upload("track.wma", "audio/x-ms-wma", 1024) is None

    def test_all_allowed_extensions_pass(self):
        """Every extension in ALLOWED_EXTENSIONS must be accepted."""
        for ext in ALLOWED_EXTENSIONS:
            result = validate_upload(f"file{ext}", "audio/mpeg", 1024)
            assert result is None, f"Expected None for extension {ext!r}, got {result!r}"

    def test_extension_case_insensitive(self):
        """Extension check is case-insensitive (.MP3 == .mp3)."""
        assert validate_upload("SONG.MP3", "audio/mpeg", 1024) is None

    def test_size_exactly_at_limit_passes(self):
        assert validate_upload("song.mp3", "audio/mpeg", MAX_UPLOAD_SIZE) is None

    def test_zero_size_passes(self):
        """Size=0 is used by the upload endpoint before reading bytes."""
        assert validate_upload("song.mp3", "audio/mpeg", 0) is None

    # -- Rejection paths ---------------------------------------------------

    def test_executable_rejected(self):
        err = validate_upload("evil.exe", "application/octet-stream", 1024)
        assert err is not None
        assert "Unsupported" in err
        assert ".exe" in err

    def test_video_file_rejected(self):
        err = validate_upload("video.mp4", "video/mp4", 1024)
        assert err is not None
        assert "Unsupported" in err

    def test_pdf_rejected(self):
        err = validate_upload("doc.pdf", "application/pdf", 1024)
        assert err is not None
        assert "Unsupported" in err

    def test_no_extension_rejected(self):
        err = validate_upload("audiofile", "audio/mpeg", 1024)
        assert err is not None
        assert "Unsupported" in err

    def test_file_too_large_rejected(self):
        err = validate_upload("song.mp3", "audio/mpeg", MAX_UPLOAD_SIZE + 1)
        assert err is not None
        assert "too large" in err.lower()

    def test_one_byte_over_limit_rejected(self):
        """Boundary: exactly one byte over the limit should fail."""
        err = validate_upload("song.mp3", "audio/mpeg", MAX_UPLOAD_SIZE + 1)
        assert err is not None

    def test_error_message_contains_max_mb(self):
        """Size error should mention the configured limit in MB."""
        limit_mb = MAX_UPLOAD_SIZE // (1024 * 1024)
        err = validate_upload("song.mp3", "audio/mpeg", MAX_UPLOAD_SIZE + 1)
        assert str(limit_mb) in err

    def test_rejected_extension_lists_accepted_formats(self):
        """The error for a bad extension should list accepted formats."""
        err = validate_upload("song.xyz", "audio/mpeg", 1024)
        assert err is not None
        # At least one known-good extension should appear in the error
        assert any(ext in err for ext in (".mp3", ".flac", ".wav"))

    def test_path_traversal_in_filename(self):
        """Filenames with directory separators should be rejected (no .mp3 ext)."""
        err = validate_upload("../../etc/passwd", "audio/mpeg", 1024)
        assert err is not None  # no valid extension


# ─────────────────────────────────────────────────────────────────────────────
# compute_file_hash
# ─────────────────────────────────────────────────────────────────────────────

class TestFileHash:
    """Tests for compute_file_hash SHA-256 helper."""

    def test_returns_64_hex_chars(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello music")
        h = compute_file_hash(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic_same_file(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello music")
        assert compute_file_hash(f) == compute_file_hash(f)

    def test_different_content_gives_different_hash(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"track one")
        f2.write_bytes(b"track two")
        assert compute_file_hash(f1) != compute_file_hash(f2)

    def test_empty_file_has_known_hash(self, tmp_path):
        """SHA-256 of empty input is well-known."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert compute_file_hash(f) == expected

    def test_identical_content_in_different_files(self, tmp_path):
        """Two files with identical bytes must produce the same hash."""
        content = b"deduplication test content"
        f1 = tmp_path / "copy1.bin"
        f2 = tmp_path / "copy2.bin"
        f1.write_bytes(content)
        f2.write_bytes(content)
        assert compute_file_hash(f1) == compute_file_hash(f2)

    def test_large_file_hashed_correctly(self, tmp_path):
        """File larger than the 8192-byte read buffer is handled correctly."""
        content = b"x" * (8192 * 4 + 7)  # crosses multiple chunk boundaries
        f = tmp_path / "large.bin"
        f.write_bytes(content)
        h = compute_file_hash(f)
        assert len(h) == 64

    def test_accepts_string_path(self, tmp_path):
        """compute_file_hash accepts both str and Path arguments."""
        f = tmp_path / "str_path.bin"
        f.write_bytes(b"string path test")
        assert len(compute_file_hash(str(f))) == 64
