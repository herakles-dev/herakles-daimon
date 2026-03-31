"""
Video Discovery — live YouTube search + channel scraping for Muse.

Two modes:
  1. SEARCH (query param): yt-dlp YouTube search — Muse constructs the query
     from conversation context. This is the primary mode.
  2. CHANNEL (channel_name/category): browse a specific subscribed channel.

No videos are downloaded — playback is via YouTube embed URL.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Optional

import yaml

from db import execute, fetch_all, fetch_one

# Proxy is optional — set SOCKS_PROXY env var to route through VPN
SOCKS_PROXY = os.environ.get("SOCKS_PROXY", "")

logger = logging.getLogger("play-backend.video-discovery")

# ---------------------------------------------------------------------------
# Channel registry (loaded from seeds/channels.yml)
# ---------------------------------------------------------------------------

_CHANNELS: list[dict] | None = None
_CATEGORIES: dict[str, list[dict]] | None = None


def _load_channels() -> list[dict]:
    """Load and cache the channel registry from channels.yml."""
    global _CHANNELS, _CATEGORIES
    if _CHANNELS is not None:
        return _CHANNELS

    seeds_path = Path(__file__).parent / "scraper" / "seeds" / "channels.yml"
    if not seeds_path.exists():
        logger.error("channels.yml not found at %s", seeds_path)
        _CHANNELS = []
        _CATEGORIES = {}
        return _CHANNELS

    with seeds_path.open() as f:
        data = yaml.safe_load(f) or {}

    _CHANNELS = data.get("channels", [])

    _CATEGORIES = {}
    for ch in _CHANNELS:
        cat = ch.get("category", "uncategorised")
        _CATEGORIES.setdefault(cat, []).append(ch)

    logger.info(
        "Loaded %d channels across %d categories",
        len(_CHANNELS), len(_CATEGORIES),
    )
    return _CHANNELS


def _get_categories() -> dict[str, list[dict]]:
    _load_channels()
    return _CATEGORIES or {}


def _find_channels(
    channel_name: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Find matching channels by name, tag, or category."""
    channels = _load_channels()

    if channel_name:
        name_lower = channel_name.lower()
        matches = [
            ch for ch in channels
            if name_lower in ch["name"].lower()
            or any(name_lower in t for t in ch.get("tags", []))
        ]
        if matches:
            return matches

    if category:
        cats = _get_categories()
        cat_lower = category.lower()
        for cat_name, cat_channels in cats.items():
            if cat_lower in cat_name.lower():
                return cat_channels

    return []


# ---------------------------------------------------------------------------
# yt-dlp: YouTube SEARCH (primary mode)
# ---------------------------------------------------------------------------

async def _search_youtube_api(
    query: str,
    max_results: int = 5,
) -> list[dict]:
    """Search YouTube via Data API v3 (OAuth). No proxy needed."""
    try:
        from broadcast.youtube_auth import get_credentials
        from googleapiclient.discovery import build

        creds = get_credentials()
        if not creds:
            return []

        loop = asyncio.get_event_loop()

        def _api_search():
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            resp = youtube.search().list(
                q=query,
                part="id,snippet",
                type="video",
                maxResults=max_results * 2,
                videoDuration="medium",  # 4-20 min; use "long" for 20+
                order="relevance",
                safeSearch="moderate",
            ).execute()

            # Also try long videos for mixes
            resp_long = youtube.search().list(
                q=query,
                part="id,snippet",
                type="video",
                maxResults=max_results,
                videoDuration="long",
                order="relevance",
                safeSearch="moderate",
            ).execute()

            all_items = resp.get("items", []) + resp_long.get("items", [])

            # Deduplicate by video ID
            seen = set()
            videos = []
            for item in all_items:
                vid_id = item["id"].get("videoId", "")
                if not vid_id or vid_id in seen:
                    continue
                seen.add(vid_id)
                snippet = item["snippet"]
                videos.append({
                    "id": vid_id,
                    "title": snippet.get("title", ""),
                    "channel": snippet.get("channelTitle", ""),
                    "duration": 0,  # API search doesn't return duration
                    "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                    "view_count": 0,
                    "description": snippet.get("description", "")[:500],
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                })
            return videos[:max_results]

        return await loop.run_in_executor(None, _api_search)

    except Exception as e:
        logger.warning("YouTube Data API search failed, will fall back to yt-dlp: %s", e)
        return []


