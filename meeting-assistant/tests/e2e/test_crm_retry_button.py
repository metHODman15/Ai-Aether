"""End-to-end test: Salesforce-offline banner "Retry now" button.

Verifies that reps watching the offline banner can force an immediate
liveness probe instead of waiting up to 30s for the next background tick.

Covers all "done looks like" criteria from task-84:
  1. The button shows when the offline reason is the MCP timeout.
  2. The button does NOT show for other offline reasons (auth, etc.) —
     those need explicit user action via the reauth link, not a probe.
  3. Clicking it POSTs to the backend retry endpoint.
  4. On success the dashboard flips back to green; on failure it surfaces
     "Still timing out" feedback while staying offline.
  5. The button is disabled while the probe is in flight so rapid-fire
     clicks can't stack requests against the MCP server.
"""
from __future__ import annotations

import os
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

# Must match backend/mcp_client.py::MCP_TIMEOUT_REASON exactly — this is
# the string the dashboard discriminates on to show the retry button.
_MCP_TIMEOUT_REASON = "Salesforce MCP server timed out"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(_SERVER_SCRIPT), str(port)],
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


def test_retry_button_lifecycle(page: Page, free_port: int) -> None:
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        page.goto(base_url)

        banner = page.locator("#crmBanner")
        retry = banner.locator(".crm-banner-retry")
        feedback = banner.locator(".crm-banner-retry-feedback")

        # Wait for the WebSocket to connect so injected events reach
        # this browser session.
        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        # ── Case 1: non-timeout offline reason → button hidden ──────────
        # Auth-required-style reasons need an explicit reconnect click,
        # not a liveness probe.
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": "auth_required"},
            timeout=5,
        )
        expect(banner).to_be_visible()
        expect(retry).to_be_hidden()

        # ── Case 2: MCP timeout offline → button visible ────────────────
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": _MCP_TIMEOUT_REASON},
            timeout=5,
        )
        expect(banner).to_be_visible()
        expect(retry).to_be_visible()
        expect(retry).to_have_text("Retry now")
        expect(retry).to_be_enabled()

        # ── Case 3: click → backend call + "still timing out" feedback ─
        # Configure the backend stub to report still-offline and add a
        # short delay so we can observe the button's "in-flight" state.
        requests.post(
            f"{base_url}/configure/retry",
            json={"online": False, "delay": 0.4},
            timeout=5,
        )
        retry.click()
        # While the probe is in flight the button must be disabled and
        # relabeled so reps can't stack rapid-fire clicks.
        expect(retry).to_be_disabled()
        expect(retry).to_have_text("Retrying…")
        # Failure feedback appears once the probe returns.
        expect(feedback).to_be_visible()
        expect(feedback).to_have_text("Still timing out")
        # And the button re-arms for the next attempt.
        expect(retry).to_be_enabled()
        expect(retry).to_have_text("Retry now")
        # Banner is still showing because the dashboard is still offline.
        expect(banner).to_be_visible()

        calls = requests.get(f"{base_url}/configure/retry", timeout=5).json()
        assert calls["calls"] == 1, "Click did not reach the backend retry endpoint"

        # ── Case 4: click → backend reports recovery → banner hides ─────
        requests.post(
            f"{base_url}/configure/retry",
            json={"online": True, "delay": 0.0},
            timeout=5,
        )
        retry.click()
        # The crm_online broadcast hides the banner outright.
        expect(banner).to_be_hidden(timeout=5_000)
        expect(retry).to_be_hidden()

        calls = requests.get(f"{base_url}/configure/retry", timeout=5).json()
        assert calls["calls"] == 1, "Successful retry did not reach the backend"
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_retry_button_surfaces_throttled_response(page: Page, free_port: int) -> None:
    """When the backend coalesces a click into a recent probe (``cached: true``),
    the dashboard must tell the rep "Just checked Xs ago" instead of acting
    like the click did nothing.

    This proves the fix for task-91: the server-side cooldown is now
    legible to the user, so a throttled retry no longer looks identical
    to a fresh probe.
    """
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        page.goto(base_url)
        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        banner = page.locator("#crmBanner")
        retry = banner.locator(".crm-banner-retry")
        feedback = banner.locator(".crm-banner-retry-feedback")

        # Drive into the offline-by-timeout state so the retry button shows.
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": _MCP_TIMEOUT_REASON},
            timeout=5,
        )
        expect(retry).to_be_visible()

        # Configure the backend stub to report a coalesced/cached probe
        # result (the underlying check ran 2 seconds ago and was offline).
        requests.post(
            f"{base_url}/configure/retry",
            json={
                "online": False,
                "cached": True,
                "age_seconds": 2.0,
                "delay": 0.0,
            },
            timeout=5,
        )
        retry.click()

        # The feedback message must mention the age of the underlying
        # probe so the rep can tell their click was registered. Banner
        # stays visible because the dashboard is still offline.
        expect(feedback).to_be_visible()
        expect(feedback).to_contain_text("Just checked")
        expect(feedback).to_contain_text("2 seconds ago")
        expect(feedback).to_contain_text("still offline")
        expect(banner).to_be_visible()
        # And the button re-arms for the next attempt — the throttle is
        # a server-side concern, the UI itself shouldn't lock up.
        expect(retry).to_be_enabled()
        expect(retry).to_have_text("Retry now")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
