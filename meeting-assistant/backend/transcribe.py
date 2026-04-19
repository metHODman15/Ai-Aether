"""Whisper-based transcription of audio chunks via the OpenAI API."""
from __future__ import annotations

import asyncio
import io
import logging

import numpy as np
import soundfile as sf
from openai import OpenAI, OpenAIError

logger = logging.getLogger(__name__)


class Transcriber:
    """Thin wrapper around OpenAI Whisper transcription."""

    def __init__(self, api_key: str, sample_rate: int, model: str = "whisper-1"):
        self._client = OpenAI(api_key=api_key)
        self._sample_rate = sample_rate
        self._model = model

    async def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a 1-D float32 audio array. Returns text or empty string."""
        if audio.size == 0:
            return ""

        return await asyncio.to_thread(self._transcribe_sync, audio)

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        # Skip near-silence to avoid burning API calls on empty audio.
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < 0.005:
            logger.debug("Skipping silent chunk (rms=%.4f)", rms)
            return ""

        buf = io.BytesIO()
        sf.write(buf, audio, self._sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)
        buf.name = "audio.wav"  # OpenAI SDK uses the .name attribute

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
                logger.warning(
                    "Whisper API error (attempt %d/3): %s", attempt + 1, exc
                )
                buf.seek(0)
                if attempt == 2:
                    return ""
        return ""
