"""WebSocket connection hub with per-client backpressure.

Each connected client has a bounded send queue (default depth 50) and
its own dedicated sender task. When a queue is full the oldest
*non-critical* event is dropped to make room — ``topic_shift`` and
any error events are always preserved so the user never loses
state-change signals during a slow-client situation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# Events whose loss would cause the UI to display incorrect state or
# silently swallow problems. Always preserved during backpressure.
CRITICAL_EVENT_TYPES: frozenset[str] = frozenset({
    "topic_shift",
    "error",
    "document_error",
    "document_unit_error",
    "crm_offline",
    "crm_online",
    "settings",
    "document_start",
    "document_done",
})

# Maximum number of pending messages per client before we start
# dropping the oldest non-critical event.
DEFAULT_QUEUE_DEPTH = 50


def is_critical(event: dict[str, Any]) -> bool:
    return (event.get("type") or "") in CRITICAL_EVENT_TYPES


class _ClientChannel:
    """A bounded send queue + sender task for a single WebSocket client."""

    def __init__(self, ws: WebSocket, max_depth: int = DEFAULT_QUEUE_DEPTH) -> None:
        self.ws = ws
        self.id = uuid.uuid4().hex[:8]
        self._max_depth = max_depth
        # deque[(payload_str, is_critical)]
        self._queue: deque[tuple[str, bool]] = deque()
        self._cv = asyncio.Condition()
        self._closed = False
        self._dropped_count = 0
        self.task: asyncio.Task | None = None

    async def offer(self, event: dict[str, Any], payload: str) -> bool:
        """Attempt to enqueue an event. Drops oldest non-critical if full.

        Returns True if the event was enqueued (possibly after dropping
        another), False only if the channel is already closed.
        """
        critical = is_critical(event)
        async with self._cv:
            if self._closed:
                return False
            if len(self._queue) >= self._max_depth:
                # Find the oldest non-critical entry to drop.
                drop_index: int | None = None
                for i, (_, is_crit) in enumerate(self._queue):
                    if not is_crit:
                        drop_index = i
                        break
                if drop_index is not None:
                    del self._queue[drop_index]
                    self._dropped_count += 1
                    logger.warning(
                        "Hub client %s queue full; dropped 1 non-critical event "
                        "(total dropped: %d)",
                        self.id, self._dropped_count,
                    )
                else:
                    # Queue is entirely critical events. To preserve them,
                    # drop the oldest critical entry and log loudly.
                    self._queue.popleft()
                    self._dropped_count += 1
                    logger.error(
                        "Hub client %s queue saturated with critical events; "
                        "dropped oldest critical event to make room.",
                        self.id,
                    )
            self._queue.append((payload, critical))
            self._cv.notify()
            return True

    async def run(self) -> None:
        while True:
            async with self._cv:
                while not self._queue and not self._closed:
                    await self._cv.wait()
                if self._closed and not self._queue:
                    return
                payload, _ = self._queue.popleft()
            try:
                await self.ws.send_text(payload)
            except Exception as exc:
                logger.debug("Hub client %s send failed: %s", self.id, exc)
                async with self._cv:
                    self._closed = True
                return

    async def close(self) -> None:
        async with self._cv:
            self._closed = True
            self._cv.notify_all()


class ConnectionHub:
    def __init__(self, max_queue_depth: int = DEFAULT_QUEUE_DEPTH) -> None:
        self._channels: dict[WebSocket, _ClientChannel] = {}
        self._lock = asyncio.Lock()
        self._history: list[dict[str, Any]] = []
        self._history_max = 100
        self._max_queue_depth = max_queue_depth

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        channel = _ClientChannel(ws, max_depth=self._max_queue_depth)
        channel.task = asyncio.create_task(channel.run())
        async with self._lock:
            self._channels[ws] = channel
            backlog = list(self._history)
        # Replay backlog into the new channel's queue (preserving order).
        for event in backlog:
            payload = json.dumps(event)
            await channel.offer(event, payload)
        logger.info("Hub: client %s connected (backlog=%d)", channel.id, len(backlog))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            channel = self._channels.pop(ws, None)
        if channel is None:
            return
        await channel.close()
        if channel.task is not None:
            try:
                await asyncio.wait_for(channel.task, timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                channel.task.cancel()
        logger.info("Hub: client %s disconnected", channel.id)

    async def broadcast(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event)
        async with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]
            channels = list(self._channels.values())

        for channel in channels:
            await channel.offer(event, payload)
