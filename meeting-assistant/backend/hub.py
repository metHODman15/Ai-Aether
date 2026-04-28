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
# silently swallow problems. Always preserved during backpressure —
# never evicted from the queue, even under saturation.
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

# High-volume event classes that are safe to drop when a slow client's
# queue fills up. These are streaming/incremental updates whose newer
# successors quickly supersede them, so dropping the oldest one of
# these has no lasting effect on dashboard correctness.
DROPPABLE_EVENT_TYPES: frozenset[str] = frozenset({
    "transcript",
    "entities",
    "document_unit",
})

# Maximum number of pending messages per client before we start
# dropping the oldest droppable event.
DEFAULT_QUEUE_DEPTH = 50


def is_critical(event: dict[str, Any]) -> bool:
    return (event.get("type") or "") in CRITICAL_EVENT_TYPES


def is_droppable(event: dict[str, Any]) -> bool:
    return (event.get("type") or "") in DROPPABLE_EVENT_TYPES


class _ClientChannel:
    """A bounded send queue + sender task for a single WebSocket client."""

    def __init__(self, ws: WebSocket, max_depth: int = DEFAULT_QUEUE_DEPTH) -> None:
        self.ws = ws
        self.id = uuid.uuid4().hex[:8]
        self._max_depth = max_depth
        # deque[(payload_str, is_critical, is_droppable)]
        self._queue: deque[tuple[str, bool, bool]] = deque()
        self._cv = asyncio.Condition()
        self._closed = False
        self._dropped_count = 0
        self.task: asyncio.Task | None = None

    async def offer(self, event: dict[str, Any], payload: str) -> bool:
        """Attempt to enqueue an event with backpressure-aware policy.

        When the queue is full we evict the oldest *droppable* event
        (a high-volume streaming event such as transcript / entities /
        document_unit) to make room. Critical events are never
        evicted. If the queue is full and contains no droppable
        entries:
          - if the incoming event is droppable, the incoming event is
            dropped (apply backpressure to the sender);
          - if the incoming event is critical, we make room for it by
            evicting the oldest non-critical entry; if there are no
            non-critical entries we keep all critical entries and
            drop the incoming critical event only as a last resort,
            logging loudly.

        Returns True if the event was enqueued, False if it was
        intentionally dropped or the channel is already closed.
        """
        critical = is_critical(event)
        droppable = is_droppable(event)
        async with self._cv:
            if self._closed:
                return False
            if len(self._queue) >= self._max_depth:
                # 1. Try to evict the oldest droppable (transcript /
                # entities / document_unit) entry first.
                drop_index: int | None = None
                for i, (_, _, is_drop) in enumerate(self._queue):
                    if is_drop:
                        drop_index = i
                        break

                # 2. If no droppable entries exist and the incoming
                # event is critical, fall back to evicting the oldest
                # non-critical entry (e.g. a `crm` payload) to keep
                # the critical signal flowing.
                if drop_index is None and critical:
                    for i, (_, is_crit, _) in enumerate(self._queue):
                        if not is_crit:
                            drop_index = i
                            break

                if drop_index is not None:
                    del self._queue[drop_index]
                    self._dropped_count += 1
                    logger.warning(
                        "Hub client %s queue full; evicted 1 non-critical event "
                        "to make room (total evicted: %d)",
                        self.id, self._dropped_count,
                    )
                elif not critical:
                    # Queue is full of critical-or-non-droppable events
                    # and the incoming event is non-critical: drop the
                    # incoming event to preserve queued state-change
                    # signals.
                    self._dropped_count += 1
                    logger.warning(
                        "Hub client %s queue full of critical events; dropped "
                        "incoming non-critical %r event (total dropped: %d)",
                        self.id, event.get("type"), self._dropped_count,
                    )
                    return False
                else:
                    # Last-resort: queue is entirely critical events
                    # and the incoming event is also critical. We must
                    # not silently corrupt either side, so we drop the
                    # *incoming* event and log loudly. The user will
                    # see the existing queued critical events first.
                    self._dropped_count += 1
                    logger.error(
                        "Hub client %s queue saturated with critical events; "
                        "dropped incoming critical %r event to preserve queued "
                        "state-change signals.",
                        self.id, event.get("type"),
                    )
                    return False
            self._queue.append((payload, critical, droppable))
            self._cv.notify()
            return True

    async def run(self) -> None:
        while True:
            async with self._cv:
                while not self._queue and not self._closed:
                    await self._cv.wait()
                if self._closed and not self._queue:
                    return
                payload, _, _ = self._queue.popleft()
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

        # Prune channels that closed during this or a previous broadcast.
        # A failed send marks the channel _closed=True in run(); leaving it in
        # _channels would leak memory until an explicit disconnect() arrives.
        async with self._lock:
            closed_ws = [ws for ws, ch in self._channels.items() if ch._closed]
            for ws in closed_ws:
                ch = self._channels.pop(ws)
                logger.info(
                    "Hub: auto-evicted closed channel %s from _channels after send failure",
                    ch.id,
                )
