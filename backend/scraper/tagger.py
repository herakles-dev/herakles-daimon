"""
Gemini-powered ADHD tag generation.

Takes a VideoMeta (with optional transcript) and asks Gemini Flash to return a
structured JSON object with ADHD-specific metrics and content taxonomy.

The prompt is designed so that the model understands the ADHD viewer context
and produces consistent, parseable JSON on every call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from google import genai
from google.genai import types as genai_types

from .youtube import VideoMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclass
class ADHDTags:
    """Structured tags produced by AI for a single video."""

    # ADHD-specific numeric metrics (1-10 scale)
    pacing: int = 5
    """Cut speed and information delivery rate. 10 = hyper-fast (Fireship), 1 = slow lecture."""

    stimulation: int = 5
    """Audio/visual density and sensory load. 10 = music + graphics + cuts, 1 = talking head."""

    novelty: int = 5
    """How obscure or unexpected the content is. 10 = niche deep-dive, 1 = mainstream basics."""

    # Qualitative descriptors
    vibe: str = "unknown"
    """Single hyphenated descriptor, e.g. 'late-night-rabbit-hole', 'high-energy-tutorial'."""

    mood_tags: List[str] = field(default_factory=list)
    """How watching this feels: ['focused', 'analytical', 'high-energy', 'calming', ...]"""

    content_tags: List[str] = field(default_factory=list)
    """What the video is about: ['tech', 'server-hardware', 'linux', ...]"""

    claude_summary: str = ""
    """2-3 sentence plain-English summary."""

    # Metadata
    model_used: str = ""
    tokens_used: int = 0
    parse_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert content analyst specialising in ADHD-friendly media curation.
Your job is to analyse YouTube video metadata and transcripts, then output a precise JSON object
describing the video's ADHD relevance and content taxonomy.

ADHD METRIC DEFINITIONS
-----------------------
pacing (1-10):
  How fast information is delivered.  Consider cut frequency, speech rate, and information density.
  10 = extremely fast / frenetic (like a Fireship 100-seconds video)
   1 = very slow / deliberate (long lecture, meditative content)

stimulation (1-10):
  Total audio + visual sensory load.
  10 = constant music, fast cuts, animations, loud sound design
   1 = static talking head, no music, quiet

novelty (1-10):
  How unexpected or niche the content is.
  10 = deeply obscure or counterintuitive deep-dive
   1 = extremely common / beginner topic everyone knows

VIBE TAXONOMY (use the closest match or invent a new hyphenated label):
  late-night-rabbit-hole | high-energy-tutorial | background-ambiance |
  satisfying-build | comedy-engineering | science-explainer | travel-vlog |
  math-visual | dev-rant | wholesome-adventure | existential-wonder

OUTPUT FORMAT — respond ONLY with valid JSON, no markdown fences, no extra text:
{
  "pacing": <1-10 int>,
  "stimulation": <1-10 int>,
  "novelty": <1-10 int>,
  "vibe": "<hyphenated-string>",
  "mood_tags": ["<tag>", ...],
  "content_tags": ["<tag>", ...],
  "claude_summary": "<2-3 sentences>"
}"""


def _build_user_prompt(video: VideoMeta) -> str:
    """Build the per-video analysis prompt."""
    parts = [
        f"TITLE: {video.title}",
        f"CHANNEL: {video.channel_name}",
        f"DURATION: {video.duration_seconds} seconds",
        f"UPLOAD DATE: {video.upload_date}",
        f"VIEWS: {video.view_count:,}",
    ]

    if video.description:
        parts.append(f"\nDESCRIPTION (truncated):\n{video.description[:800]}")

    if video.transcript:
        transcript_excerpt = video.transcript[:3000]
        parts.append(f"\nTRANSCRIPT EXCERPT:\n{transcript_excerpt}")
    else:
        parts.append("\nTRANSCRIPT: not available — infer from title/description only")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> dict:
    """Extract the JSON object from the model response."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = cleaned.rstrip("`").strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {raw[:200]}")

    return json.loads(match.group(0))


def _safe_int(val, default: int = 5) -> int:
    """Coerce to int, clamp to [1, 10]."""
    try:
        return max(1, min(10, int(val)))
    except (TypeError, ValueError):
        return default


def _safe_list(val) -> List[str]:
    """Coerce to list of strings."""
    if isinstance(val, list):
        return [str(item) for item in val]
    if isinstance(val, str):
        return [val] if val else []
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class Tagger:
    """
    Wraps the Gemini client and exposes a single async method:
      tags = await tagger.tag_video(video)
    """

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash"):
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._client = genai.Client(api_key=self._api_key)
        self._model = model

    async def tag_video(self, video: VideoMeta) -> ADHDTags:
        """
        Call Gemini to generate ADHD tags for the video.

        On any error (API, parse, etc.) returns an ADHDTags with
        parse_error set so the pipeline can continue.
        """
        user_prompt = _build_user_prompt(video)

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model=self._model,
                    contents=genai_types.Content(
                        parts=[genai_types.Part(text=user_prompt)]
                    ),
                    config=genai_types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        max_output_tokens=2048,
                        temperature=0.3,
                    ),
                ),
            )
        except Exception as exc:
            logger.error("Gemini API error for '%s': %s", video.title[:60], exc)
            return ADHDTags(
                parse_error=f"API error: {exc}",
                model_used=self._model,
            )

        raw_text = response.text if response.text else ""
        tokens = 0
        if response.usage_metadata:
            tokens = (response.usage_metadata.prompt_token_count or 0) + (
                response.usage_metadata.candidates_token_count or 0
            )

        try:
            parsed = _parse_response(raw_text)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error(
                "Failed to parse Gemini response for '%s': %s\nRaw: %s",
                video.title[:60],
                exc,
                raw_text[:300],
            )
            return ADHDTags(
                parse_error=str(exc),
                model_used=self._model,
                tokens_used=tokens,
            )

        tags = ADHDTags(
            pacing=_safe_int(parsed.get("pacing")),
            stimulation=_safe_int(parsed.get("stimulation")),
            novelty=_safe_int(parsed.get("novelty")),
            vibe=str(parsed.get("vibe") or "unknown"),
            mood_tags=_safe_list(parsed.get("mood_tags")),
            content_tags=_safe_list(parsed.get("content_tags")),
            claude_summary=str(parsed.get("claude_summary") or ""),
            model_used=self._model,
            tokens_used=tokens,
        )

        logger.info(
            "Tagged '%s': pacing=%d stim=%d novelty=%d vibe=%s (%d tokens)",
            video.title[:50],
            tags.pacing,
            tags.stimulation,
            tags.novelty,
            tags.vibe,
            tokens,
        )

        return tags
