# Meeting Assistant

A local, real-time meeting assistant that listens to your microphone,
transcribes the conversation with OpenAI Whisper, uses Anthropic Claude
for **topic-shift detection** and **CRM entity extraction**, queries
your Salesforce org through the **Salesforce Hosted MCP Server** (the
backend connects as an MCP client over Streamable HTTP, authenticated
with an External Client App + OAuth 2.0 + PKCE), and visualises
everything on a live web dashboard that stays pinned to the current
topic.

Everything runs locally on your machine. OAuth tokens are encrypted and stored in
`meetings.db`; no audio or transcript data is sent to a remote server (except to the
OpenAI or Anthropic APIs as described below).

## How topics work

Sales calls jump between subjects — different deals, customers, and
products. The assistant treats each subject as a **topic**:

- Every new chunk of audio is sent to Claude with the current topic label
  and a rolling summary. Claude is used **only** for this — it answers
  "is this still the same topic, or has it shifted?"
- While the topic stays the same, the dashboard's transcript, entities,
  charts, and Salesforce records stay pinned. They update as new info
  arrives, but they are never blanked out mid-topic.
- When Claude reports a shift, the dashboard clears all panels, shows
  the new topic label, and renders fresh data from the next Salesforce
  query.

CRM entity extraction (customer, contact, deal amount, stage) also
runs on Claude (Haiku), so no OpenAI key is needed beyond Whisper
transcription.

## Transcription backends

Two Whisper backends are available, selected with the `WHISPER_BACKEND` env var:

| Backend | Value | Description |
| --- | --- | --- |
| OpenAI (default) | `openai` | Audio is sent to the OpenAI Whisper API. No extra install needed. Requires an internet connection and incurs per-minute API charges. |
| Local | `local` | Audio is transcribed on-device with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No audio leaves the machine after the initial model download. Free to run and works offline. |

### Setting up the local backend

