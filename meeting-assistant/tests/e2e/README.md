# End-to-end tests

These tests exercise the dashboard in a real browser via [Playwright]. They
stand up the FastAPI app in a background asyncio task, point Playwright at it
on a free port, and assert on rendered DOM after WebSocket events fire.

They're slow (browser launch ~1–2 s, plus per-test page loads), they need a
Chromium binary on the machine, and they only cover behaviour you genuinely
can't observe at the unit/integration level (e.g. CRM-offline banner CSS,
WebSocket reconnect UI). For those reasons they're **excluded from the default
`pytest` run** and have to be opted into explicitly.

## Run-book

```bash
# 1. Install dev dependencies (once)
pip install -r requirements-dev.txt

# 2. Install a Chromium binary for Playwright (once)
#    On Nix-based environments (Replit, NixOS) the conftest will pick up the
#    system `chromium` automatically — you can skip this step there.
playwright install chromium

# 3. Run only the e2e suite
pytest tests/e2e/

# Run a single file
pytest tests/e2e/test_crm_offline_banner.py

# Run everything (unit + integration + e2e)
pytest tests/ tests/e2e/
```

## How the suite is wired

- `conftest.py` overrides Playwright's `browser_type_launch_args` to prefer a
  system-installed Chromium (`shutil.which("chromium")`). This keeps the suite
  working inside Nix-based environments without re-downloading the bundled
  browser. If no system Chromium is present, Playwright falls back to its own
  binary (which is what `playwright install chromium` provides).
- Each test that needs a backend launches a stripped-down FastAPI server via
  `tests/helpers/minimal_server.py` (it serves the frontend, exposes the real
  WebSocket hub, and adds a couple of test-only `/inject/...` routes for
  triggering events). No real Salesforce / OpenAI / Anthropic credentials are
  required — the helper short-circuits everything except the dashboard surface
  the test exercises.
- `pyproject.toml` excludes `tests/e2e/` from the default `pytest` collect via
  `addopts = "--ignore=tests/e2e"`, which is why a bare `pytest` skips them.

## Troubleshooting

- **`playwright._impl._errors.Error: Executable doesn't exist…`** — you skipped
  `playwright install chromium`. Run it (or install system Chromium).
- **Tests pass locally but hang in CI** — the app's lifespan task keeps
  recovery probes running; tests use a stub Salesforce client to short-circuit
  them. If you wire in a real client by accident, those probes can hold the
  event loop open and tests will time out at teardown.
- **Port already in use** — the suite picks a free port per test; if a previous
  run crashed, kill any leftover `python app.py` / `uvicorn` processes.

[Playwright]: https://playwright.dev/python/
