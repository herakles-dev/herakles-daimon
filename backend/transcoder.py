"""HLS transcoding pipeline for music tracks.

Converts uploaded audio files to HLS adaptive bitrate format using FFmpeg.
Produces two quality variants (128kbps AAC + 64kbps AAC) with a master playlist.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _strip_html(s: Optional[str]) -> Optional[str]:
    """Remove HTML tags from a string to prevent XSS via embedded track metadata."""
    if not s:
        return s
    return re.sub(r"<[^>]+>", "", s).strip() or None

MUSIC_ROOT = Path(os.getenv("MUSIC_STORAGE_PATH", "/music"))
UPLOADS_DIR = MUSIC_ROOT / "uploads"
HLS_DIR = MUSIC_ROOT / "hls"
CACHE_DIR = MUSIC_ROOT / "cache"
ARTWORK_DIR = MUSIC_ROOT / "artwork"

# Quality variants — ordered highest to lowest for master playlist
VARIANTS = [
    {"name": "128k", "bitrate": "128k", "bandwidth": 131072},
    {"name": "64k",  "bitrate": "64k",  "bandwidth": 65536},
]
HLS_SEGMENT_DURATION = 6  # seconds

ALLOWED_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".wma"}
ALLOWED_MIME_TYPES = {
    "audio/mpeg", "audio/flac", "audio/wav", "audio/ogg", "audio/mp4",
    "audio/aac", "audio/opus", "audio/x-ms-wma", "audio/x-flac",
    "audio/vnd.wave", "audio/wave", "audio/x-wav",
    # Zip archives — accepted by the /upload/zip endpoint only; the zip
    # endpoint does its own structural validation before extracting audio.
    "application/zip", "application/x-zip-compressed",
}
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE_MB", "200")) * 1024 * 1024
MAX_ZIP_SIZE = int(os.getenv("MAX_ZIP_SIZE_MB", "500")) * 1024 * 1024

# Audio file magic bytes — used to verify file content matches the claimed extension
MAGIC_BYTES = {
    ".mp3": [b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"ID3"],
    ".flac": [b"fLaC"],
    ".wav": [b"RIFF"],
    ".ogg": [b"OggS"],
    ".m4a": [b"\x00\x00\x00", b"ftyp"],  # MP4 container
    ".aac": [b"\xff\xf1", b"\xff\xf9"],   # ADTS headers
    ".opus": [b"OggS"],  # Opus in Ogg container
    ".wma": [b"\x30\x26\xb2\x75"],  # ASF header
}


def validate_magic_bytes(content: bytes, extension: str) -> bool:
    """Check if file content matches expected magic bytes for the extension."""
    if extension not in MAGIC_BYTES:
        return True  # No known magic bytes, allow
    header = content[:12]  # Read enough for all checks
    return any(header.startswith(magic) for magic in MAGIC_BYTES[extension])


async def ensure_dirs() -> None:
    """Create all storage directories if they don't exist."""
    for d in [UPLOADS_DIR, HLS_DIR, CACHE_DIR, ARTWORK_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def compute_file_hash(file_path: "str | Path") -> str:
    """Compute SHA-256 hash of a file for deduplication."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def _run_subprocess(cmd: list[str]) -> tuple[int, bytes, bytes]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def extract_metadata(file_path: "str | Path") -> dict:
    """Extract audio metadata using ffprobe.

    Returns a dict with duration_sec, bitrate, title, artist, album,
    genre, track_number, and year.  Missing fields are None.
    Returns an empty dict on probe failure.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(file_path),
    ]
    rc, stdout, stderr = await _run_subprocess(cmd)
    if rc != 0:
        logger.error("ffprobe failed for %s: %s", file_path, stderr.decode(errors="replace"))
        return {}

    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        logger.error("ffprobe output parse error for %s: %s", file_path, exc)
        return {}

    fmt = data.get("format", {})
    tags = fmt.get("tags", {})

    # ffprobe tags can be upper- or lower-case depending on container
    def tag(*keys: str) -> Optional[str]:
        for k in keys:
            v = tags.get(k) or tags.get(k.upper()) or tags.get(k.lower())
            if v:
                return v.strip() or None
        return None

    try:
        duration_sec = int(float(fmt.get("duration", 0)))
    except (TypeError, ValueError):
        duration_sec = 0

    try:
        bitrate = int(fmt.get("bit_rate", 0))
    except (TypeError, ValueError):
        bitrate = 0

    return {
        "duration_sec": duration_sec,
        "bitrate": bitrate,
        "title": _strip_html(tag("title")),
        "artist": _strip_html(tag("artist", "album_artist")),
        "album": _strip_html(tag("album")),
        "genre": _strip_html(tag("genre")),
        "track_number": _strip_html(tag("track")),
        "year": _strip_html(tag("date", "year")),
    }


