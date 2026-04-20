"""Whisper-based transcription of audio chunks.

Two backends are supported, selected via the ``WHISPER_BACKEND`` env var:

* ``openai``  (default) — sends audio to the OpenAI Whisper API.
* ``local``             — runs a faster-whisper model entirely on-device;
                          no network calls after the initial model download.
"""
from __future__ import annotations

import asyncio
import io
import logging
from abc import ABC, abstractmethod

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

_SILENCE_RMS = 0.005


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _is_silent(audio: np.ndarray) -> bool:
    rms = float(np.sqrt(np.mean(np.square(audio))))
    if rms < _SILENCE_RMS:
        logger.debug("Skipping silent chunk (rms=%.4f)", rms)
        return True
    return False


def _to_wav_bytes(audio: np.ndarray, sample_rate: int) -> io.BytesIO:
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    buf.name = "audio.wav"
    return buf


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class Transcriber(ABC):
    """Common interface for all transcription backends."""

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value: int) -> None:
        self._sample_rate = value

    async def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a 1-D float32 audio array. Returns text or empty string."""
        if audio.size == 0:
            return ""
        return await asyncio.to_thread(self._transcribe_sync, audio)

    @abstractmethod
    def _transcribe_sync(self, audio: np.ndarray) -> str: ...


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

class OpenAITranscriber(Transcriber):
    """Sends audio to the OpenAI Whisper API."""

    def __init__(self, api_key: str, sample_rate: int, model: str = "whisper-1") -> None:
        super().__init__(sample_rate)
        from openai import OpenAI  # noqa: PLC0415
        self._client = OpenAI(api_key=api_key)
        self._model = model

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        from openai import OpenAIError  # noqa: PLC0415

        if _is_silent(audio):
            return ""

        buf = _to_wav_bytes(audio, self._sample_rate)

        for attempt in range(3):
            try:
                result = self._client.audio.transcriptions.create(
                    model=self._model,
                    file=buf,
                    response_format="text",
                )
                text = (result if isinstance(result, str) else getattr(result, "text", "")).strip()
                return text
            except OpenAIError as exc:
                logger.warning("Whisper API error (attempt %d/3): %s", attempt + 1, exc)
                buf.seek(0)
                if attempt == 2:
                    return ""
        return ""


# ---------------------------------------------------------------------------
# Local (faster-whisper) backend
# ---------------------------------------------------------------------------

class LocalWhisperTranscriber(Transcriber):
    """Runs Whisper entirely on-device via the faster-whisper library.

    The model is downloaded once from Hugging Face and cached locally.
    No audio data leaves the machine after that point.

    Parameters
    ----------
    model_size:
        One of ``tiny``, ``base``, ``small``, ``medium``, ``large-v2``, or
        ``large-v3``.  Larger models are more accurate but slower and use
        more RAM/VRAM.  ``base`` is a reasonable default for most laptops.
    device:
        ``"cpu"`` or ``"cuda"``.  Defaults to ``"cpu"`` so it works without
        a GPU.
    compute_type:
        Quantisation level.  ``"int8"`` (default) keeps memory use low on
        CPU.  Use ``"float16"`` for GPU inference.
    """

    def __init__(
        self,
        sample_rate: int,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        super().__init__(sample_rate)
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is required for the local Whisper backend. "
                "Install it with:  pip install faster-whisper"
            ) from exc

        logger.info(
            "Loading local Whisper model '%s' on %s (compute_type=%s). "
            "This may take a moment on first run while the model is downloaded.",
            model_size, device, compute_type,
        )
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        logger.info("Local Whisper model ready.")

    # faster-whisper / Whisper models are trained at 16 kHz.
    _WHISPER_SAMPLE_RATE = 16_000

    def _resample_to_16k(self, audio: np.ndarray) -> np.ndarray:
        """Resample *audio* to 16 kHz if needed using linear interpolation."""
        if self._sample_rate == self._WHISPER_SAMPLE_RATE:
            return audio
        num_out = int(len(audio) * self._WHISPER_SAMPLE_RATE / self._sample_rate)
        if num_out == 0:
            return audio
        resampled = np.interp(
            np.linspace(0, len(audio) - 1, num_out),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
        logger.debug(
            "Resampled audio from %d Hz to %d Hz (%d → %d samples)",
            self._sample_rate, self._WHISPER_SAMPLE_RATE, len(audio), num_out,
        )
        return resampled

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        if _is_silent(audio):
            return ""

        audio_f32 = self._resample_to_16k(audio.astype(np.float32))
        segments, _info = self._model.transcribe(audio_f32, beam_size=5)
        text = " ".join(seg.text for seg in segments).strip()
        return text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_transcriber(
    sample_rate: int,
    backend: str = "openai",
    *,
    openai_api_key: str | None = None,
    openai_model: str = "whisper-1",
    local_model_size: str = "base",
    local_device: str = "cpu",
    local_compute_type: str = "int8",
) -> Transcriber:
    """Return the appropriate :class:`Transcriber` for *backend*.

    Parameters
    ----------
    backend:
        ``"openai"`` or ``"local"``.
    openai_api_key:
        Required when *backend* is ``"openai"``.
    openai_model:
        OpenAI model name (default ``"whisper-1"``).
    local_model_size:
        faster-whisper model size (default ``"base"``).
    local_device:
        ``"cpu"`` or ``"cuda"`` (default ``"cpu"``).
    local_compute_type:
        Quantisation type for faster-whisper (default ``"int8"``).
    """
    backend = backend.lower().strip()

    if backend == "openai":
        if not openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY must be set when WHISPER_BACKEND=openai (the default)."
            )
        return OpenAITranscriber(
            api_key=openai_api_key,
            sample_rate=sample_rate,
            model=openai_model,
        )

    if backend == "local":
        return LocalWhisperTranscriber(
            sample_rate=sample_rate,
            model_size=local_model_size,
            device=local_device,
            compute_type=local_compute_type,
        )

    raise ValueError(
        f"Unknown WHISPER_BACKEND '{backend}'. Valid values are 'openai' and 'local'."
    )
