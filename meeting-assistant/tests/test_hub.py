"""Tests for backend.hub.ConnectionHub — connect/broadcast/disconnect."""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.hub import ConnectionHub, _ClientChannel


class _FakeWebSocket:
    """Minimal WebSocket stand-in that records sent text."""

    def __init__(self, fail_on_send: bool = False) -> None:
        self.sent: list[str] = []
        self.accepted: bool = False
        self._fail = fail_on_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, payload: str) -> None:
        if self._fail:
            raise RuntimeError("simulated send failure")
        self.sent.append(payload)


async def _drain(channel: _ClientChannel, n: int = 1) -> None:
    """Allow the channel's sender task to run until n messages are sent."""
    for _ in range(n * 20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_connect_replays_history_backlog():
    hub = ConnectionHub()
    event = {"type": "entities", "data": {"customer_name": "Acme"}}
    await hub.broadcast(event)

    ws = _FakeWebSocket()
    await hub.connect(ws)
    assert ws.accepted is True
    channel = hub._channels[ws]
    assert len(channel._queue) >= 1
    stored = json.loads(channel._queue[0][0])
    assert stored["type"] == "entities"

    await hub.disconnect(ws)


@pytest.mark.asyncio
async def test_broadcast_fans_out_to_all_clients():
    hub = ConnectionHub()
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()
    await hub.connect(ws1)
    await hub.connect(ws2)

    event = {"type": "transcript", "text": "hello"}
    await hub.broadcast(event)

    ch1 = hub._channels[ws1]
    ch2 = hub._channels[ws2]
    assert len(ch1._queue) >= 1
    assert len(ch2._queue) >= 1

    await hub.disconnect(ws1)
    await hub.disconnect(ws2)


@pytest.mark.asyncio
async def test_broadcast_trims_history_at_max():
    hub = ConnectionHub()
    hub._history_max = 5
    for i in range(7):
        await hub.broadcast({"type": "transcript", "text": f"chunk {i}"})
    assert len(hub._history) == 5
    assert hub._history[0]["text"] == "chunk 2"


@pytest.mark.asyncio
async def test_failed_send_silently_closes_channel_and_drops_future_messages():
    """A client whose send_text raises must be silently dropped from the active set.

    When a send fails the channel marks itself _closed=True. The *next* call to
    broadcast() auto-evicts the closed channel from _channels, preventing a
    memory leak from accumulating stale entries until disconnect() arrives.
    """
    hub = ConnectionHub()
    ws_bad = _FakeWebSocket(fail_on_send=True)
    ws_good = _FakeWebSocket()
    await hub.connect(ws_bad)
    await hub.connect(ws_good)

    trigger = {"type": "transcript", "text": "trigger send"}
    await hub.broadcast(trigger)

    channel = hub._channels[ws_bad]
    # Drain the sender task so it actually tries (and fails) send_text
    await _drain(channel, n=1)

    assert channel._closed is True, "Channel must mark itself closed after send failure"

    # Subsequent broadcast must not raise and must silently skip the failed client.
    # It also auto-evicts the closed channel from _channels to prevent memory leaks.
    follow_up = {"type": "transcript", "text": "follow-up"}
    await hub.broadcast(follow_up)  # must not raise

    # A closed channel's offer() returns False — no exception propagated
    result = await channel.offer(follow_up, "{}")
    assert result is False, "Closed channel must reject further offers silently"

    # After the follow-up broadcast, the closed channel must be auto-evicted.
    assert ws_bad not in hub._channels, (
        "broadcast() must auto-evict closed channels to prevent memory leaks"
    )

    # disconnect() on an already-evicted ws is a safe no-op.
    await hub.disconnect(ws_bad)
    assert ws_bad not in hub._channels

    await hub.disconnect(ws_good)


@pytest.mark.asyncio
async def test_connect_with_empty_history_starts_with_empty_queue():
    hub = ConnectionHub()
    ws = _FakeWebSocket()
    await hub.connect(ws)
    channel = hub._channels[ws]
    assert len(channel._queue) == 0
    await hub.disconnect(ws)


@pytest.mark.asyncio
async def test_disconnect_unknown_websocket_is_noop():
    hub = ConnectionHub()
    ws = _FakeWebSocket()
    await hub.disconnect(ws)
