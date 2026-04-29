"""Minimal FastAPI server for the dashboard reconnect e2e test.

Serves the frontend static files, proxies WebSocket connections through the
real ConnectionHub, and exposes two test-only routes:

  GET  /health        – readiness probe
  POST /inject/topic  – broadcast a topic_shift so the hub caches it

CLI: python tests/helpers/minimal_server.py <port> [--connect-delay <s>]
     --connect-delay delays ws.accept() to hold the browser in "Connecting…"
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_PROJECT_DIR = Path(__file__).parent.parent.parent  # meeting-assistant/
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from backend.hub import ConnectionHub

hub = ConnectionHub()
app = FastAPI(title="Minimal Test Server")
_ws_connect_delay: float = 0.0


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/settings")
async def get_settings() -> JSONResponse:
    return JSONResponse({
        "sensitivity": "balanced",
        "sensitivity_options": ["conservative", "balanced", "aggressive"],
        "audio_chunk_seconds": 5.0,
        "audio_chunk_seconds_min": 1.0,
        "audio_chunk_seconds_max": 30.0,
        "audio_sample_rate": 16000,
        "audio_sample_rate_options": [8000, 16000, 44100],
    })


@app.get("/history")
async def list_history() -> JSONResponse:
    return JSONResponse([])


@app.post("/inject/topic")
async def inject_topic(payload: dict) -> JSONResponse:
    event: dict = {
        "type": "topic_shift",
        "ts": time.time(),
        "label": payload.get("label", "Test Topic"),
        "summary": payload.get("summary", "Reconnect verification topic"),
        "meeting_id": "test-meeting-reconnect-001",
    }
    await hub.broadcast(event)
    return JSONResponse({"ok": True})


@app.post("/inject/crm")
async def inject_crm(payload: dict) -> JSONResponse:
    """Test-only: broadcast a crm_offline / crm_online event.

    Lets the dashboard e2e tests exercise the offline banner without
    spinning up the full Salesforce MCP stack.
    """
    online = bool(payload.get("online", False))
    event: dict
    if online:
        event = {"type": "crm_online", "ts": time.time()}
    else:
        event = {
            "type": "crm_offline",
            "ts": time.time(),
            "reason": payload.get("reason", "Salesforce is unreachable"),
        }
    await hub.broadcast(event)
    return JSONResponse({"ok": True})


# Retry-button test fixtures. ``_retry_state`` lets the e2e test script the
# response of the next ``POST /salesforce/retry`` (success → also broadcasts
# ``crm_online`` so the banner hides; failure → stays offline). ``cached``
# and ``age_seconds`` mirror the production contract so the dashboard can
# render its "Just checked Xs ago" hint when the backend coalesces a click
# into a recent probe. ``calls`` counts invocations so the test can assert
# the click reached the backend.
_retry_state: dict = {
    "online": False,
    "delay": 0.0,
    "cached": False,
    "age_seconds": 0.0,
    "calls": 0,
}


@app.post("/configure/retry")
async def configure_retry(payload: dict) -> JSONResponse:
    """Test-only: configure the next ``/salesforce/retry`` response."""
    _retry_state["online"] = bool(payload.get("online", False))
    try:
        _retry_state["delay"] = float(payload.get("delay", 0.0))
    except (TypeError, ValueError):
        _retry_state["delay"] = 0.0
    _retry_state["cached"] = bool(payload.get("cached", False))
    try:
        _retry_state["age_seconds"] = float(payload.get("age_seconds", 0.0))
    except (TypeError, ValueError):
        _retry_state["age_seconds"] = 0.0
    _retry_state["calls"] = 0
    return JSONResponse({"ok": True})


@app.get("/configure/retry")
async def configure_retry_state() -> JSONResponse:
    return JSONResponse({"calls": _retry_state["calls"]})


@app.post("/salesforce/retry")
async def salesforce_retry() -> JSONResponse:
    """Test-only stand-in for the production retry endpoint.

    Mirrors the production contract: returns
    ``{"online": bool, "cached": bool, "age_seconds": float}`` and, on a
    fresh success (``cached=False``), broadcasts a ``crm_online`` event
    the way ``probe_once`` would. A coalesced response intentionally does
    NOT broadcast — the underlying probe already did so on its first run.
    The configurable delay lets the test verify the button stays disabled
    while a probe is in flight.
    """
    _retry_state["calls"] += 1
    if _retry_state["delay"] > 0:
        await asyncio.sleep(_retry_state["delay"])
    online = _retry_state["online"]
    cached = _retry_state["cached"]
    if online and not cached:
        await hub.broadcast({"type": "crm_online", "ts": time.time()})
    return JSONResponse(
        {
            "online": online,
            "cached": cached,
            "age_seconds": _retry_state["age_seconds"],
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if _ws_connect_delay > 0:
        await asyncio.sleep(_ws_connect_delay)
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(ws)


app.mount("/", StaticFiles(directory=str(_PROJECT_DIR / "frontend"), html=True), name="static")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    delay = 0.0
    args = sys.argv[2:]
    for i, a in enumerate(args):
        if a == "--connect-delay" and i + 1 < len(args):
            try:
                delay = float(args[i + 1])
            except ValueError:
                pass
    _ws_connect_delay = delay
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
