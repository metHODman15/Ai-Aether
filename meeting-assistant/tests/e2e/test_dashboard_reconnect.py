"""End-to-end test: dashboard WebSocket reconnect lifecycle.

Exercises the full badge sequence:
  Connecting… → Connected → Reconnecting… → Connected

Also verifies that the topic cached in frontend state remains visible in
the history panel throughout the disconnect and after reconnect.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
import pytest
from playwright.sync_api import Page, expect

_E2E_DIR = Path(__file__).parent
_TESTS_DIR = _E2E_DIR.parent
_PROJECT_DIR = _TESTS_DIR.parent
_SERVER_SCRIPT = _TESTS_DIR / "helpers" / "minimal_server.py"

TOPIC_LABEL = "Product Roadmap Discussion"
# Hold the WS handshake open long enough to assert "Connecting…" before it
# flips to "Connected".
_WS_CONNECT_DELAY = 2.0


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, connect_delay: float = 0.0) -> subprocess.Popen:
    cmd = [sys.executable, str(_SERVER_SCRIPT), str(port)]
    if connect_delay > 0:
        cmd += ["--connect-delay", str(connect_delay)]
    return subprocess.Popen(
        cmd,
        cwd=str(_PROJECT_DIR),
        env={**os.environ, "PYTHONPATH": str(_PROJECT_DIR)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=1).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"Server on port {port} not healthy after {timeout}s")


@pytest.fixture()
def free_port() -> int:
    return _find_free_port()


def test_dashboard_reconnects_gracefully(page: Page, free_port: int) -> None:
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        # Start with a deliberate WS-accept delay to make "Connecting…" assertable.
        proc = _start_server(port, connect_delay=_WS_CONNECT_DELAY)
        _wait_for_server(port)

        page.goto(base_url)
        status = page.locator("#status")

        # Initial state: WS handshake is still in progress.
        expect(status).to_have_text("Connecting\u2026", timeout=8_000)
        assert "disconnected" in (status.get_attribute("class") or "")

        # Handshake completes after the connect delay.
        expect(status).to_have_text("Connected", timeout=15_000)
        assert "connected" in (status.get_attribute("class") or "")

        # Seed a topic so it appears in the history panel.
        r = requests.post(
            f"{base_url}/inject/topic",
            json={"label": TOPIC_LABEL, "summary": "Reconnect test topic"},
            timeout=5,
        )
        assert r.status_code == 200
        topic_item = page.locator("#historyList .h-label", has_text=TOPIC_LABEL)
        expect(topic_item).to_be_visible(timeout=8_000)

        # Kill the server — simulates crash/restart.
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        proc = None

        # ws.onclose fires immediately and updates the badge.
        expect(status).to_have_text("Reconnecting\u2026", timeout=10_000)
        assert "reconnecting" in (status.get_attribute("class") or "")
        # Frontend in-memory state is preserved across disconnects.
        expect(topic_item).to_be_visible(timeout=2_000)

        # Bring the server back up.
        proc = _start_server(port, connect_delay=0.0)
        _wait_for_server(port)

        # Backoff loop re-connects; allow up to 40 s for retries (1 s, 2 s, …).
        expect(status).to_have_text("Connected", timeout=40_000)
        assert "connected" in (status.get_attribute("class") or "")
        expect(topic_item).to_be_visible(timeout=5_000)

    finally:
        if proc is not None:
            proc.kill()
            proc.wait()
