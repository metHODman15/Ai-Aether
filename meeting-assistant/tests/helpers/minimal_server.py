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
