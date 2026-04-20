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
    get_chunk_seconds=None,
    get_sample_rate=None,
) -> AsyncIterator[np.ndarray]:
    """Yield mono float32 numpy arrays of audio captured from the default mic.

    Each chunk is roughly `chunk_seconds` long. The capture runs continuously
    on a background sounddevice thread; this generator drains a queue without
    blocking the asyncio event loop.

    If `get_chunk_seconds` is provided (a zero-argument callable), the chunk
    duration is re-evaluated on every iteration so changes take effect without
    restarting the server.

    If `get_sample_rate` is provided, the generator exits when the returned
    sample rate differs from the one the stream was opened with, allowing the
    caller to restart the generator with the new rate.
    """
    initial_chunk_seconds = chunk_seconds
    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, _frames, _time, status):
        if status:
            logger.warning("Audio input status: %s", status)
        # Copy because sounddevice reuses the buffer.
        audio_q.put(indata.copy())

    initial_frames = int(sample_rate * initial_chunk_seconds)
    blocksize = max(1024, initial_frames // 4)

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
                initial_chunk_seconds,
            )
            buffer = np.zeros((0, channels), dtype="float32")
            loop = asyncio.get_running_loop()
            while True:
                if get_sample_rate is not None and get_sample_rate() != sample_rate:
                    logger.info(
                        "Sample rate changed to %d Hz, restarting audio capture",
                        get_sample_rate(),
                    )
                    return
                block = await loop.run_in_executor(None, audio_q.get)
                buffer = np.concatenate([buffer, block], axis=0)
                current_seconds = get_chunk_seconds() if get_chunk_seconds is not None else initial_chunk_seconds
                frames_per_chunk = int(sample_rate * current_seconds)
                while buffer.shape[0] >= frames_per_chunk:
                    chunk = buffer[:frames_per_chunk]
                    buffer = buffer[frames_per_chunk:]
                    yield chunk.flatten() if channels == 1 else chunk
    except sd.PortAudioError as exc:
        logger.error("Audio capture failed: %s", exc)
        raise