async def _search_youtube(
    query: str,
    max_results: int = 5,
) -> list[dict]:
    """
    Search YouTube. Tries Data API first (no proxy needed), falls back to yt-dlp.
    Filters out shorts (< 60s).
    """
    # Primary: YouTube Data API (OAuth, no proxy)
    api_results = await _search_youtube_api(query, max_results)
    if api_results:
        logger.info("YouTube Data API returned %d results for '%s'", len(api_results), query)
        return api_results

    logger.info("Falling back to yt-dlp for '%s'", query)
    return await _search_youtube_ytdlp(query, max_results)


async def _search_youtube_ytdlp(
    query: str,
    max_results: int = 5,
) -> list[dict]:
    """
    FALLBACK: Search YouTube via yt-dlp ytsearch. Returns metadata only, no download.
    Filters out shorts (< 60s) and very long videos (> 45min).
    """
    import subprocess
    import sys
    import json

    search_term = f"ytsearch{max_results * 2}:{query}"  # fetch extra to filter

    args = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "--print", '{"id":"%(id)s","title":"%(title)s","channel":"%(channel)s","duration":%(duration)s,"thumbnail":"%(thumbnail)s","view_count":%(view_count)s,"description":"%(description).500s","url":"https://www.youtube.com/watch?v=%(id)s"}',
        "--no-warnings",
        "--quiet",
        search_term,
    ]
    # Only add proxy if configured (empty = direct connection)
    if SOCKS_PROXY:
        args.insert(3, "--proxy")
        args.insert(4, SOCKS_PROXY)

    loop = asyncio.get_event_loop()

    def _run():
        return subprocess.run(args, capture_output=True, text=True, timeout=30)

    try:
        result = await loop.run_in_executor(None, _run)
    except Exception as exc:
        logger.error("yt-dlp search failed for '%s': %s", query, exc)
        return []

    if result.returncode != 0 and not result.stdout.strip():
        logger.warning("yt-dlp search returned nothing for '%s': %s", query, result.stderr[:300])
        return []

    videos = []
    for line in result.stdout.strip().splitlines():
        try:
            v = json.loads(line)
            for field in ("duration", "view_count"):
                try:
                    v[field] = int(v[field])
                except (ValueError, TypeError):
                    v[field] = 0
            # Filter: skip shorts (< 60s)
            dur = v.get("duration", 0)
            if dur < 60:
                continue
            videos.append(v)
        except json.JSONDecodeError:
            continue

    return videos[:max_results]


# ---------------------------------------------------------------------------
# yt-dlp: channel browse (secondary mode)
# ---------------------------------------------------------------------------

async def _fetch_channel_videos(
    channel_url: str,
    max_videos: int = 5,
) -> list[dict]:
    """
    Run yt-dlp flat-playlist on a channel. Metadata only, no download.
    Filters out shorts (< 60s).
    """
    import subprocess
    import sys
    import json

    args = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "--playlist-end", str(max_videos * 2),
        "--print", '{"id":"%(id)s","title":"%(title)s","channel":"%(channel)s","duration":%(duration)s,"thumbnail":"%(thumbnail)s","view_count":%(view_count)s,"description":"%(description).500s","url":"https://www.youtube.com/watch?v=%(id)s"}',
        "--no-warnings",
        "--quiet",
        channel_url,
    ]
    if SOCKS_PROXY:
        args.insert(3, "--proxy")
        args.insert(4, SOCKS_PROXY)

    loop = asyncio.get_event_loop()

    def _run():
        return subprocess.run(args, capture_output=True, text=True, timeout=60)

    try:
        result = await loop.run_in_executor(None, _run)
    except Exception as exc:
        logger.error("yt-dlp failed for %s: %s", channel_url, exc)
        return []

    if result.returncode != 0 and not result.stdout.strip():
        logger.warning("yt-dlp returned no output for %s: %s", channel_url, result.stderr[:300])
        return []

    videos = []
    for line in result.stdout.strip().splitlines():
        try:
            v = json.loads(line)
            for field in ("duration", "view_count"):
                try:
                    v[field] = int(v[field])
                except (ValueError, TypeError):
                    v[field] = 0
            # Filter out shorts
            if v.get("duration", 0) < 60:
                continue
            videos.append(v)
        except json.JSONDecodeError:
            continue

    return videos[:max_videos]


