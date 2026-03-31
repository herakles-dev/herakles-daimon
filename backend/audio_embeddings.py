"""PANNs audio embeddings for Herakles Play — Sprint 11 Audio Intelligence (S11.3).

Generates 2048-dim audio embeddings using the PANNs CNN14 architecture, pre-trained
on AudioSet.  Embeddings capture timbral, rhythmic, and semantic audio characteristics
and are stored in the ``media_embeddings.audio_embedding`` column (vector(2048)).

Public interface
----------------
embed_audio(file_path)          → np.ndarray   (2048,) float32
embed_batch(file_paths, ...)    → list[np.ndarray]

Implementation notes
--------------------
- Uses ``panns_inference`` if available (recommended); falls back to raw torch with
  manually-built CNN14 if the package is missing or broken.
- Model weights are downloaded once and cached under MODEL_CACHE_DIR.
- All inference runs on CPU.  The GIL-heavy torch work is offloaded to a thread-pool
  executor so FastAPI's event loop is never blocked.
- A threading.Lock protects the single shared model instance (lazy initialisation).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger("play-backend.audio-embeddings")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL_CACHE_DIR: str = os.getenv("MODEL_CACHE_DIR", "/models")
PANNS_SAMPLE_RATE: int = 32000           # CNN14 native sample rate
MEL_BINS: int = 64                       # log-mel spectrogram bins expected by CNN14
EMBEDDING_DIM: int = 2048                # CNN14 fc1 layer output dimension

# CNN14 checkpoint from Zenodo (Kong et al. 2020 — mAP=0.431)
CNN14_ZENODO_URL: str = (
    "https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth"
)
CNN14_CHECKPOINT_NAME: str = "Cnn14_mAP=0.431.pth"

# ---------------------------------------------------------------------------
# Lazy model state — protected by _model_lock
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_model_instance: Optional[object] = None   # either panns_inference.AudioTagging or _CNN14Wrapper
_model_backend: Optional[str] = None       # "panns_inference" | "raw_torch"


# ---------------------------------------------------------------------------
# panns_inference backend
# ---------------------------------------------------------------------------

def _load_panns_inference() -> object:
    """Attempt to load model via panns_inference package."""
    from panns_inference import AudioTagging  # type: ignore[import]

    logger.info("Loading CNN14 via panns_inference (checkpoint_path=None → auto-download)")
    at = AudioTagging(checkpoint_path=None, device="cpu")
    logger.info("panns_inference AudioTagging model ready")
    return at


def _embed_panns_inference(model: object, waveform: np.ndarray) -> np.ndarray:
    """Run inference using panns_inference AudioTagging.

    waveform: float32 array shape (samples,) at PANNS_SAMPLE_RATE Hz.
    Returns float32 array shape (2048,).
    """
    # panns_inference expects shape (1, samples)
    wav_batch = waveform[None, :]
    _clipwise, embedding = model.inference(wav_batch)
    # embedding shape: (1, 2048) — squeeze to (2048,)
    emb = np.array(embedding[0], dtype=np.float32)
    return emb


# ---------------------------------------------------------------------------
# Raw torch backend — CNN14 architecture
# ---------------------------------------------------------------------------

class _ConvBlock(object):
    """Minimal CNN14 conv block (two 3x3 convolutions + BN + avg-pool)."""

    def __init__(self, in_channels: int, out_channels: int, module: object) -> None:
        # module is the nn.Module; we just hold a reference for forward()
        self._module = module

    def __call__(self, x: object, pool_size: tuple = (2, 2), pool_type: str = "avg") -> object:
        return self._module(x, pool_size=pool_size, pool_type=pool_type)


def _build_cnn14_model() -> object:
    """Build the CNN14 model using torchlibrosa or a minimal torch implementation."""
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F

        # Try torchlibrosa first — it provides CNN14 directly
        try:
            from torchlibrosa.stft import Spectrogram, LogmelFilterBank
            from torchlibrosa.augmentation import SpecAugmentation

            _HAS_TORCHLIBROSA = True
        except ImportError:
            _HAS_TORCHLIBROSA = False
            logger.debug("torchlibrosa not available; using manual spectrogram computation")

        class ConvBlock(nn.Module):
            def __init__(self, in_channels: int, out_channels: int) -> None:
                super().__init__()
                self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=3,
                                       padding=1, bias=False)
                self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3,
                                       padding=1, bias=False)
                self.bn1 = nn.BatchNorm2d(out_channels)
                self.bn2 = nn.BatchNorm2d(out_channels)

            def forward(self, x: "torch.Tensor",
                        pool_size: tuple = (2, 2),
                        pool_type: str = "avg") -> "torch.Tensor":
                x = F.relu_(self.bn1(self.conv1(x)))
                x = F.relu_(self.bn2(self.conv2(x)))
                if pool_type == "avg":
                    x = F.avg_pool2d(x, kernel_size=pool_size)
                elif pool_type == "max":
                    x = F.max_pool2d(x, kernel_size=pool_size)
                return x

        class CNN14(nn.Module):
            """CNN14 architecture (Kong et al., 2020).

            Input: log-mel spectrogram (batch, 1, T, 64).
            Output: (clipwise_output, embedding) where embedding is (batch, 2048).
            """

            def __init__(self, classes_num: int = 527) -> None:
                super().__init__()
                self.bn0 = nn.BatchNorm2d(64)
                self.conv_block1 = ConvBlock(1, 64)
                self.conv_block2 = ConvBlock(64, 128)
                self.conv_block3 = ConvBlock(128, 256)
                self.conv_block4 = ConvBlock(256, 512)
                self.conv_block5 = ConvBlock(512, 1024)
                self.conv_block6 = ConvBlock(1024, 2048)
                self.fc1 = nn.Linear(2048, 2048, bias=True)
                self.fc_audioset = nn.Linear(2048, classes_num, bias=True)

            def forward(self, x: "torch.Tensor") -> tuple:
                # x: (batch, 1, T, mel_bins) — where mel_bins==64
                x = x.transpose(1, 3)   # (batch, mel_bins, T, 1)
                x = self.bn0(x)
                x = x.transpose(1, 3)   # back to (batch, 1, T, mel_bins)

                x = self.conv_block1(x, pool_size=(2, 2), pool_type="avg")
                x = F.dropout(x, p=0.2, training=self.training)
                x = self.conv_block2(x, pool_size=(2, 2), pool_type="avg")
                x = F.dropout(x, p=0.2, training=self.training)
                x = self.conv_block3(x, pool_size=(2, 2), pool_type="avg")
                x = F.dropout(x, p=0.2, training=self.training)
                x = self.conv_block4(x, pool_size=(2, 2), pool_type="avg")
                x = F.dropout(x, p=0.2, training=self.training)
                x = self.conv_block5(x, pool_size=(2, 2), pool_type="avg")
                x = F.dropout(x, p=0.2, training=self.training)
                x = self.conv_block6(x, pool_size=(1, 1), pool_type="avg")
                x = F.dropout(x, p=0.2, training=self.training)

                x = torch.mean(x, dim=3)                      # (batch, 2048, T')
                x1, _ = torch.max(x, dim=2)                   # (batch, 2048)
                x2 = torch.mean(x, dim=2)                     # (batch, 2048)
                x = x1 + x2                                    # (batch, 2048)

                x = F.dropout(x, p=0.5, training=self.training)
                x = F.relu_(self.fc1(x))                       # (batch, 2048) — fc1 embedding
                embedding = F.dropout(x, p=0.5, training=self.training)

                clipwise_output = torch.sigmoid(self.fc_audioset(x))
                return clipwise_output, embedding

        return CNN14

    except ImportError as exc:
        raise RuntimeError(
            "torch is required for raw CNN14 backend — install torch or use panns-inference"
        ) from exc


def _download_cnn14_checkpoint(cache_dir: str) -> str:
    """Download CNN14 weights to cache_dir if not already present.

    Returns the local path to the checkpoint file.
    """
    import urllib.request

    os.makedirs(cache_dir, exist_ok=True)
    dest = os.path.join(cache_dir, CNN14_CHECKPOINT_NAME)

    if os.path.exists(dest):
        logger.info("CNN14 checkpoint already cached at %s", dest)
        return dest

    logger.info("Downloading CNN14 checkpoint from Zenodo → %s", dest)
    logger.info("URL: %s", CNN14_ZENODO_URL)

    try:
        urllib.request.urlretrieve(CNN14_ZENODO_URL, dest)
        size_mb = os.path.getsize(dest) / 1_048_576
        logger.info("Downloaded CNN14 checkpoint (%.1f MB)", size_mb)
    except Exception as exc:
        # Clean up partial download
        if os.path.exists(dest):
            os.remove(dest)
        raise RuntimeError(
            f"Failed to download CNN14 checkpoint: {exc}\n"
            f"Manually download from {CNN14_ZENODO_URL} → {dest}"
        ) from exc

    return dest


class _CNN14Wrapper:
    """Wraps raw-torch CNN14 for inference, mimicking panns_inference API."""

    def __init__(self, checkpoint_path: str) -> None:
        import torch

        CNN14 = _build_cnn14_model()
        self._model = CNN14(classes_num=527)

        logger.info("Loading CNN14 weights from %s", checkpoint_path)
        checkpoint = torch.load(checkpoint_path, map_location="cpu")

        # Checkpoint may be stored under 'model' key
        state_dict = checkpoint.get("model", checkpoint)
        self._model.load_state_dict(state_dict, strict=False)
        self._model.eval()
        logger.info("CNN14 raw-torch model ready (CPU)")

    def compute_logmel(self, waveform: np.ndarray) -> "torch.Tensor":
        """Convert waveform to log-mel spectrogram tensor for CNN14.

        waveform: float32 array (samples,) at PANNS_SAMPLE_RATE Hz.
        Returns tensor (1, 1, T, 64).
        """
        import torch
        import librosa

        # Log-mel spectrogram — CNN14 paper: window=1024, hop=320, mels=64
        mel = librosa.feature.melspectrogram(
            y=waveform,
            sr=PANNS_SAMPLE_RATE,
            n_fft=1024,
            hop_length=320,
            n_mels=MEL_BINS,
            fmin=50.0,
            fmax=14000.0,
        )
        log_mel = librosa.power_to_db(mel, ref=1.0)  # (64, T)

        # Normalise: mean=0, std=1 per batch item
        log_mel = (log_mel - log_mel.mean()) / (log_mel.std() + 1e-9)

        # Shape: (1, 1, T, mel_bins)
        tensor = torch.from_numpy(log_mel.T[None, None, :, :].astype(np.float32))
        return tensor

    def inference(self, waveform: np.ndarray) -> np.ndarray:
        """Run CNN14 and return 2048-dim embedding.

        waveform: float32 (samples,) at PANNS_SAMPLE_RATE.
        Returns float32 array (2048,).
        """
        import torch

        mel_tensor = self.compute_logmel(waveform)

        with torch.no_grad():
            _clipwise, embedding = self._model(mel_tensor)

        emb = embedding[0].numpy().astype(np.float32)  # (2048,)
        return emb


# ---------------------------------------------------------------------------
# Model loader (lazy, thread-safe)
# ---------------------------------------------------------------------------

def _get_model() -> tuple[object, str]:
    """Return (model, backend_name), loading on first call."""
    global _model_instance, _model_backend

    if _model_instance is not None:
        return _model_instance, _model_backend  # type: ignore[return-value]

    with _model_lock:
        # Double-checked locking
        if _model_instance is not None:
            return _model_instance, _model_backend  # type: ignore[return-value]

        # Attempt 1: panns_inference package
        try:
            model = _load_panns_inference()
            _model_instance = model
            _model_backend = "panns_inference"
            logger.info("Audio embedding backend: panns_inference")
            return _model_instance, _model_backend  # type: ignore[return-value]
        except ImportError:
            logger.info(
                "panns_inference not installed; falling back to raw-torch CNN14"
            )
        except Exception as exc:
            logger.warning(
                "panns_inference failed to load (%s); falling back to raw-torch CNN14",
                exc,
            )

        # Attempt 2: raw torch with downloaded weights
        try:
            checkpoint_path = _download_cnn14_checkpoint(MODEL_CACHE_DIR)
            model = _CNN14Wrapper(checkpoint_path)
            _model_instance = model
            _model_backend = "raw_torch"
            logger.info("Audio embedding backend: raw_torch CNN14")
            return _model_instance, _model_backend  # type: ignore[return-value]
        except Exception as exc:
            raise RuntimeError(
                f"Could not initialise any audio embedding backend: {exc}\n"
                "Install panns-inference (pip install panns-inference) or "
                "ensure torch is available and MODEL_CACHE_DIR is writable."
            ) from exc


# ---------------------------------------------------------------------------
# Synchronous embedding (runs in thread executor)
# ---------------------------------------------------------------------------

def _load_audio_32k(file_path: str) -> np.ndarray:
    """Load audio file at CNN14 native rate (32 kHz, mono, float32)."""
    import librosa

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    try:
        y, _sr = librosa.load(file_path, sr=PANNS_SAMPLE_RATE, mono=True)
    except Exception as exc:
        raise ValueError(f"Cannot decode audio file '{file_path}': {exc}") from exc

    if y.size == 0 or float(np.max(np.abs(y))) < 1e-9:
        raise ValueError(f"Audio file is silent or zero-length: {file_path}")

    return y.astype(np.float32)


def _embed_sync(file_path: str) -> np.ndarray:
    """Synchronous embedding generation — intended to run in a thread executor."""
    logger.info("Generating PANNs embedding for: %s", file_path)

    y = _load_audio_32k(file_path)
    model, backend = _get_model()

    if backend == "panns_inference":
        emb = _embed_panns_inference(model, y)
    else:
        # _CNN14Wrapper
        emb = model.inference(y)  # type: ignore[union-attr]

    if emb.shape != (EMBEDDING_DIM,):
        raise RuntimeError(
            f"Unexpected embedding shape {emb.shape}; expected ({EMBEDDING_DIM},)"
        )

    if not np.all(np.isfinite(emb)):
        n_bad = int(np.sum(~np.isfinite(emb)))
        logger.warning(
            "%d non-finite values in embedding for %s — zeroing", n_bad, file_path
        )
        emb = np.where(np.isfinite(emb), emb, 0.0).astype(np.float32)

    logger.info(
        "Embedding generated (backend=%s, shape=%s, norm=%.4f)",
        backend,
        emb.shape,
        float(np.linalg.norm(emb)),
    )
    return emb


# ---------------------------------------------------------------------------
# Public async interface
# ---------------------------------------------------------------------------


async def embed_audio(file_path: str) -> np.ndarray:
    """Generate a 2048-dim PANNs embedding from an audio file.

    Offloads CPU-bound inference to a thread-pool executor.

    Args:
        file_path: Absolute or relative path to an audio file (any format
                   supported by librosa — MP3, WAV, FLAC, OGG, etc.).

    Returns:
        float32 numpy array of shape (2048,).

    Raises:
        FileNotFoundError: File does not exist.
        ValueError: File cannot be decoded or is silent/zero-length.
        RuntimeError: Model backend could not be initialised, or embedding
                      has an unexpected shape.
    """
    loop = asyncio.get_event_loop()
    emb: np.ndarray = await loop.run_in_executor(None, _embed_sync, file_path)
    return emb


async def embed_batch(
    file_paths: list[str],
    batch_size: int = 4,
) -> list[np.ndarray]:
    """Batch embedding for multiple audio files.

    Processes files in groups of ``batch_size``.  Each file is embedded
    independently (CNN14 operates per-clip); batching here simply controls
    memory pressure by limiting concurrent thread-pool tasks.

    Args:
        file_paths: List of audio file paths.
        batch_size: Number of files to process concurrently per batch.

    Returns:
        List of float32 numpy arrays, one per input file, preserving order.
        Files that fail are represented by a zero vector so the list length
        always equals len(file_paths).
    """
    if not file_paths:
        return []

    results: list[np.ndarray] = [np.zeros(EMBEDDING_DIM, dtype=np.float32)] * len(file_paths)

    for batch_start in range(0, len(file_paths), batch_size):
        batch = file_paths[batch_start : batch_start + batch_size]
        tasks = [embed_audio(fp) for fp in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for offset, (fp, result) in enumerate(zip(batch, batch_results)):
            idx = batch_start + offset
            if isinstance(result, Exception):
                logger.error("embed_batch: failed for %s — %s", fp, result)
                results[idx] = np.zeros(EMBEDDING_DIM, dtype=np.float32)
            else:
                results[idx] = result  # type: ignore[assignment]

        logger.debug(
            "embed_batch: processed batch %d–%d / %d",
            batch_start,
            batch_start + len(batch) - 1,
            len(file_paths),
        )

    return results
