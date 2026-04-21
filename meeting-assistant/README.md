# Meeting Assistant

A local, real-time meeting assistant that listens to your microphone,
transcribes the conversation with local Whisper (faster-whisper), uses
Anthropic Claude for **both topic-shift detection and CRM entity
extraction**, queries your Salesforce org through the REST API, and
visualises everything on a live web dashboard that stays pinned to the
current topic.

Only one API key is required: your **Anthropic API key**. No OpenAI key needed.

Everything runs locally on your machine. No data is stored on disk.

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

CRM entity extraction (customer, contact, deal amount, stage) also runs
through Claude (`claude-haiku-4-5`), reusing the same Anthropic API key.

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

> **Note:** `OPENAI_API_KEY` is not required. Both topic-shift detection and
> CRM entity extraction use the Anthropic API (`ANTHROPIC_API_KEY`).

## Requirements

- Python 3.10 or newer
- A working microphone
- An **Anthropic API key** (Claude — topic-shift detection and entity extraction)
- Salesforce username, password, and security token

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
cd Ai-Aether/meeting-assistant
bash setup.sh
```

**Windows (PowerShell)**

```powershell
git clone <your-fork-url>
cd Ai-Aether\meeting-assistant
.\setup.ps1
```

> **Windows note:** If you see an execution-policy error, run this once in an
> Administrator PowerShell window first:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

After the script finishes, start the app with:

```bash
# macOS / Linux (run from Ai-Aether root)
source .venv/bin/activate
python3 meeting-assistant/app.py

# Windows (run from Ai-Aether root)
.\.venv\Scripts\Activate.ps1
python meeting-assistant/app.py
```

---

### Manual setup

```bash
git clone <your-fork-url>
cd Ai-Aether
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r meeting-assistant/requirements.txt
pip install faster-whisper
cp meeting-assistant/.env.example meeting-assistant/.env
# edit meeting-assistant/.env and fill in your keys
```

### Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `OPENAI_API_KEY` | no | Not required — entity extraction now uses Claude |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key — used for topic-shift detection **and** CRM entity extraction |
| `SF_USERNAME` | yes | Salesforce login email |
| `SF_PASSWORD` | yes | Salesforce password |
| `SF_SECURITY_TOKEN` | yes | Salesforce security token (sent to your email by Salesforce) |
| `SF_DOMAIN` | no | `login` (default) for production orgs, `test` for sandboxes |
| `HOST` | no | Bind address for the web server (default `127.0.0.1`) |
| `PORT` | no | Port for the web server (default `8000`) |
| `AUDIO_CHUNK_SECONDS` | no | Seconds of audio per Whisper request (default `5`) |
| `AUDIO_SAMPLE_RATE` | no | Mic sample rate in Hz (default `16000`) |
| `WHISPER_BACKEND` | no | `openai` (default) or `local` — see [Transcription backends](#transcription-backends) |
| `LOCAL_WHISPER_MODEL` | no | Model size for the local backend: `tiny`, `base` (default), `small`, `medium`, `large-v2`, `large-v3` |
| `LOCAL_WHISPER_DEVICE` | no | `cpu` (default) or `cuda` for the local backend |
| `LOCAL_WHISPER_COMPUTE_TYPE` | no | Quantisation type for the local backend: `int8` (default, CPU), `float16` (GPU) |

## Run

```bash
# From the Ai-Aether root with .venv active
python3 meeting-assistant/app.py
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
mic → backend/audio.py → backend/transcribe.py     (faster-whisper — local)
                       → backend/context.py        (Claude — topic shift?)
                       → backend/entities.py       (Claude — extract CRM entities)
                       → backend/topic_state.py    (merge into current topic)
                       → backend/salesforce_client.py (REST API)
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
│   ├── transcribe.py         faster-whisper local transcription
│   ├── context.py            Anthropic Claude — topic-shift detection
│   ├── entities.py           Anthropic Claude — CRM entity extraction
│   ├── topic_state.py        In-memory current-topic state
│   ├── salesforce_client.py  Salesforce REST queries + aggregations
│   ├── hub.py                WebSocket broadcast hub
│   └── config.py             Env-var loading and validation
├── frontend/
│   ├── index.html            Dashboard markup
│   ├── styles.css            Dashboard styling
│   └── app.js                WebSocket client + Chart.js charts
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
- **Salesforce auth fails** — confirm your security token is current;
  reset it in Salesforce under "My Personal Information → Reset My
  Security Token". Sandbox orgs require `SF_DOMAIN=test`.
- **Topic never shifts** — Claude treats small tangents as "same topic"
  on purpose. Shifts happen when the subject (customer, deal, product)
  clearly changes.
- **Empty charts** — the dashboard only shows data when entities match
  Accounts or Opportunities in your org.
