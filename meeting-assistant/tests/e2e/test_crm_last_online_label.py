"""End-to-end test: "Last connected …" label on the CRM-offline banner.

Verifies the small relative-timestamp line added to the offline banner so
reps can gauge how stale the greyed-out CRM panels are during an outage.

Covers all three "done looks like" criteria from task-87:
  1. The label appears under the offline banner once Salesforce has been
     observed online at least once this session.
  2. The label updates live while the banner is visible (the test waits
     long enough for the displayed minutes count to advance).
  3. The label is hidden when the dashboard has never been online in
     this session (cold-load straight into an offline event).
"""
from __future__ import annotations

import os
import re
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


def test_last_connected_label_appears_after_online_event(
    page: Page, free_port: int
) -> None:
    """Online → offline shows "Last connected …" line under the banner."""
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        page.goto(base_url)

        banner = page.locator("#crmBanner")
        last_online = banner.locator(".crm-banner-last-online")

        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        # Confirm Salesforce was reachable this session by broadcasting a
        # crm_online event — this seeds state.crmLastOnlineAt.
        requests.post(f"{base_url}/inject/crm", json={"online": True}, timeout=5)
        # Banner stays hidden while online.
        expect(banner).to_be_hidden()

        # Now go offline. The "Last connected …" line should appear.
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": _MCP_TIMEOUT_REASON},
            timeout=5,
        )
        expect(banner).to_be_visible()
        expect(last_online).to_be_visible()
        # Just-online → "just now" copy. Matches formatLastOnline().
        expect(last_online).to_contain_text("Last connected")
        expect(last_online).to_contain_text("just now")

        # Going back online hides the line so it doesn't carry over.
        requests.post(f"{base_url}/inject/crm", json={"online": True}, timeout=5)
        expect(banner).to_be_hidden()
        expect(last_online).to_be_hidden()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_last_connected_label_hidden_when_never_online(
    page: Page, free_port: int
) -> None:
    """Cold-load straight into offline → label stays hidden (no signal)."""
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        page.goto(base_url)

        banner = page.locator("#crmBanner")
        last_online = banner.locator(".crm-banner-last-online")

        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        # Skip the crm_online event — go straight to offline. The page has
        # never observed Salesforce as reachable, so the label must stay
        # hidden even though the offline banner itself shows.
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": _MCP_TIMEOUT_REASON},
            timeout=5,
        )
        expect(banner).to_be_visible()
        expect(last_online).to_be_hidden()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_last_connected_label_updates_live(page: Page, free_port: int) -> None:
    """While banner is visible, the label ticks from "just now" → "1 min ago".

    Uses page.clock to fast-forward virtual time so the test doesn't have
    to wait a real minute. Confirms the banner doesn't render a stale
    snapshot during a longer outage.
    """
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        # Install the virtual clock before navigation so Date.now() and
        # setInterval inside the page run on test-controlled time from
        # the very first script.
        page.clock.install()
        page.goto(base_url)

        banner = page.locator("#crmBanner")
        last_online = banner.locator(".crm-banner-last-online")

        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        # Seed an "online" observation, then go offline. Because we
        # injected crm_online first, crmLastOnlineAt is set to virtual
        # "now"; the offline event flips the banner on with "just now".
        requests.post(f"{base_url}/inject/crm", json={"online": True}, timeout=5)
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": _MCP_TIMEOUT_REASON},
            timeout=5,
        )
        expect(banner).to_be_visible()
        expect(last_online).to_contain_text("just now")

        # Fast-forward 75 virtual seconds (past the 60s "just now" cliff
        # and at least one 15s ticker interval). The label should
        # re-render to a "1 min ago" line on its own.
        page.clock.fast_forward(75_000)
        expect(last_online).to_contain_text(re.compile(r"1\s*min ago"))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
