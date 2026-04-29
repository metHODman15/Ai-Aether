"""End-to-end test: Salesforce-offline banner "Reconnecting…" hint.

Verifies the small reassurance UI added to the CRM-offline banner so reps
can tell the dashboard is actively re-checking Salesforce after an MCP
timeout (and is not stuck or hung).

Covers all three "done looks like" criteria from task-85:
  1. The hint appears when the offline reason is the MCP timeout.
  2. The hint disappears the moment the dashboard flips back online.
  3. The hint does NOT appear for other offline reasons (auth, etc.) —
     those need explicit user action, not waiting.
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
# the string the dashboard discriminates on to show the hint.
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


def test_reconnecting_hint_lifecycle(page: Page, free_port: int) -> None:
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        page.goto(base_url)

        banner = page.locator("#crmBanner")
        hint = banner.locator(".crm-banner-reconnecting")

        # Wait for the WebSocket to connect so injected events are
        # delivered to this browser session.
        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        # ── Case 1: MCP timeout offline → hint visible ──────────────────
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": _MCP_TIMEOUT_REASON},
            timeout=5,
        )
        expect(banner).to_be_visible()
        expect(hint).to_be_visible()
        expect(hint).to_contain_text("Reconnecting")

        # ── Case 2: back online → hint hidden, banner hidden ────────────
        requests.post(f"{base_url}/inject/crm", json={"online": True}, timeout=5)
        expect(banner).to_be_hidden()
        expect(hint).to_be_hidden()

        # ── Case 3: non-timeout offline reason → banner shown, hint hidden
        # auth_required-style reasons need an explicit reconnect click,
        # not a waiting hint.
        requests.post(
            f"{base_url}/inject/crm",
            json={"online": False, "reason": "auth_required"},
            timeout=5,
        )
        expect(banner).to_be_visible()
        expect(hint).to_be_hidden()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# Exact rep-facing strings rendered by the banner. These mirror the
# `REASON_MESSAGES` mapping (and the `DEFAULT_OFFLINE_MESSAGE` fallback)
# in frontend/modules/crm_banner.js. They are duplicated here on purpose:
# if either side is edited without intent, this test fails loudly so the
# friendly copy can't quietly regress back to a raw backend reason like
# "mcp_tools_unavailable".
_FRIENDLY_TIMEOUT = (
    "Salesforce is slow to respond — we're retrying in the background."
)
_FRIENDLY_AUTH_REQUIRED = (
    "Salesforce needs you to sign in again to load CRM data."
)
_FRIENDLY_TOOLS_UNAVAILABLE = (
    "Salesforce is connected, but its CRM tools aren't responding right now."
)
_FRIENDLY_DEFAULT = "Salesforce is offline — CRM data is unavailable."


def test_friendly_copy_for_each_reason(page: Page, free_port: int) -> None:
    """The banner shows the exact rep-friendly wording for each known
    backend reason, and falls back to the generic copy for an unknown
    reason — never leaking the raw backend string into the UI.
    """
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        page.goto(base_url)

        banner = page.locator("#crmBanner")
        text_el = banner.locator(".crm-banner-text")

        # Wait for the WebSocket to connect so injected events are
        # delivered to this browser session.
        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        # Each case: (injected backend reason, exact rep-facing string).
        # The unknown reason is deliberately something the mapping does
        # not know about, so it must fall through to the generic copy
        # instead of being shown verbatim.
        cases = [
            (_MCP_TIMEOUT_REASON, _FRIENDLY_TIMEOUT),
            ("auth_required", _FRIENDLY_AUTH_REQUIRED),
            ("mcp_tools_unavailable", _FRIENDLY_TOOLS_UNAVAILABLE),
            ("some_unknown_backend_reason", _FRIENDLY_DEFAULT),
        ]

        for reason, expected_text in cases:
            requests.post(
                f"{base_url}/inject/crm",
                json={"online": False, "reason": reason},
                timeout=5,
            )
            expect(banner).to_be_visible()
            # `to_have_text` asserts the *exact* trimmed text content of
            # the element, which is what we want — any tweak to the
            # mapping (or accidental fall-through to the raw reason)
            # will fail this assertion clearly.
            expect(text_el).to_have_text(expected_text)

            # Flip back online between cases so each assertion starts
            # from a clean slate and we know the next text change was
            # caused by the new offline reason, not stale state.
            requests.post(
                f"{base_url}/inject/crm", json={"online": True}, timeout=5
            )
            expect(banner).to_be_hidden()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_reauth_link_visibility_per_reason(page: Page, free_port: int) -> None:
    """The ``Reconnect`` link in the offline banner must only appear when
    the rep actually has to re-authenticate — never as a side effect of
    an unrelated offline reason that happens to share letters with
    "auth".

    Today the visibility rule is a substring check
    (``reason.includes("auth")`` in ``crm_banner.js``), which works for
    the current set of reasons but is fragile: a future backend reason
    that happens to contain "auth" — or a typo like "authrequired" —
    could quietly start sending reps to the sign-in flow when they don't
    need to be there, or hide the link when they do. Pinning the
    expected per-reason visibility here makes any such drift fail
    loudly, even if the underlying check is later replaced with
    something stricter.
    """
    port = free_port
    base_url = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None

    try:
        proc = _start_server(port)
        _wait_for_server(port)

        page.goto(base_url)

        banner = page.locator("#crmBanner")
        reauth = banner.locator(".crm-banner-reauth")

        # Wait for the WebSocket to connect so injected events are
        # delivered to this browser session.
        expect(page.locator("#status")).to_have_text("Connected", timeout=10_000)

        # Each case: (injected backend reason, whether the Reconnect
        # link is currently visible). Only ``auth_required`` is a true
        # re-auth situation; the timeout reason is handled by the
        # background recovery probe, the tools-unavailable reason needs
        # a backend fix rather than a sign-in, and a generic unknown
        # reason defaults to *hidden* so we never silently send reps to
        # the OAuth flow for a cause we don't recognise.
        #
        # The final case (``"oauth_glitch"``) is deliberately an
        # auth-substring-but-not-canonical reason: today the banner
        # uses ``reason.includes("auth")`` so this currently *shows*
        # the link, even though the rep doesn't actually need to
        # re-authenticate. We pin the current behaviour here on purpose
        # — when the substring rule is later replaced with a stricter
        # check, this assertion will fail and force the fix author to
        # update it intentionally to ``False``, instead of the change
        # silently flipping rep-facing behaviour.
        cases = [
            (_MCP_TIMEOUT_REASON, False),
            ("auth_required", True),
            ("mcp_tools_unavailable", False),
            ("some_unknown_backend_reason", False),
            ("oauth_glitch", True),
        ]

        for reason, should_be_visible in cases:
            requests.post(
                f"{base_url}/inject/crm",
                json={"online": False, "reason": reason},
                timeout=5,
            )
            expect(banner).to_be_visible()
            if should_be_visible:
                expect(reauth).to_be_visible()
            else:
                expect(reauth).to_be_hidden()

            # Flip back online between cases so each assertion starts
            # from a clean slate and any stale link visibility from the
            # previous reason can't leak into the next assertion.
            requests.post(
                f"{base_url}/inject/crm", json={"online": True}, timeout=5
            )
            expect(banner).to_be_hidden()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
