"""
YouTube Comments — fetch top comments for a video via innertube API.

Routes through SOCKS_PROXY if configured.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("play-backend.youtube-comments")

# Proxy is optional — set SOCKS_PROXY env var to route through VPN
SOCKS_PROXY = os.environ.get("SOCKS_PROXY", "")

INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/next"
INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20250101.00.00",
        "hl": "en",
        "gl": "US",
    }
}


def _extract_video_id(url: str) -> str | None:
    """Extract video ID from various YouTube URL formats."""
    if not url:
        return None
    if "v=" in url:
        return url.split("v=")[-1].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[-1].split("?")[0]
    if "/embed/" in url:
        return url.split("/embed/")[-1].split("?")[0]
    # Maybe it's already just an ID
    if len(url) == 11 and url.isalnum():
        return url
    return None


def _find_comment_continuation(data: dict) -> str | None:
    """Dig through innertube response to find the comment section continuation token."""
    try:
        # Path: contents.twoColumnWatchNextResults.results.results.contents
        contents = (
            data.get("contents", {})
            .get("twoColumnWatchNextResults", {})
            .get("results", {})
            .get("results", {})
            .get("contents", [])
        )
        for item in contents:
            section = item.get("itemSectionRenderer", {})
            for sub in section.get("contents", []):
                cont = sub.get("continuationItemRenderer", {})
                endpoint = cont.get("continuationEndpoint", {})
                token = endpoint.get("continuationCommand", {}).get("token")
                if token:
                    return token
    except Exception:
        pass
    return None


def _extract_comments(data: dict) -> list[dict]:
    """Extract comments from innertube response (commentEntityPayload format)."""
    comments = []

    def _find_all(obj, key, depth=0):
        if depth > 12:
            return []
        results = []
        if isinstance(obj, dict):
            if key in obj:
                results.append(obj[key])
            for v in obj.values():
                results.extend(_find_all(v, key, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                results.extend(_find_all(item, key, depth + 1))
        return results

    payloads = _find_all(data, "commentEntityPayload")

    for p in payloads:
        props = p.get("properties", {})
        text = props.get("content", {}).get("content", "").strip()
        author = p.get("author", {}).get("displayName", "Anonymous")
        toolbar = p.get("toolbar", {})
        likes = toolbar.get("likeCountLiked", toolbar.get("likeCountNotliked", "0"))
        time_text = props.get("publishedTime", "")

        if text:
            from main import _sanitize_for_gemini
            comments.append({
                "author": author,
                "text": _sanitize_for_gemini(text, max_len=500),
                "likes": str(likes),
                "time": time_text,
            })

    return comments


async def get_comments(
    video_url: str,
    max_comments: int = 15,
) -> dict:
    """
    Fetch top comments for a YouTube video.

    Uses innertube API. Routes through SOCKS_PROXY if configured.
    Returns dict with comments list and metadata.
    """
    video_id = _extract_video_id(video_url)
    if not video_id:
        return {"error": "Could not extract video ID", "comments": []}

    loop = asyncio.get_event_loop()

    def _fetch():
        try:
            with httpx.Client(proxy=SOCKS_PROXY or None, timeout=15) as client:
                # Step 1: Get video page data with comment continuation token
                r = client.post(
                    INNERTUBE_URL,
                    json={"context": INNERTUBE_CONTEXT, "videoId": video_id},
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code != 200:
                    return {"error": f"innertube returned {r.status_code}", "comments": []}

                data = r.json()
                token = _find_comment_continuation(data)

                if not token:
                    return {"error": "No comment section found (comments may be disabled)", "comments": []}

                # Step 2: Fetch comments using continuation token
                r2 = client.post(
                    INNERTUBE_URL,
                    json={"context": INNERTUBE_CONTEXT, "continuation": token},
                    headers={"Content-Type": "application/json"},
                )
                if r2.status_code != 200:
                    return {"error": f"comment fetch returned {r2.status_code}", "comments": []}

                comments = _extract_comments(r2.json())
                return {
                    "video_id": video_id,
                    "comment_count": len(comments),
                    "comments": comments[:max_comments],
                }
        except httpx.ProxyError as e:
            logger.warning("Proxy error fetching comments: %s", e)
            return {"error": "Proxy unavailable", "comments": []}
        except Exception as e:
            logger.error("Failed to fetch comments for %s: %s", video_id, e)
            return {"error": str(e)[:100], "comments": []}

    return await loop.run_in_executor(None, _fetch)
