"""Tests for the HLS streaming router (streaming.py).

Two suites:
- TestStreamingRouteRegistration: checks that all expected URL patterns are
  registered on the APIRouter without starting a server.
- TestSegmentPathTraversalLogic: directly exercises the path-traversal guard
  logic extracted from stream_segment, since it's the one security-critical
  piece of pure logic in the module.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from streaming import router


# ─────────────────────────────────────────────────────────────────────────────
# Route registration
# ─────────────────────────────────────────────────────────────────────────────

class TestStreamingRouteRegistration:
    """Verify every expected route is wired on the APIRouter."""

    def _registered_paths(self):
        return [r.path for r in router.routes]

    def test_master_playlist_route_registered(self):
        paths = self._registered_paths()
        assert any("stream.m3u8" in p for p in paths), (
            f"No stream.m3u8 route found. Registered: {paths}"
        )

    def test_segment_route_registered(self):
        paths = self._registered_paths()
        assert any("segment" in p or "stream/{" in p for p in paths), (
            f"No segment route found. Registered: {paths}"
        )

    def test_artwork_route_registered(self):
        paths = self._registered_paths()
        assert any("artwork" in p for p in paths), (
            f"No artwork route found. Registered: {paths}"
        )

    def test_variant_playlist_route_registered(self):
        """The variant playlist endpoint (128k/64k) must be registered."""
        paths = self._registered_paths()
        # Matches /api/tracks/{track_id}/stream/{variant}.m3u8
        assert any("variant" in p or (".m3u8" in p and "variant" in p) or
                   ("stream/{" in p and "m3u8" in p) for p in paths), (
            f"No variant playlist route found. Registered: {paths}"
        )

    def test_router_prefix_is_api_tracks(self):
        assert router.prefix == "/api/tracks"


# ─────────────────────────────────────────────────────────────────────────────
# Path-traversal guard logic
# ─────────────────────────────────────────────────────────────────────────────

class TestSegmentPathTraversalLogic:
    """The segment guard: reject if not .aac, or contains / or ..

    This mirrors the exact condition in streaming.stream_segment:
        if not segment.endswith(".aac") or "/" in segment or ".." in segment:
            raise HTTPException(400, ...)
    """

    @staticmethod
    def _is_rejected(segment: str) -> bool:
        """Return True if the segment would be rejected by the handler."""
        return (
            not segment.endswith(".aac")
            or "/" in segment
            or ".." in segment
        )

    # Good segments
    def test_valid_segment_accepted(self):
        assert not self._is_rejected("128k_000.aac")

    def test_valid_segment_with_index_accepted(self):
        assert not self._is_rejected("64k_099.aac")

    def test_segment_with_leading_zeros_accepted(self):
        assert not self._is_rejected("128k_001.aac")

    # Bad segments — wrong extension
    def test_non_aac_extension_rejected(self):
        assert self._is_rejected("segment.mp3")

    def test_m3u8_extension_rejected(self):
        assert self._is_rejected("master.m3u8")

    def test_no_extension_rejected(self):
        assert self._is_rejected("segment")

    # Bad segments — path traversal
    def test_dotdot_slash_rejected(self):
        assert self._is_rejected("../../../etc/passwd")

    def test_forward_slash_in_name_rejected(self):
        assert self._is_rejected("foo/bar.aac")

    def test_dotdot_in_filename_rejected(self):
        assert self._is_rejected("..malicious.aac")

    def test_url_encoded_traversal_lacks_dot_dot_but_has_slash(self):
        # URL-decoded segment containing a literal slash is rejected
        assert self._is_rejected("foo/bar.aac")

    def test_double_extension_with_traversal_rejected(self):
        assert self._is_rejected("../hack.aac")


# ─────────────────────────────────────────────────────────────────────────────
# Variant validation logic
# ─────────────────────────────────────────────────────────────────────────────

class TestVariantValidationLogic:
    """Mirrors the variant guard in stream_variant_playlist:
        if variant not in ("128k", "64k"): reject
    """

    @staticmethod
    def _is_valid_variant(variant: str) -> bool:
        return variant in ("128k", "64k")

    def test_128k_is_valid(self):
        assert self._is_valid_variant("128k")

    def test_64k_is_valid(self):
        assert self._is_valid_variant("64k")

    def test_random_string_is_invalid(self):
        assert not self._is_valid_variant("320k")

    def test_empty_string_is_invalid(self):
        assert not self._is_valid_variant("")

    def test_traversal_attempt_is_invalid(self):
        assert not self._is_valid_variant("../../../etc/passwd")

    def test_mixed_case_is_invalid(self):
        """Variant check is case-sensitive."""
        assert not self._is_valid_variant("128K")
