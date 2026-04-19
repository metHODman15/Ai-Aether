"""Continuous microphone capture using sounddevice.

Exposes an async generator that yields fixed-length audio chunks
suitable for sending to a speech-to-text API.
"""
from __future__ import annotations

import asyncio
import logging
import queue
from typing import AsyncIterator

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


async def microphone_chunks(
    sample_rate: int,
    chunk_seconds: float,
    channels: int = 1,
) -> AsyncIterator[np.ndarray]:
    """Yield mono float32 numpy arrays of audio captured from the default mic.

    Each chunk is roughly `chunk_seconds` long. The capture runs continuously
    on a background sounddevice thread; this generator drains a queue without
    blocking the asyncio event loop.
    """
    frames_per_chunk = int(sample_rate * chunk_seconds)
    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, _frames, _time, status):
        if status:
            logger.warning("Audio input status: %s", status)
        # Copy because sounddevice reuses the buffer.
        audio_q.put(indata.copy())

    blocksize = max(1024, frames_per_chunk // 4)

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=blocksize,
            callback=callback,
        ):
            logger.info(
                "Microphone capture started: %d Hz, %.1fs chunks",
                sample_rate,
                chunk_seconds,
            )
            buffer = np.zeros((0, channels), dtype="float32")
            loop = asyncio.get_running_loop()
            while True:
                block = await loop.run_in_executor(None, audio_q.get)
                buffer = np.concatenate([buffer, block], axis=0)
                while buffer.shape[0] >= frames_per_chunk:
                    chunk = buffer[:frames_per_chunk]
                    buffer = buffer[frames_per_chunk:]
                    yield chunk.flatten() if channels == 1 else chunk
    except sd.PortAudioError as exc:
        logger.error("Audio capture failed: %s", exc)
        raise