1. Install faster-whisper (not included in the default `requirements.txt` to
   avoid forcing a large dependency on users who don't need it):

   ```bash
   pip install faster-whisper
   ```

2. Set the env var before starting the app:

   ```bash
   # in your .env file, or exported in the shell
   WHISPER_BACKEND=local
   ```

3. On first start the model weights are downloaded automatically from Hugging
   Face and cached in `~/.cache/huggingface/hub/` (or the path set by
   `HF_HOME`).  Subsequent starts load directly from the cache with no
   network access required.

### Choosing a model size

The `LOCAL_WHISPER_MODEL` env var controls the accuracy/speed trade-off:

| Model | Size on disk | Relative speed | Notes |
| --- | --- | --- | --- |
| `tiny` | ~75 MB | fastest | Good for testing; noticeable word errors |
| `base` | ~145 MB | fast | **Recommended default** for most laptops |
| `small` | ~465 MB | moderate | Better accuracy, still runs on CPU |
| `medium` | ~1.5 GB | slow on CPU | Noticeably better; GPU strongly recommended |
| `large-v2` / `large-v3` | ~3 GB | slowest | Near-human accuracy; requires a GPU |

For CPU-only machines `base` or `small` give the best quality-to-speed ratio.
Set `LOCAL_WHISPER_DEVICE=cuda` and `LOCAL_WHISPER_COMPUTE_TYPE=float16` to
use an NVIDIA GPU.

### Trade-offs at a glance

| | OpenAI backend | Local backend |
| --- | --- | --- |
| Network required | Yes | Only for first-run model download |
| Cost | Per-minute API charge | Free after model download |
| Accuracy | High (server-side large model) | Depends on model size |
| Latency | Network round-trip | CPU/GPU speed — can be slower than `base` on old hardware |
| Privacy | Audio sent to OpenAI | Audio never leaves the machine |

> **Note:** `OPENAI_API_KEY` is **not** required in local mode. Entity
> extraction uses the Anthropic API; only Whisper transcription needs an
> OpenAI key.

## Requirements

- Python 3.10 or newer
- A working microphone
- An Anthropic API key (Claude — topic-shift detection and entity extraction)
- An OpenAI API key (Whisper transcription only — required when `WHISPER_BACKEND=openai`, the default; not needed for the local backend)
- A Salesforce **External Client App** (ECA) with PKCE enabled
- A Salesforce **Hosted MCP Server** endpoint (Setup → MCP Servers)

On Linux you may need PortAudio system libraries for microphone capture:

```bash
# Debian / Ubuntu
sudo apt-get install -y portaudio19-dev libsndfile1

# macOS (Homebrew)
brew install portaudio libsndfile
```

## Setup

### One-click setup (recommended)

Run a single script and follow the prompts — it installs system audio libraries
(where possible), creates a virtual environment, installs all Python
dependencies, and walks you through filling in every credential.

**macOS / Linux**

```bash
git clone <your-fork-url>
cd meeting-assistant
bash setup.sh
```

**Windows (PowerShell)**

```powershell
git clone <your-fork-url>
cd meeting-assistant
.\setup.ps1
```

> **Windows note:** If you see an execution-policy error, run this once in an
> Administrator PowerShell window first:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

After the script finishes, start the app with:

```bash
# macOS / Linux
source .venv/bin/activate
python app.py

# Windows
.\.venv\Scripts\Activate.ps1
python app.py
```

---

### Manual setup

```bash
git clone <your-fork-url>
cd meeting-assistant
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env
# edit .env and fill in your keys
```

### Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key — Claude powers both topic-shift detection and CRM entity extraction |
| `OPENAI_API_KEY` | openai backend only | OpenAI API key — Whisper transcription only. Required when `WHISPER_BACKEND=openai` (the default). **Not required** when `WHISPER_BACKEND=local` |
| `SF_CLIENT_ID` | yes | Salesforce **External Client App** Consumer Key |
| `SF_CLIENT_SECRET` | no | External Client App Consumer Secret. **Leave blank for a public ECA** (recommended) — PKCE proves the client's identity instead. Provide only for confidential ECAs |
| `SF_MCP_SERVER_URL` | yes | Salesforce **Hosted MCP Server** endpoint (Streamable HTTP). The backend connects here as an MCP client to run all SOQL/CRM queries |
| `SF_MCP_SCOPES` | no | OAuth scopes requested during the authorize step (default `api refresh_token offline_access`). Adjust if your org requires extra scopes such as `mcp` |
| `SF_LOGIN_URL` | no | `https://login.salesforce.com` (default) for production orgs, `https://test.salesforce.com` for sandboxes. Also reads legacy `SF_DOMAIN=test` for backwards compatibility |
| `ENCRYPTION_KEY` | yes | Arbitrary secret used to encrypt OAuth tokens at rest. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Changing this value invalidates stored tokens |
| `HOST` | no | Bind address for the web server (default `127.0.0.1`) |
| `PORT` | no | Port for the web server (default `8000`) |
| `AUDIO_CHUNK_SECONDS` | no | Seconds of audio per Whisper request (default `5`) |
| `AUDIO_SAMPLE_RATE` | no | Mic sample rate in Hz (default `16000`) |
| `WHISPER_BACKEND` | no | `openai` (default) or `local` — see [Transcription backends](#transcription-backends) |
| `LOCAL_WHISPER_MODEL` | no | Model size for the local backend: `tiny`, `base` (default), `small`, `medium`, `large-v2`, `large-v3` |
| `LOCAL_WHISPER_DEVICE` | no | `cpu` (default) or `cuda` for the local backend |
| `LOCAL_WHISPER_COMPUTE_TYPE` | no | Quantisation type for the local backend: `int8` (default, CPU), `float16` (GPU) |
| `LOG_LEVEL` | no | Logging verbosity: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. Each log line carries a per-chunk `request_id` to trace one audio chunk end-to-end |
| `SF_SESSION_TIMEOUT_MINUTES` | no | Salesforce idle timeout in minutes (default `30`). After this much idle time the next CRM query proactively refreshes the OAuth token |
| `SF_MCP_TIMEOUT_SECONDS` | no | Maximum seconds to wait for any single Salesforce Hosted MCP Server request — warm-up, SOQL query, or describe (default `30`). On timeout the dashboard shows a red "Salesforce MCP server timed out" banner and CRM data falls back to empty, so a hung MCP server can never freeze "Connect to Salesforce" |
| `SKIP_STARTUP_VALIDATION` | no | Set to `1` to skip the Anthropic credential ping at startup (default `0`). Salesforce is validated through the MCP + OAuth UI flow — not at boot |

### Salesforce External Client App + Hosted MCP Server setup

Starting in **V6.0.0**, the backend connects to Salesforce through the
**Salesforce Hosted MCP Server** as an MCP client. Authentication uses an
**External Client App (ECA)** with **OAuth 2.0 + PKCE** — no Connected App,
no client secret required for the recommended public ECA configuration.

1. In Salesforce Setup, go to **External Client Apps → New External Client App**.
2. Enable **OAuth Settings** and set the callback URL to
   `http://localhost:8000/oauth/callback` (adjust the port if you changed `PORT`).
3. Enable **Require Proof Key for Code Exchange (PKCE)** under OAuth Policies.
4. Add the OAuth scopes: **api**, **refresh_token**, **offline_access**.
5. **Recommended (public ECA):** disable "Require Secret for Web Server Flow"
   and leave `SF_CLIENT_SECRET` blank in your `.env`. PKCE proves the client's
   identity, so a long-lived shared secret is no longer required.
   *Confidential ECA:* if your security policy requires it, leave the secret
   requirement enabled and paste the Consumer Secret into `SF_CLIENT_SECRET`.
6. Copy the **Consumer Key** → `SF_CLIENT_ID` in your `.env`.
7. In Setup → **MCP Servers**, copy the **Hosted MCP Server endpoint URL** →
   `SF_MCP_SERVER_URL` in your `.env`.
8. Start the app and click **Connect to Salesforce** in the browser to authorize.

## Run

```bash
python app.py
```

Open <http://127.0.0.1:8000> in your browser. As soon as the page loads
it opens a WebSocket back to the server and you'll see:

- The current topic label in the header (lights up once detected)
- A live transcript that updates every few seconds
- The merged entities for the current topic
- A pie chart showing the distribution of matching Opportunity stages
- A line graph of matching Opportunity amounts over time
- Lists of matching Salesforce Accounts and Opportunities

When Claude detects a new topic, the panels clear and a "New topic:"
banner appears at the top of the transcript.

Stop the server with `Ctrl+C`.

## How it works

```
mic → backend/audio.py → backend/transcribe.py     (Whisper)
                       → backend/context.py        (Claude — topic shift?)
                       → backend/entities.py       (Claude Haiku — extract CRM entities)
                       → backend/topic_state.py    (merge into current topic)
                       → backend/mcp_client.py        (Hosted Salesforce MCP Server, OAuth 2.0 + PKCE)
                       → backend/hub.py → WebSocket → frontend/
```

Every transcript chunk first runs through context detection. If Claude
reports a shift, the topic state resets and a `topic_shift` event is
broadcast. Otherwise entities are merged into the current topic and
Salesforce is re-queried only when the merged entities change. The
frontend ignores stale events tagged with an older topic to keep the
current view pinned.

## Project layout

```
meeting-assistant/
├── app.py                    FastAPI server + capture/transcribe pipeline
├── backend/
│   ├── audio.py              Microphone capture (sounddevice)
│   ├── transcribe.py         OpenAI Whisper wrapper
│   ├── context.py            Anthropic Claude — topic-shift detection
│   ├── entities.py           Anthropic Claude Haiku — CRM entity extraction
│   ├── topic_state.py        In-memory current-topic state
│   ├── mcp_client.py         Salesforce Hosted MCP Server client (SOQL via MCP, OAuth refresh, status callbacks)
│   ├── salesforce_client.py  Pure helpers: stage distribution, amount timeline, CrmResult shape
│   ├── pkce.py               PKCE verifier/challenge + OAuth state generation (RFC 7636)
│   ├── oauth.py              ECA OAuth helpers: authorize URL, code exchange, token refresh
│   ├── token_store.py        Fernet-encrypted SQLite store for access + refresh tokens
│   ├── hub.py                WebSocket broadcast hub with per-client backpressure
│   ├── log_utils.py          Structured logging + per-chunk request_id propagation
│   └── config.py             Env-var loading + startup credential validation
├── frontend/
│   ├── index.html            Dashboard markup (loads modules/main.js as ES module)
│   ├── styles.css            Dashboard styling
│   └── modules/              ES6 modules: state, dom, utils, charts, entities,
│       │                     transcript, history, settings, document, events,
│       │                     crm_banner, websocket, demo, main
├── requirements.txt          Pinned Python dependencies
├── .env.example              Template for required environment variables
└── README.md
```

## Troubleshooting

- **"Missing required environment variable"** — copy `.env.example` to
  `.env` and fill in the missing key.
- **No microphone detected / PortAudio errors** — install the system
  PortAudio package as noted above and ensure your OS allows mic access
  for the terminal.
- **Salesforce authorization required** — open the app in a browser and
  click **Connect to Salesforce**. You'll be redirected to Salesforce to
  authorize. Sandbox orgs: set `SF_LOGIN_URL=https://test.salesforce.com`.
- **Topic never shifts** — Claude treats small tangents as "same topic"
  on purpose. Shifts happen when the subject (customer, deal, product)
  clearly changes.
- **Empty charts** — the dashboard only shows data when entities match
  Accounts or Opportunities in your org.
- **Salesforce went offline mid-meeting** — the dashboard surfaces a
  red "Salesforce is offline" banner at the top of the page and dims
  the CRM panels. Whisper transcription and Claude topic-shift
  detection keep running. The banner clears automatically once the
  next CRM query succeeds.
- **WebSocket dropped** — the status badge shows "Reconnecting…" with
  exponential backoff (1s → 30s) until the server is reachable again.
  Cached topics, entities, and CRM data stay on screen the entire
  time; nothing is lost across a brief disconnect.

## Operational notes

- **Per-chunk request IDs.** Every log line emitted while processing a
  single audio chunk carries the same short `request_id`, so you can
  `grep` one chunk's full trace through transcribe → topic-shift →
  entities → Salesforce.
- **Backpressure.** Each WebSocket client has a bounded send queue
  (depth 50). If a client is slow, the oldest *non-critical* event is
  dropped; `topic_shift`, `error`, `crm_offline`, `crm_online`, and
  document lifecycle events are always preserved.
- **Startup validation.** On boot the app pings Anthropic. An invalid
  Anthropic key aborts startup; Salesforce is validated lazily through
  the OAuth UI flow so the meeting can still be transcribed even before
  authorization. Set `SKIP_STARTUP_VALIDATION=1` to bypass the Anthropic
  check (useful for offline development).
- **MCP request timeouts.** Every Salesforce Hosted MCP Server request —
  warm-up, SOQL query, describe — is bounded by `SF_MCP_TIMEOUT_SECONDS`
  (default 30s). If the server hangs or stops responding, the request is
  cancelled, the dashboard's red Salesforce-offline banner shows
  "Salesforce MCP server timed out", and the CRM panel renders empty
  data instead of leaving a spinner running. Tune the timeout up for
  slow links or down for tighter SLAs.
