# Ai-Aether — AI-Powered Real-Time Meeting Assistant

  Ai-Aether listens to your microphone during sales calls, transcribes speech in real time with OpenAI Whisper, uses **Anthropic Claude exclusively** to detect when the conversation topic shifts, extracts CRM entities (customers, contacts, deal amounts, stages) with OpenAI, queries Salesforce for live account and opportunity data, and renders everything on a live dashboard with Chart.js charts — all pinned to the current meeting topic.

  A built-in **demo / mock mode** lets you try the full experience in any browser with no API keys or microphone required.

  ---

  ## Table of Contents

  1. [Features](#features)
  2. [Requirements](#requirements)
  3. [Quick Start](#quick-start)
  4. [Environment Variables](#environment-variables)
  5. [Running the App](#running-the-app)
  6. [Using the Dashboard](#using-the-dashboard)
  7. [Demo Mode](#demo-mode)
  8. [Document Upload](#document-upload)
  9. [Transcription Backends](#transcription-backends)
  10. [Project Layout](#project-layout)
  11. [Troubleshooting](#troubleshooting)

  ---

  ## Features

  - **Live transcription** — microphone audio chunked every few seconds and sent to Whisper (OpenAI API or fully local via faster-whisper)
  - **Topic-shift detection** — Claude reads each transcript chunk with the current topic label; when the subject changes it broadcasts a `topic_shift` event and the dashboard resets to the new topic
  - **CRM entity extraction** — OpenAI extracts customer names, contact names, deal amounts, and pipeline stages from the transcript
  - **Salesforce integration** — matching Accounts and Opportunities are fetched via the Salesforce REST API in real time
  - **Live charts** — a pie chart of Opportunity stages and a line chart of deal amounts, both scoped to the current topic
  - **Document analysis** — upload meeting minutes or briefing notes (PDF, DOCX, or TXT); each paragraph is analysed for entities and queried against Salesforce, with per-unit charts rendered as results arrive
  - **Demo mode** — fully scripted client-side simulation of a 3-topic call plus a document upload, runnable with zero backend dependencies
  - **Settings persistence** — API keys and Salesforce credentials are editable in the UI and saved across sessions

  ---

  ## Requirements

  - Python **3.10** or newer
  - A working microphone (not needed for demo mode)
  - API keys / credentials (not needed for demo mode):
    - [OpenAI](https://platform.openai.com/) — entity extraction + Whisper (default backend)
    - [Anthropic](https://console.anthropic.com/) — Claude, used only for topic-shift detection
    - Salesforce username, password, and security token

  ### System audio libraries (Linux / macOS only)

  ```bash
  # Debian / Ubuntu
  sudo apt-get install -y portaudio19-dev libsndfile1

  # macOS (Homebrew)
  brew install portaudio libsndfile
  ```

  ---

  ## Quick Start

  ### macOS / Linux — one command

  ```bash
  git clone https://github.com/metHODman15/Ai-Aether.git
  cd Ai-Aether/meeting-assistant
  bash setup.sh
  ```

  The script installs system audio libraries (where possible), creates a Python virtual environment, installs all dependencies, and walks you through filling in every credential interactively.

  ### Windows (PowerShell)

  ```powershell
  git clone https://github.com/metHODman15/Ai-Aether.git
  cd Ai-Aether\meeting-assistant
  .\setup.ps1
  ```

  > If you see an execution-policy error, run this once in an Administrator PowerShell window first:
  > `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

  ### Manual setup

  ```bash
  git clone https://github.com/metHODman15/Ai-Aether.git
  cd Ai-Aether/meeting-assistant

  # Create and activate a virtual environment (recommended)
  python -m venv .venv
  source .venv/bin/activate          # macOS / Linux
  # .venv\Scripts\Activate.ps1      # Windows PowerShell

  pip install -r requirements.txt

  # Copy the environment template and fill in your credentials
  cp .env.example .env
  ```

  Open `.env` in any text editor and add your keys (see [Environment Variables](#environment-variables) below).

  ---

  ## Environment Variables

  | Variable | Required | Description |
  |---|---|---|
  | `OPENAI_API_KEY` | yes | OpenAI key — entity extraction; also Whisper when `WHISPER_BACKEND=openai` |
  | `ANTHROPIC_API_KEY` | yes | Anthropic key — Claude, topic-shift detection only |
  | `SF_USERNAME` | yes | Salesforce login email |
  | `SF_PASSWORD` | yes | Salesforce password |
  | `SF_SECURITY_TOKEN` | yes | Salesforce security token (emailed to you by Salesforce) |
  | `SF_DOMAIN` | no | `login` (default) for production orgs, `test` for sandboxes |
  | `HOST` | no | Server bind address (default `127.0.0.1`) |
  | `PORT` | no | Server port (default `8000`) |
  | `AUDIO_CHUNK_SECONDS` | no | Seconds of audio per Whisper request (default `5`) |
  | `AUDIO_SAMPLE_RATE` | no | Microphone sample rate in Hz (default `16000`) |
  | `WHISPER_BACKEND` | no | `openai` (default) or `local` — see [Transcription Backends](#transcription-backends) |
  | `LOCAL_WHISPER_MODEL` | no | `tiny` / `base` (default) / `small` / `medium` / `large-v2` / `large-v3` |
  | `LOCAL_WHISPER_DEVICE` | no | `cpu` (default) or `cuda` for the local backend |
  | `LOCAL_WHISPER_COMPUTE_TYPE` | no | `int8` (CPU default) or `float16` (GPU) |

  > Credentials can also be updated at any time through the **Settings** panel in the dashboard UI.

  ---

  ## Running the App

  ```bash
  # Make sure your virtual environment is active
  source .venv/bin/activate          # macOS / Linux
  # .venv\Scripts\Activate.ps1      # Windows

  python app.py
  ```

  Open **<http://127.0.0.1:8000>** in your browser.  
  Stop the server at any time with `Ctrl+C`.

  ---

  ## Using the Dashboard

  Once the app is running and the browser is open:

  1. **Start recording** — click the microphone button. The status bar turns green and audio capture begins immediately.
  2. **Live transcript** — the panel on the left fills in as Whisper processes each audio chunk (every few seconds by default).
  3. **Topic label** — shown in the header. When Claude detects a new subject, a *"New topic:"* banner appears and all panels reset.
  4. **Entities panel** — customer name, contact, deal amount, and pipeline stage extracted by OpenAI from the current topic's transcript.
  5. **Salesforce panel** — matching Accounts and Opportunities fetched live from your org.
  6. **Charts** — a pie chart of Opportunity stages and a line chart of deal amounts, both pinned to the current topic.
  7. **Settings** — the gear icon opens a sidebar where you can update API keys and Salesforce credentials without restarting the server.
  8. **History** — the clock icon shows past topics from the current session.
  9. **Stop recording** — click the microphone button again; the pipeline pauses.

  ---

  ## Demo Mode

  No API keys? No microphone? No problem.

  When the app loads and no WebSocket connection can be established (or any time you like), click **"Try Demo"** in the header or the **"▶ Start Demo"** banner that appears on the dashboard.

  The demo runs a 30-second scripted simulation entirely in the browser:

  | Time | Event |
  |---|---|
  | 0 – 10 s | Topic: *Q2 Sales Pipeline* — transcript, entity, and CRM events fill in |
  | 10 – 20 s | Topic shift → *Product Roadmap* |
  | 20 – 30 s | Topic shift → *MegaCorp Renewal* |
  | ~26 s | Simulated document upload — 4 per-unit analysis cards with charts |

  Click **"Stop Demo"** at any time to exit. The demo never touches any backend or external API.

  ---

  ## Document Upload

  Upload meeting minutes, briefing notes, or any reference document to get a per-paragraph CRM analysis.

  1. Click the **"Upload Minutes"** button in the header (or wait for the auto-trigger in demo mode).
  2. Select a **PDF**, **DOCX**, or **TXT** file (max 5 MB).
  3. The dashboard switches to **Document mode** and shows a progress bar as results arrive over WebSocket.
  4. Each paragraph (unit) gets its own card with:
     - The raw text
     - Extracted entities (customer, contact, deal amount, stage)
     - Matching Salesforce Accounts and Opportunities
     - A pie chart of Opportunity stages
     - A line chart of deal amounts
  5. Click **"Back to Live"** to return to the real-time microphone view.

  **Supported formats:**

  | Format | Notes |
  |---|---|
  | TXT | Split on blank lines; Windows (CRLF) and Unix (LF) line endings both supported |
  | DOCX | Each non-empty paragraph becomes one unit |
  | PDF | Text extracted page-by-page and split on blank lines |

  ---

  ## Transcription Backends

  ### OpenAI (default)

  Audio is sent to the OpenAI Whisper API. No local setup required beyond the API key. Incurs per-minute charges.

  ### Local (faster-whisper)

  Audio is transcribed entirely on-device — no audio data ever leaves your machine after the first model download.

  **Install:**

  ```bash
  pip install faster-whisper
  ```

  **Enable in `.env`:**

  ```
  WHISPER_BACKEND=local
  LOCAL_WHISPER_MODEL=base    # see table below
  ```

  | Model | Disk size | Speed | Notes |
  |---|---|---|---|
  | `tiny` | ~75 MB | fastest | Good for testing; higher error rate |
  | `base` | ~145 MB | fast | **Recommended for most laptops (CPU)** |
  | `small` | ~465 MB | moderate | Better accuracy, still CPU-friendly |
  | `medium` | ~1.5 GB | slow on CPU | GPU strongly recommended |
  | `large-v2` / `large-v3` | ~3 GB | slowest | Near-human accuracy; requires a GPU |

  Add `LOCAL_WHISPER_DEVICE=cuda` and `LOCAL_WHISPER_COMPUTE_TYPE=float16` to use an NVIDIA GPU.

  > `OPENAI_API_KEY` is still required in local mode because entity extraction always uses the OpenAI chat API.

  ---

  ## Project Layout

  ```
  Ai-Aether/
  └── meeting-assistant/
      ├── app.py                    FastAPI server + capture/transcribe pipeline
      ├── backend/
      │   ├── audio.py              Microphone capture (sounddevice)
      │   ├── config.py             Env-var loading and validation
      │   ├── context.py            Anthropic Claude — topic-shift detection
      │   ├── document_parser.py    PDF / DOCX / TXT parsing and paragraph splitting
      │   ├── entities.py           OpenAI — CRM entity extraction
      │   ├── hub.py                WebSocket broadcast hub
      │   ├── salesforce_client.py  Salesforce REST queries and aggregations
      │   ├── store.py              In-memory history and settings store
      │   ├── topic_state.py        In-memory current-topic state
      │   └── transcribe.py         Whisper wrapper (OpenAI + local)
      ├── frontend/
      │   ├── index.html            Dashboard markup
      │   ├── styles.css            Dashboard styling
      │   └── app.js                WebSocket client, Chart.js charts, demo mode
      ├── tests/
      │   ├── test_meeting_store.py
      │   └── test_settings_persistence.py
      ├── requirements.txt          Pinned Python dependencies
      ├── .env.example              Credential template
      ├── setup.sh                  One-click setup (macOS / Linux)
      └── setup.ps1                 One-click setup (Windows)
  ```

  ---

  ## Troubleshooting

  | Problem | Fix |
  |---|---|
  | *"Missing required environment variable"* | Copy `.env.example` → `.env` and fill in the missing key, or use the Settings panel in the UI. |
  | *No microphone / PortAudio errors* | Install the PortAudio system package (see [Requirements](#requirements)) and confirm your OS allows terminal microphone access. |
  | *Salesforce auth fails* | Reset your security token in Salesforce under **My Personal Information → Reset My Security Token**. Sandbox orgs need `SF_DOMAIN=test`. |
  | *Topic never shifts* | Claude treats small tangents as "same topic" by design. Shifts happen when the subject (customer, deal, or product) clearly changes. |
  | *Charts are empty* | The dashboard only shows data when extracted entities match Accounts or Opportunities in your Salesforce org. |
  | *Whisper is very slow* | Switch to the local backend with a smaller model (`tiny` or `base`) or reduce `AUDIO_CHUNK_SECONDS`. |
  | *Windows execution-policy error* | Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` in an Administrator PowerShell, then retry `setup.ps1`. |

  ---

  ## How It Works

  ```
  mic → audio.py → transcribe.py       Whisper (OpenAI API or local faster-whisper)
                → context.py           Claude — "same topic or new topic?"
                → entities.py          OpenAI — extract CRM entities
                → topic_state.py       Merge entities into current topic
                → salesforce_client.py Salesforce REST API queries
                → hub.py               WebSocket broadcast → frontend
  ```

  Every audio chunk first runs through Claude's topic detection. On a shift, the topic state resets, a `topic_shift` event is broadcast, and the dashboard clears. Otherwise entities are merged and Salesforce is re-queried only when the merged entities change, so the current-topic view stays pinned and stable.

  ---

  *Built with FastAPI · Anthropic Claude · OpenAI Whisper · Salesforce REST API · Chart.js*
  