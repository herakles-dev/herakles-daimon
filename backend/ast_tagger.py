"""Audio Spectrogram Transformer (AST) auto-tagger for Herakles Play.

Uses MIT/ast-finetuned-audioset-10-10-0.4593 — a Vision Transformer fine-tuned on
AudioSet 527 classes — to classify audio and map predictions to genre, mood, and
instrument tags used by the content engine.

CPU-bound inference is offloaded to a thread executor; the model is lazy-loaded once
and cached behind a threading.Lock so concurrent async callers share one instance.

Sprint 11 — Audio Intelligence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("play-backend.ast-tagger")

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
_TARGET_SR = 16_000   # AST requires 16 kHz mono

# Directory where HuggingFace will cache downloaded model weights.
_MODEL_CACHE_DIR: str = os.getenv("MODEL_CACHE_DIR", "/models")

# ---------------------------------------------------------------------------
# AudioSet 527 → application tag mappings
# ---------------------------------------------------------------------------
# Keys are AudioSet label strings (as returned by the model's id2label dict).
# Values are (category, tag) where category is one of "genre", "mood", "instrument".
#
# Coverage goal: 80+ of the most music-relevant AudioSet labels.

_AUDIOSET_TAG_MAP: dict[str, tuple[str, str]] = {
    # ---- Genre ---------------------------------------------------------------
    "Hip hop music":          ("genre", "hip-hop"),
    "Hip-hop":                ("genre", "hip-hop"),
    "Rap music":              ("genre", "hip-hop"),
    "Electronic music":       ("genre", "electronic"),
    "Electronica":            ("genre", "electronic"),
    "Electronic dance music": ("genre", "edm"),
    "Jazz":                   ("genre", "jazz"),
    "Jazz guitar":            ("genre", "jazz"),
    "Jazz piano":             ("genre", "jazz"),
    "Rock music":             ("genre", "rock"),
    "Rock and roll":          ("genre", "rock"),
    "Indie rock":             ("genre", "indie"),
    "Alternative rock":       ("genre", "alternative"),
    "Classical music":        ("genre", "classical"),
    "Orchestra":              ("genre", "classical"),
    "Orchestral music":       ("genre", "classical"),
    "Reggae":                 ("genre", "reggae"),
    "Dancehall":              ("genre", "reggae"),
    "Blues":                  ("genre", "blues"),
    "Country":                ("genre", "country"),
    "Country music":          ("genre", "country"),
    "Bluegrass":              ("genre", "country"),
    "Punk rock":              ("genre", "punk"),
    "Punk":                   ("genre", "punk"),
    "Heavy metal":            ("genre", "metal"),
    "Metal":                  ("genre", "metal"),
    "Death metal":            ("genre", "metal"),
    "Black metal":            ("genre", "metal"),
    "Folk music":             ("genre", "folk"),
    "Soul music":             ("genre", "soul"),
    "Soul":                   ("genre", "soul"),
    "Funk":                   ("genre", "funk"),
    "Disco":                  ("genre", "disco"),
    "Techno":                 ("genre", "techno"),
    "Drum and bass":          ("genre", "dnb"),
    "House music":            ("genre", "house"),
    "Deep house":             ("genre", "house"),
    "Ambient music":          ("genre", "ambient"),
    "Drone":                  ("genre", "ambient"),
    "Pop music":              ("genre", "pop"),
    "Synth-pop":              ("genre", "synth-pop"),
    "R&B":                    ("genre", "rnb"),
    "Rhythm and blues":       ("genre", "rnb"),
    "Gospel":                 ("genre", "gospel"),
    "Trance music":           ("genre", "trance"),
    "Dubstep":                ("genre", "dubstep"),
    "Trap music":             ("genre", "trap"),
    "Grunge":                 ("genre", "grunge"),
    "Ska":                    ("genre", "ska"),
    "Latin music":            ("genre", "latin"),
    "Salsa music":            ("genre", "latin"),
    "Bossa nova":             ("genre", "bossa-nova"),
    "New-age music":          ("genre", "new-age"),
    "Opera":                  ("genre", "opera"),
    "Choral music":           ("genre", "choral"),
    "A capella":              ("genre", "acapella"),
    "Music for children":     ("genre", "children"),
    "Christian music":        ("genre", "christian"),
    "World music":            ("genre", "world"),
    "Flamenco":               ("genre", "flamenco"),
    # ---- Mood ----------------------------------------------------------------
    # High energy / intense
    "Drum and bass (mood)":          ("mood", "energetic"),
    "Techno (mood)":                 ("mood", "energetic"),
    "Heavy metal (mood)":            ("mood", "intense"),
    "Metal (mood)":                  ("mood", "intense"),
    "Death metal (mood)":            ("mood", "intense"),
    "Punk rock (mood)":              ("mood", "energetic"),
    "Hard rock":                     ("mood", "intense"),
    "Dubstep (mood)":                ("mood", "intense"),
    "Electronic dance music (mood)": ("mood", "energetic"),
    "Music of Africa":               ("mood", "energetic"),
    # Calm / relaxed / chill
    "Ambient music (mood)":          ("mood", "calm"),
    "Drone (mood)":                  ("mood", "calm"),
    "New-age music (mood)":          ("mood", "relaxed"),
    "Lullaby":                       ("mood", "calm"),
    "Choral music (mood)":           ("mood", "peaceful"),
    "Nature sounds":                 ("mood", "relaxed"),
    "Rain":                          ("mood", "chill"),
    # Upbeat / happy
    "Disco (mood)":                  ("mood", "upbeat"),
    "Funk (mood)":                   ("mood", "upbeat"),
    "Ska (mood)":                    ("mood", "upbeat"),
    "Music for children (mood)":     ("mood", "happy"),
    "Cheerful music":                ("mood", "happy"),
    "Happy music":                   ("mood", "happy"),
    # Sad / melancholic
    "Sad music":                     ("mood", "melancholic"),
    "Slow music":                    ("mood", "melancholic"),
    # Dark / moody
    "Dark music":                    ("mood", "dark"),
    "Scary music":                   ("mood", "dark"),
    "Tense music":                   ("mood", "moody"),
    "Suspenseful music":             ("mood", "moody"),
    # Romantic
    "Romantic music":                ("mood", "romantic"),
    "Love song":                     ("mood", "romantic"),
    # Motivational
    "March":                         ("mood", "motivational"),
    "Sports music":                  ("mood", "energetic"),
    # ---- Instruments ---------------------------------------------------------
    "Guitar":                  ("instrument", "guitar"),
    "Electric guitar":         ("instrument", "guitar"),
    "Bass guitar":             ("instrument", "bass"),
    "Acoustic guitar":         ("instrument", "acoustic-guitar"),
    "Piano":                   ("instrument", "piano"),
    "Electric piano":          ("instrument", "piano"),
    "Organ":                   ("instrument", "organ"),
    "Synthesizer":             ("instrument", "synth"),
    "Drum":                    ("instrument", "drums"),
    "Drum kit":                ("instrument", "drums"),
    "Snare drum":              ("instrument", "drums"),
    "Bass drum":               ("instrument", "drums"),
    "Drums":                   ("instrument", "drums"),
    "Drum machine":            ("instrument", "drum-machine"),
    "Violin":                  ("instrument", "violin"),
    "Viola":                   ("instrument", "viola"),
    "Cello":                   ("instrument", "cello"),
    "Double bass":             ("instrument", "double-bass"),
    "Trumpet":                 ("instrument", "trumpet"),
    "Trombone":                ("instrument", "trombone"),
    "French horn":             ("instrument", "horn"),
    "Saxophone":               ("instrument", "saxophone"),
    "Flute":                   ("instrument", "flute"),
    "Clarinet":                ("instrument", "clarinet"),
    "Oboe":                    ("instrument", "oboe"),
    "Bassoon":                 ("instrument", "bassoon"),
    "Harp":                    ("instrument", "harp"),
    "Banjo":                   ("instrument", "banjo"),
    "Mandolin":                ("instrument", "mandolin"),
    "Ukulele":                 ("instrument", "ukulele"),
    "Sitar":                   ("instrument", "sitar"),
    "Tabla":                   ("instrument", "tabla"),
    "Marimba":                 ("instrument", "marimba"),
    "Xylophone":               ("instrument", "xylophone"),
    "Glockenspiel":            ("instrument", "glockenspiel"),
    "Harpsichord":             ("instrument", "harpsichord"),
    "Turntable":               ("instrument", "turntable"),
    "DJ":                      ("instrument", "turntable"),
    "Theremin":                ("instrument", "theremin"),
    "Accordion":               ("instrument", "accordion"),
    "Bagpipes":                ("instrument", "bagpipes"),
    "Steel guitar":            ("instrument", "steel-guitar"),
    "Percussion":              ("instrument", "percussion"),
    "Cowbell":                 ("instrument", "percussion"),
    "Tambourine":              ("instrument", "percussion"),
    "Bongo drum":              ("instrument", "percussion"),
    "Conga drum":              ("instrument", "percussion"),
    "Didgeridoo":              ("instrument", "didgeridoo"),
    "Harmonica":               ("instrument", "harmonica"),
    "Choir":                   ("instrument", "choir"),
    "Singing":                 ("instrument", "vocals"),
    "Male singing":            ("instrument", "vocals"),
    "Female singing":          ("instrument", "vocals"),
    "Rapping":                 ("instrument", "vocals"),
    "Beatboxing":              ("instrument", "vocals"),
    "Whistling":               ("instrument", "vocals"),
}

# Some AudioSet labels map to both a genre tag AND a mood tag (e.g. "Ambient
# music" is genre "ambient" and mood "calm"). A plain dict can't hold the same
# key twice — the later definition would silently overwrite the earlier one —
# so the second occurrence carries a " (mood)" suffix on its key to keep it
# distinct. _classify_sync() below matches on the label prefix (up to the
# " (" marker) rather than requiring an exact key, so both entries still fire
# for the one AudioSet label the model actually returns.

# ---------------------------------------------------------------------------
# ASTResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class ASTResult:
    """Classification output from the AST model, mapped to application tags.

    Fields
    ------
    genre_tags
        Music genre labels derived from AudioSet top predictions.
    mood_tags
        Emotional / energy character derived from AudioSet top predictions.
    instrument_tags
        Detected instruments derived from AudioSet top predictions.
    top_classes
        Top-20 raw AudioSet predictions as (label, confidence) tuples, sorted
        descending by confidence.  Useful for debugging and downstream re-mapping.
    """

    genre_tags: list[str] = field(default_factory=list)
    mood_tags: list[str] = field(default_factory=list)
    instrument_tags: list[str] = field(default_factory=list)
    top_classes: list[tuple[str, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lazy model singleton
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_feature_extractor = None   # type: ignore[assignment]
_model = None               # type: ignore[assignment]


def _load_model() -> None:
    """Load and cache the AST model + feature extractor (call once, thread-safe)."""
    global _feature_extractor, _model

    if _model is not None:
        return  # already loaded

    with _model_lock:
        if _model is not None:  # double-checked locking
            return

        logger.info("Loading AST model '%s' (this may take a moment on first run) ...", _MODEL_ID)

        try:
            from transformers import ASTFeatureExtractor, ASTForAudioClassification
        except ImportError as exc:
            raise RuntimeError(
                "transformers package is not installed.  "
                "Add 'transformers>=4.40' to requirements.txt."
            ) from exc

        try:
            fe = ASTFeatureExtractor.from_pretrained(
                _MODEL_ID,
                cache_dir=_MODEL_CACHE_DIR,
            )
            mdl = ASTForAudioClassification.from_pretrained(
                _MODEL_ID,
                cache_dir=_MODEL_CACHE_DIR,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load AST model '{_MODEL_ID}' from cache dir "
                f"'{_MODEL_CACHE_DIR}': {exc}"
            ) from exc

        # Force CPU; no CUDA dependency
        try:
            import torch
            mdl = mdl.to(torch.device("cpu"))
            mdl.eval()
        except ImportError as exc:
            raise RuntimeError(
                "torch package is not installed.  "
                "Add 'torch>=2.2' to requirements.txt."
            ) from exc

        _feature_extractor = fe
        _model = mdl
        logger.info("AST model loaded successfully.")


# ---------------------------------------------------------------------------
# Synchronous inference (runs in thread executor)
# ---------------------------------------------------------------------------


def _classify_sync(file_path: str, confidence_threshold: float) -> ASTResult:
    """Load audio, run AST inference, and map predictions to application tags.

    This function is CPU-bound and must be executed in a thread pool executor
    to avoid blocking the FastAPI event loop.

    Args:
        file_path: Path to the audio file (any librosa-supported format).
        confidence_threshold: Minimum sigmoid probability to include a class.

    Returns:
        ASTResult with populated genre, mood, and instrument tags.

    Raises:
        FileNotFoundError: If *file_path* does not exist.
        ValueError: If the audio cannot be decoded or is too short.
        RuntimeError: If the model cannot be loaded.
    """
    import torch

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    # --- Load audio at 16 kHz mono (AST requirement) ---
    try:
        import librosa

        y, sr = librosa.load(file_path, sr=_TARGET_SR, mono=True)
    except Exception as exc:
        raise ValueError(f"Cannot decode audio file '{file_path}': {exc}") from exc

    duration = len(y) / _TARGET_SR
    if duration < 0.5:
        logger.warning(
            "Very short audio file (%.2fs): %s — AST results may be unreliable",
            duration,
            file_path,
        )
        if duration == 0.0:
            raise ValueError(
                f"Audio file appears to be silent or zero-length: {file_path}"
            )

    # --- Lazy model load ---
    _load_model()

    # --- Preprocess with ASTFeatureExtractor ---
    inputs = _feature_extractor(
        y,
        sampling_rate=_TARGET_SR,
        return_tensors="pt",
    )

    # --- Inference ---
    logger.debug("Running AST inference on %s (%.1fs at %d Hz)", file_path, duration, _TARGET_SR)
    with torch.no_grad():
        outputs = _model(**inputs)

    # AudioSet is a multi-label task — apply sigmoid, not softmax
    logits = outputs.logits[0]                     # shape (527,)
    probs = torch.sigmoid(logits).cpu().numpy()    # shape (527,)

    # id2label maps int index → AudioSet label string
    id2label: dict[int, str] = _model.config.id2label

    # --- Build top-20 list (all classes above threshold sorted by prob) ---
    above_threshold = [
        (id2label[i], float(probs[i]))
        for i in range(len(probs))
        if float(probs[i]) >= confidence_threshold
    ]
    above_threshold.sort(key=lambda x: x[1], reverse=True)
    top_classes = above_threshold[:20]

    logger.debug(
        "AST: %d classes above threshold %.2f; top-3: %s",
        len(above_threshold),
        confidence_threshold,
        top_classes[:3],
    )

    # --- Map AudioSet labels → application tags ---
    genre_set: set[str] = set()
    mood_set: set[str] = set()
    instrument_set: set[str] = set()

    for label, _prob in top_classes:
        for map_key, (category, tag) in _AUDIOSET_TAG_MAP.items():
            if map_key != label and not map_key.startswith(f"{label} ("):
                continue
            if category == "genre":
                genre_set.add(tag)
            elif category == "mood":
                mood_set.add(tag)
            elif category == "instrument":
                instrument_set.add(tag)

    result = ASTResult(
        genre_tags=sorted(genre_set),
        mood_tags=sorted(mood_set),
        instrument_tags=sorted(instrument_set),
        top_classes=top_classes,
    )
    logger.info(
        "AST tagged %s: genres=%s moods=%s instruments=%s",
        os.path.basename(file_path),
        result.genre_tags,
        result.mood_tags,
        result.instrument_tags,
    )
    return result


# ---------------------------------------------------------------------------
# Public async interface
# ---------------------------------------------------------------------------


async def classify_audio(
    file_path: str,
    confidence_threshold: float = 0.15,
) -> ASTResult:
    """Classify an audio file using the AST model and return mapped tags.

    Runs the CPU-bound model inference in the default thread executor so the
    FastAPI event loop remains non-blocking.

    Args:
        file_path: Absolute or relative path to the audio file.
        confidence_threshold: Sigmoid probability floor for including an
            AudioSet class in the results (default 0.15).  Lower values
            produce more tags with less certainty; higher values are stricter.

    Returns:
        ASTResult containing genre_tags, mood_tags, instrument_tags, and
        top_classes (up to 20 raw AudioSet predictions).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the audio cannot be decoded or is zero-length.
        RuntimeError: If the model fails to load.
    """
    loop = asyncio.get_event_loop()
    result: ASTResult = await loop.run_in_executor(
        None, _classify_sync, file_path, confidence_threshold
    )
    return result
