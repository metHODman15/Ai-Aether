"""pytest-playwright configuration for the meeting-assistant e2e suite.

Redirects Playwright to the Nix-installed system Chromium so the browser
binary is already linked against the correct library paths.  Falls back to
Playwright's bundled binary when no system browser is found.
"""
from __future__ import annotations

import shutil
import pytest


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    exe = shutil.which("chromium") or shutil.which("chromium-browser")
    if exe:
        return {**browser_type_launch_args, "executable_path": exe}
    return browser_type_launch_args