# ---------------------------------------------------------------------------
# Quick-tag with Gemini Flash
# ---------------------------------------------------------------------------

async def _quick_tag(video: dict, api_key: str) -> dict | None:
    """Fast tag using Gemini Flash from title + description only."""
    from google import genai
    from google.genai import types as genai_types
    import json
    import re

    prompt = f"""Analyze this YouTube video and return JSON with ADHD-friendly tags.

TITLE: {video.get('title', '')}
CHANNEL: {video.get('channel', '')}
DURATION: {video.get('duration', 0)} seconds
DESCRIPTION: {video.get('description', '')[:500]}

Return ONLY valid JSON:
{{"pacing": <1-10>, "stimulation": <1-10>, "novelty": <1-10>, "vibe": "<hyphenated>", "mood_tags": ["<tag>", ...], "content_tags": ["<tag>", ...], "claude_summary": "<1-2 sentences>"}}"""

    try:
        client = genai.Client(api_key=api_key)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-2.5-flash",
                contents=genai_types.Content(
                    parts=[genai_types.Part(text=prompt)]
                ),
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=2048,
                    temperature=0.2,
                ),
            ),
        )
        raw = response.text or ""
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception as exc:
        logger.warning("Quick-tag failed for '%s': %s: %s", video.get("title", "")[:50], type(exc).__name__, exc)

    return None