async def extract_artwork(file_path: "str | Path", output_path: "str | Path") -> bool:
    """Extract embedded album art and convert to WebP (max 512x512).

    Returns True if a WebP file was successfully written, False otherwise.
    Cleans up any intermediate JPEG on both success and failure.
    """
    output_path = Path(output_path)
    jpg_path = output_path.with_suffix(".jpg")

    # Step 1: pull the cover image stream out of the container
    rc, _, stderr = await _run_subprocess([
        "ffmpeg", "-y", "-i", str(file_path),
        "-an", "-vcodec", "copy",
        "-f", "image2", str(jpg_path),
    ])

    if rc != 0 or not jpg_path.exists() or jpg_path.stat().st_size == 0:
        logger.debug("No embedded artwork in %s: %s", file_path, stderr.decode(errors="replace"))
        jpg_path.unlink(missing_ok=True)
        return False

    # Step 2: resize and convert to WebP
    rc2, _, stderr2 = await _run_subprocess([
        "ffmpeg", "-y", "-i", str(jpg_path),
        "-vf", "scale=512:512:force_original_aspect_ratio=decrease",
        str(output_path),
    ])

    jpg_path.unlink(missing_ok=True)

    if rc2 != 0 or not output_path.exists():
        logger.warning("Artwork WebP conversion failed for %s: %s", file_path, stderr2.decode(errors="replace"))
        output_path.unlink(missing_ok=True)
        return False

    return True


async def transcode_to_hls(file_path: "str | Path", track_id: int) -> Optional[str]:
    """Transcode an audio file to HLS adaptive bitrate format.

    Produces two AAC variants (128k and 64k) plus a master playlist.
    The output directory is cleaned up on any FFmpeg error so partial
    state is never left on disk.

    Returns the absolute path to the output directory (which contains
    master.m3u8) on success, or None on failure.
    """
    output_dir = HLS_DIR / str(track_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    for variant in VARIANTS:
        variant_m3u8 = output_dir / f"{variant['name']}.m3u8"
        segment_pattern = output_dir / f"{variant['name']}_%03d.aac"

        cmd = [
            "ffmpeg", "-y",
            "-fflags", "+genpts",   # regenerate PTS — fixes broken timestamps
            "-i", str(file_path),
            "-vn",                  # strip video/cover art (confuses HLS segmenter)
            # Audio codec
            "-c:a", "aac",
            "-b:a", variant["bitrate"],
            "-ac", "2",       # stereo output
            "-ar", "44100",   # standard sample rate
            # HLS muxer settings
            "-f", "hls",
            "-hls_time", str(HLS_SEGMENT_DURATION),
            "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(segment_pattern),
            str(variant_m3u8),
        ]

        rc, _, stderr = await _run_subprocess(cmd)
        if rc != 0:
            logger.error(
                "FFmpeg transcode failed for track %d variant %s: %s",
                track_id, variant["name"], stderr.decode(errors="replace"),
            )
            shutil.rmtree(output_dir, ignore_errors=True)
            return None

    # Build the master playlist
    master_path = output_dir / "master.m3u8"
    lines = ["#EXTM3U"]
    for variant in VARIANTS:
        lines.append(
            f'#EXT-X-STREAM-INF:BANDWIDTH={variant["bandwidth"]},'
            f'CODECS="mp4a.40.2"'
        )
        lines.append(f'{variant["name"]}.m3u8')
    master_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info("Transcoded track %d → %s", track_id, output_dir)
    return str(output_dir)


async def save_upload(
    file_content: bytes,
    filename: str,
    user_id: str = "default",
) -> tuple[str, str]:
    """Persist an uploaded audio file under a content-addressed path.

    Directory structure: uploads/<user_id>/<sha256>/original<ext>

    Returns (save_path, file_hash).
    """
    await ensure_dirs()

    file_hash = hashlib.sha256(file_content).hexdigest()
    ext = Path(filename).suffix.lower() or ".bin"
    user_dir = UPLOADS_DIR / user_id / file_hash
    user_dir.mkdir(parents=True, exist_ok=True)

    save_path = user_dir / f"original{ext}"
    save_path.write_bytes(file_content)

    logger.debug("Saved upload %s → %s (%d bytes)", filename, save_path, len(file_content))
    return str(save_path), file_hash


def validate_upload(filename: str, content_type: str, size: int, content: bytes = None) -> Optional[str]:
    """Validate an incoming upload before accepting the bytes.

    Returns an error message string if the upload is rejected, or None
    if the file is acceptable.  When ``content`` is provided, also
    checks that the file's magic bytes match the claimed extension.
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return (
            f"Unsupported format: {ext!r}. "
            f"Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if size > MAX_UPLOAD_SIZE:
        actual_mb = size // (1024 * 1024)
        limit_mb = MAX_UPLOAD_SIZE // (1024 * 1024)
        return f"File too large: {actual_mb} MB. Maximum: {limit_mb} MB"
    if content and not validate_magic_bytes(content, ext):
        return f"File content does not match {ext} format (magic bytes mismatch)"
    return None
