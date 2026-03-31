"""
Scraper configuration — loads seed channels from YAML and exposes
runtime tunables as a single dataclass instance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

# ---------------------------------------------------------------------------
# Seed-channel schema
# ---------------------------------------------------------------------------

@dataclass
class ChannelConfig:
    name: str
    url: str
    category: str = "uncategorised"
    tags: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------

@dataclass
class ScraperConfig:
    # Behaviour
    max_videos_per_channel: int = 10
    """How many of the most-recent videos to fetch per channel run."""

    concurrency: int = 3
    """Max parallel video-processing coroutines."""

    request_delay_min: float = 2.0
    """Minimum seconds to wait between yt-dlp invocations."""

    request_delay_max: float = 4.0
    """Maximum seconds to wait (random jitter added on top of min)."""

    transcript_languages: List[str] = field(
        default_factory=lambda: ["en", "en-US", "en-GB"]
    )
    """Preferred subtitle/transcript language codes (in priority order)."""

    # Gemini (tagging + embeddings)
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))

    # Channels
    channels: List[ChannelConfig] = field(default_factory=list)

    # Paths
    seeds_file: Path = field(
        default_factory=lambda: Path(__file__).parent / "seeds" / "channels.yml"
    )

    @classmethod
    def load(cls, seeds_file: Optional[Path] = None) -> "ScraperConfig":
        """Load config from environment + seed YAML."""
        cfg = cls()

        if seeds_file is not None:
            cfg.seeds_file = Path(seeds_file)

        if cfg.seeds_file.exists():
            with cfg.seeds_file.open() as fh:
                data = yaml.safe_load(fh) or {}
            cfg.channels = [
                ChannelConfig(
                    name=ch["name"],
                    url=ch["url"],
                    category=ch.get("category", "uncategorised"),
                    tags=ch.get("tags", []),
                )
                for ch in data.get("channels", [])
            ]
        else:
            import logging
            logging.getLogger(__name__).warning(
                "Seeds file not found: %s — no channels loaded", cfg.seeds_file
            )

        return cfg


# Module-level default instance (lazy — callers can also construct their own)
DEFAULT_CONFIG: Optional[ScraperConfig] = None


def get_config() -> ScraperConfig:
    """Return (and cache) the default config singleton."""
    global DEFAULT_CONFIG
    if DEFAULT_CONFIG is None:
        DEFAULT_CONFIG = ScraperConfig.load()
    return DEFAULT_CONFIG