def _safe_int(val, default=5):
    try:
        return max(1, min(10, int(val)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Store in DB
# ---------------------------------------------------------------------------

async def _store_video(video: dict, tags: dict | None = None) -> int | None:
    """Insert video into DB. Returns video_id or None if duplicate."""
    url = video.get("url", "")

    existing = await fetch_one("SELECT id FROM videos WHERE url = $1", url)
    if existing:
        return existing["id"]

    row = await fetch_one(
        """INSERT INTO videos (url, source, title, channel, description,
           duration_sec, thumbnail_url, transcript_hash)
           VALUES ($1, 'youtube', $2, $3, $4, $5, $6, NULL)
           ON CONFLICT (url) DO NOTHING
           RETURNING id""",
        url,
        video.get("title", ""),
        video.get("channel", ""),
        (video.get("description") or "")[:2000],
        video.get("duration", 0),
        video.get("thumbnail", ""),
    )

    if not row:
        existing = await fetch_one("SELECT id FROM videos WHERE url = $1", url)
        return existing["id"] if existing else None

    video_id = row["id"]

    # If tags provided (e.g. from CLI seeding), store them now
    if tags:
        await _store_video_tags(video_id, tags)

    return video_id


def _make_video_result(v: dict, video_id: int, tags: dict | None) -> dict:
    """Build a result dict from raw video + tags."""
    from main import _sanitize_for_gemini
    return {
        "id": video_id,
        "url": v["url"],
        "title": v.get("title", ""),
        "channel": _sanitize_for_gemini(v.get("channel", v.get("_channel_name", "")), max_len=100),
        "category": v.get("_category", ""),
        "duration_sec": v.get("duration", 0),
        "thumbnail": v.get("thumbnail", ""),
        "vibe": tags.get("vibe", "unknown") if tags else "untagged",
        "mood_tags": tags.get("mood_tags", []) if tags else [],
        "content_tags": tags.get("content_tags", []) if tags else [],
        "summary": tags.get("claude_summary", "") if tags else "",
        "pacing": _safe_int(tags.get("pacing")) if tags else None,
        "stimulation": _safe_int(tags.get("stimulation")) if tags else None,
        "novelty": _safe_int(tags.get("novelty")) if tags else None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def discover_videos(
    query: str | None = None,
    category: str | None = None,
    channel_name: str | None = None,
    max_results: int = 5,
) -> dict:
    """
    Discover YouTube videos. Muse is the brain — it constructs the query.

    Modes (in priority order):
      1. query     → YouTube search (primary — Muse decides what to search)
      2. channel   → browse a specific subscribed channel
      3. category  → browse channels in a category
      4. none      → error, Muse must provide at least a query
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    max_results = min(max_results, 10)

    # --- Mode 1: YouTube search (primary) ---
    if query:
        logger.info("discover_videos: searching YouTube for '%s'", query)
        raw_videos = await _search_youtube(query, max_results=max_results)

        if not raw_videos:
            return {
                "discovered": 0,
                "videos": [],
                "message": f"No results for '{query}'. Try a different search.",
            }

        return await _tag_store_return(raw_videos, api_key, max_results,
                                        source_desc=f"YouTube search: {query}")

    # --- Mode 2: specific channel ---
    if channel_name:
        targets = _find_channels(channel_name=channel_name)
        if not targets:
            return {
                "discovered": 0,
                "videos": [],
                "message": f"No subscribed channel matching '{channel_name}'.",
                "available_categories": list(_get_categories().keys()),
            }
        # Pick first matching channel
        ch = targets[0]
        logger.info("discover_videos: browsing channel '%s'", ch["name"])
        raw_videos = await _fetch_channel_videos(ch["url"], max_videos=max_results)
        return await _tag_store_return(raw_videos, api_key, max_results,
                                        source_desc=ch["name"])

    # --- Mode 3: category browse ---
    if category:
        targets = _find_channels(category=category)
        if not targets:
            return {
                "discovered": 0,
                "videos": [],
                "message": f"No category matching '{category}'.",
                "available_categories": list(_get_categories().keys()),
            }
        random.shuffle(targets)
        ch = targets[0]
        logger.info("discover_videos: browsing category '%s' → channel '%s'", category, ch["name"])
        raw_videos = await _fetch_channel_videos(ch["url"], max_videos=max_results)
        return await _tag_store_return(raw_videos, api_key, max_results,
                                        source_desc=f"{category} → {ch['name']}")

    # --- Mode 4: nothing provided ---
    return {
        "discovered": 0,
        "videos": [],
        "message": "Provide a search query, channel name, or category.",
        "available_categories": list(_get_categories().keys()),
    }


async def _tag_store_return(
    raw_videos: list[dict],
    api_key: str,
    max_results: int,
    source_desc: str,
) -> dict:
    """Store results immediately, return fast. Tag in background."""
    if not raw_videos:
        return {
            "discovered": 0,
            "videos": [],
            "message": f"No videos found from {source_desc}.",
        }

    # Deduplicate against DB
    fresh = []
    existing_results = []
    for v in raw_videos:
        row = await fetch_one(
            """SELECT v.id, v.url, v.title, v.channel, v.duration_sec,
                      vt.pacing, vt.stimulation, vt.novelty, vt.vibe,
                      vt.mood_tags, vt.content_tags, vt.claude_summary
               FROM videos v
               LEFT JOIN video_tags vt ON vt.video_id = v.id
               WHERE v.url = $1""",
            v["url"],
        )
        if row:
            existing_results.append(_video_row_to_dict(row))
        else:
            fresh.append(v)

    if not fresh:
        return {
            "discovered": 0,
            "already_known": len(existing_results),
            "videos": existing_results[:max_results],
            "message": f"Already have these from {source_desc}.",
        }

    # Store immediately WITHOUT tagging — return results to Muse fast.
    # Include full metadata so Muse (which IS Gemini) can pick intelligently.
    fresh = fresh[:max_results]
    stored = []
    for v in fresh:
        vid = await _store_video(v, None)
        if vid:
            dur = v.get("duration", 0)
            from main import _sanitize_for_gemini
            stored.append({
                "id": vid,
                "url": v["url"],
                "title": v.get("title", ""),
                "channel": _sanitize_for_gemini(v.get("channel", ""), max_len=100),
                "duration_sec": dur,
                "duration_human": f"{dur // 60}:{dur % 60:02d}" if dur else "unknown",
                "thumbnail": v.get("thumbnail", ""),
                "description": _sanitize_for_gemini(v.get("description", ""), max_len=400),
                "view_count": v.get("view_count", 0),
            })

    # Fire-and-forget: tag in background so future queries benefit
    if api_key and fresh:
        async def _background_tag():
            tag_sem = asyncio.Semaphore(3)
            for v in fresh:
                async with tag_sem:
                    try:
                        tags = await _quick_tag(v, api_key)
                        if tags:
                            vid_row = await fetch_one(
                                "SELECT id FROM videos WHERE url = $1", v["url"]
                            )
                            if vid_row:
                                await _store_video_tags(vid_row["id"], tags)
                    except Exception as exc:
                        logger.warning("Background tag failed: %s", exc)

        asyncio.create_task(_background_tag())

    all_results = stored + existing_results
    return {
        "discovered": len(stored),
        "videos": all_results[:max_results],
        "source": source_desc,
        "message": f"Found {len(stored)} new video(s) via {source_desc}.",
    }


async def _store_video_tags(video_id: int, tags: dict) -> None:
    """Write tags for an already-stored video."""
    mood_tags = tags.get("mood_tags", [])
    if isinstance(mood_tags, str):
        mood_tags = [mood_tags]
    content_tags = tags.get("content_tags", [])
    if isinstance(content_tags, str):
        content_tags = [content_tags]

    await execute(
        """INSERT INTO video_tags (video_id, pacing, stimulation, novelty,
           vibe, mood_tags, content_tags, claude_summary)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (video_id) DO UPDATE SET
             pacing = EXCLUDED.pacing, stimulation = EXCLUDED.stimulation,
             novelty = EXCLUDED.novelty, vibe = EXCLUDED.vibe,
             mood_tags = EXCLUDED.mood_tags, content_tags = EXCLUDED.content_tags,
             claude_summary = EXCLUDED.claude_summary, tagged_at = NOW()""",
        video_id,
        _safe_int(tags.get("pacing")),
        _safe_int(tags.get("stimulation")),
        _safe_int(tags.get("novelty")),
        str(tags.get("vibe", "unknown")),
        mood_tags,
        content_tags,
        str(tags.get("claude_summary", ""))[:500],
    )


async def list_channels(category: str | None = None) -> dict:
    """List available channels and categories."""
    channels = _load_channels()
    cats = _get_categories()

    if category:
        cat_lower = category.lower()
        for cat_name, cat_channels in cats.items():
            if cat_lower in cat_name.lower():
                return {
                    "category": cat_name,
                    "channels": [
                        {"name": ch["name"], "tags": ch.get("tags", [])}
                        for ch in cat_channels
                    ],
                    "count": len(cat_channels),
                }
        return {"error": f"Category '{category}' not found", "available": list(cats.keys())}

    return {
        "total_channels": len(channels),
        "categories": {
            cat: {"count": len(chs), "channels": [ch["name"] for ch in chs]}
            for cat, chs in cats.items()
        },
    }


def _video_row_to_dict(row) -> dict:
    """Convert DB row to response dict."""
    return {
        "id": int(row["id"]),
        "url": row["url"],
        "title": row["title"],
        "channel": row["channel"],
        "duration_sec": int(row["duration_sec"]) if row["duration_sec"] else None,
        "pacing": int(row["pacing"]) if row.get("pacing") else None,
        "stimulation": int(row["stimulation"]) if row.get("stimulation") else None,
        "novelty": int(row["novelty"]) if row.get("novelty") else None,
        "vibe": row.get("vibe"),
        "mood_tags": list(row["mood_tags"]) if row.get("mood_tags") else [],
        "content_tags": list(row["content_tags"]) if row.get("content_tags") else [],
        "summary": row.get("claude_summary"),
    }
