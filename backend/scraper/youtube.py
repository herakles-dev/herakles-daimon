"""
YouTube discovery + transcript fetching via yt-dlp.

Two public entry points:

    list_recent_videos(channel_url, max_videos) -> list[VideoMeta]
    fetch_transcript(video_url)                 -> str | None

Everything is async-friendly: the synchronous yt-dlp calls run inside
asyncio's default thread-pool executor so the event loop stays free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class VideoMeta:
    video_id: str
    url: str
    title: str
    channel_name: str
    channel_url: str
    duration_seconds: int
    thumbnail_url: str
    description: str
    upload_date: str          # YYYYMMDD string from yt-dlp
    view_count: int
    transcript: Optional[str] = None
    transcript_language: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers (sync — run in executor)
# ---------------------------------------------------------------------------

def _run_ytdlp(args: List[str]) -> subprocess.CompletedProcess:
    """Run yt-dlp as a subprocess and return the result."""
    cmd = [sys.executable, "-m", "yt_dlp"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _extract_channel_videos_sync(
    channel_url: str,
    max_videos: int,
    preferred_langs: List[str],
) -> List[VideoMeta]:
    """
    Synchronous inner implementation — runs inside a thread-pool executor.

    Uses yt-dlp's flat-playlist dump to enumerate recent uploads, then
    fetches per-video metadata (including subtitle info) in a single pass
    using --write-auto-sub + print-json.
    """
    # Step 1: list video IDs from the channel (flat, no download).
    list_args = [
        "--flat-playlist",
        "--playlist-end", str(max_videos),
        "--print", "%(id)s\t%(title)s\t%(duration)s\t%(thumbnail)s\t%(upload_date)s\t%(view_count)s",
        "--no-warnings",
        "--quiet",
        channel_url,
    ]

    result = _run_ytdlp(list_args)
    if result.returncode != 0 and not result.stdout.strip():
        logger.error(
            "yt-dlp channel listing failed for %s: %s", channel_url, result.stderr[:500]
        )
        return []

    videos: List[VideoMeta] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        video_id, title, duration_raw, thumbnail, upload_date, view_count_raw = parts[:6]

        try:
            duration = int(duration_raw) if duration_raw and duration_raw != "NA" else 0
        except ValueError:
            duration = 0

        try:
            view_count = int(view_count_raw) if view_count_raw and view_count_raw != "NA" else 0
        except ValueError:
            view_count = 0

        videos.append(
            VideoMeta(
                video_id=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=title,
                channel_name="",      # filled in next step
                channel_url=channel_url,
                duration_seconds=duration,
                thumbnail_url=thumbnail,
                description="",
                upload_date=upload_date or "",
                view_count=view_count,
            )
        )

    if not videos:
        return []

    # Step 2: enrich first video with channel name (applies to all).
    # Skip full metadata fetch for speed — we already have the essentials.
    # Channel name is derived from the channel URL handle.
    channel_handle = channel_url.rstrip("/").split("@")[-1] if "@" in channel_url else ""
    for v in videos:
        v.channel_name = channel_handle

    return videos


def _fetch_transcript_via_api(
    video_id: str,
    preferred_langs: List[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    Attempt transcript fetch via youtube-transcript-api.

    This library uses YouTube's timedtext JSON API, which requires no ffmpeg
    and is lighter than yt-dlp subtitle extraction.  Returns (text, lang) or
    raises so the caller can fall through to the yt-dlp path.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError:
        raise RuntimeError("youtube-transcript-api not installed")

    api = YouTubeTranscriptApi()

    # Try preferred languages first, then fall back to any available transcript.
    try:
        transcript_list = api.list(video_id)
    except Exception as exc:
        raise RuntimeError(f"youtube-transcript-api list failed: {exc}") from exc

    # Build ordered candidate list: preferred manual → preferred auto → any
    candidates = []
    for lang in preferred_langs:
        try:
            candidates.append(transcript_list.find_manually_created_transcript([lang]))
        except Exception:
            pass
    for lang in preferred_langs:
        try:
            candidates.append(transcript_list.find_generated_transcript([lang]))
        except Exception:
            pass
    if not candidates:
        # Last resort: first available transcript translated to English
        try:
            t = next(iter(transcript_list))
            if t.language_code not in preferred_langs:
                t = t.translate("en")
            candidates.append(t)
        except Exception:
            pass

    if not candidates:
        raise RuntimeError("No transcripts found via youtube-transcript-api")

    t = candidates[0]
    snippets = t.fetch()
    text = " ".join(s.text for s in snippets if s.text.strip())
    return text or None, t.language_code


def _fetch_transcript_sync(
    video_url: str,
    preferred_langs: List[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    Download auto-generated subtitles for a video and return (text, lang).

    Strategy (in order):
    1. youtube-transcript-api — lightweight timedtext JSON path, no ffmpeg needed.
    2. yt-dlp auto-subs — writes VTT file, needs ffmpeg for format conversion.
    3. yt-dlp manual subs — same as above but --write-sub instead of --write-auto-sub.

    Returns (None, None) when no transcript is available.
    """
    import os
    import tempfile

    # Parse video ID for the API path
    video_id: Optional[str] = None
    if "v=" in video_url:
        video_id = video_url.split("v=")[-1].split("&")[0]
    elif "youtu.be/" in video_url:
        video_id = video_url.split("youtu.be/")[-1].split("?")[0]

    # --- Strategy 1: youtube-transcript-api ---
    if video_id:
        try:
            text, lang = _fetch_transcript_via_api(video_id, preferred_langs)
            if text:
                logger.info(
                    "Transcript obtained via youtube-transcript-api for %s (lang=%s, %d chars)",
                    video_id, lang, len(text),
                )
                return text, lang
        except Exception as exc:
            logger.debug(
                "youtube-transcript-api failed for %s (%s) — falling back to yt-dlp",
                video_id, exc,
            )

    # --- Strategies 2 & 3: yt-dlp subtitle download ---
    lang_str = ",".join(preferred_langs)
    cookies_file = os.environ.get("YOUTUBE_COOKIES_FILE", "")

    def _build_ytdlp_args(auto: bool, tmpdir: str) -> List[str]:
        args = [
            "--skip-download",
            "--write-auto-sub" if auto else "--write-sub",
            "--sub-lang", lang_str,
            "--sub-format", "vtt",
            "--no-warnings",
            "--quiet",
            # Prefer clients that don't require a JS runtime
            "--extractor-args", "youtube:player_client=ios,android",
            "-o", os.path.join(tmpdir, "%(id)s.%(ext)s"),
        ]
        if cookies_file and os.path.isfile(cookies_file):
            args += ["--cookies", cookies_file]
        args.append(video_url)
        return args

    with tempfile.TemporaryDirectory() as tmpdir:
        # Auto-generated subtitles first
        result = _run_ytdlp(_build_ytdlp_args(auto=True, tmpdir=tmpdir))
        if result.returncode != 0 and result.stderr:
            logger.debug("yt-dlp auto-sub stderr for %s: %s", video_url, result.stderr[:400])

        sub_files = [
            f for f in os.listdir(tmpdir)
            if f.endswith(".vtt") or f.endswith(".srt")
        ]

        if not sub_files:
            # Manual captions fallback
            result2 = _run_ytdlp(_build_ytdlp_args(auto=False, tmpdir=tmpdir))
            if result2.returncode != 0 and result2.stderr:
                logger.debug("yt-dlp manual-sub stderr for %s: %s", video_url, result2.stderr[:400])
            sub_files = [
                f for f in os.listdir(tmpdir)
                if f.endswith(".vtt") or f.endswith(".srt")
            ]

        if not sub_files:
            # Log root cause so operators can act (e.g. provide cookies)
            combined_err = (result.stderr or "") + "\n" + (result2.stderr if "result2" in dir() else "")
            if "bot" in combined_err.lower() or "sign in" in combined_err.lower() or "429" in combined_err:
                logger.warning(
                    "YouTube rate-limited or blocked transcript request for %s. "
                    "Try again later or use a proxy.",
                    video_url,
                )
            return None, None

        sub_path = os.path.join(tmpdir, sub_files[0])
        with open(sub_path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()

        # Detect language from filename, e.g. "abc123.en.vtt"
        lang = None
        parts = sub_files[0].split(".")
        if len(parts) >= 3:
            lang = parts[-2]

        text = _vtt_to_plain(raw)
        return text or None, lang


def _vtt_to_plain(vtt_content: str) -> str:
    """
    Strip VTT/SRT timing lines and metadata, returning plain prose.

    Duplicate lines (common in auto-subs) are collapsed.
    """
    import re

    # Remove WEBVTT header
    lines = vtt_content.splitlines()
    seen: set = set()
    text_lines: List[str] = []

    # Regex for VTT timestamp lines: 00:00:00.000 --> 00:00:00.000
    timestamp_re = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
    tag_re = re.compile(r"<[^>]+>")

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if timestamp_re.match(line):
            continue
        if line.isdigit():
            continue

        # Strip VTT inline tags
        clean = tag_re.sub("", line).strip()
        if not clean or clean in seen:
            continue

        seen.add(clean)
        text_lines.append(clean)

    return " ".join(text_lines)


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def list_recent_videos(
    channel_url: str,
    max_videos: int = 10,
    preferred_langs: Optional[List[str]] = None,
    delay_min: float = 2.0,
    delay_max: float = 4.0,
) -> List[VideoMeta]:
    """
    Discover and return the most-recent `max_videos` from a channel.

    Runs synchronous yt-dlp logic in a thread-pool executor to keep the
    asyncio event loop free.
    """
    if preferred_langs is None:
        preferred_langs = ["en", "en-US", "en-GB"]

    loop = asyncio.get_event_loop()

    try:
        videos = await loop.run_in_executor(
            None,
            _extract_channel_videos_sync,
            channel_url,
            max_videos,
            preferred_langs,
        )
    except Exception as exc:
        logger.error("list_recent_videos failed for %s: %s", channel_url, exc)
        return []

    logger.info("Discovered %d videos from %s", len(videos), channel_url)

    # Polite delay after channel listing
    delay = random.uniform(delay_min, delay_max)
    await asyncio.sleep(delay)

    return videos


async def fetch_transcript(
    video: VideoMeta,
    preferred_langs: Optional[List[str]] = None,
    delay_min: float = 2.0,
    delay_max: float = 4.0,
) -> VideoMeta:
    """
    Fetch and attach a transcript to the VideoMeta in-place.

    Returns the same VideoMeta (mutated), so it can be used in map-style
    pipelines.
    """
    if preferred_langs is None:
        preferred_langs = ["en", "en-US", "en-GB"]

    loop = asyncio.get_event_loop()

    try:
        text, lang = await loop.run_in_executor(
            None,
            _fetch_transcript_sync,
            video.url,
            preferred_langs,
        )
        video.transcript = text
        video.transcript_language = lang
        if text:
            logger.info(
                "Transcript fetched for '%s' (%d chars, lang=%s)",
                video.title[:60],
                len(text),
                lang,
            )
        else:
            logger.warning("No transcript available for '%s'", video.title[:60])
    except Exception as exc:
        logger.error("fetch_transcript failed for %s: %s", video.url, exc)

    # Polite delay after per-video call
    delay = random.uniform(delay_min, delay_max)
    await asyncio.sleep(delay)

    return video


def deduplicate_videos(
    new_videos: List[VideoMeta],
    seen_urls: set,
) -> List[VideoMeta]:
    """
    Filter out videos whose URLs already exist in `seen_urls`.

    Updates `seen_urls` in-place with the URLs that pass the filter.
    """
    fresh: List[VideoMeta] = []
    for v in new_videos:
        if v.url not in seen_urls:
            seen_urls.add(v.url)
            fresh.append(v)
    return fresh
