# Meeting Assistant

A local, real-time meeting assistant that listens to your microphone,
transcribes the conversation with OpenAI Whisper, uses Anthropic Claude
for **conversation context management** (detecting when the topic
shifts), extracts CRM entities with OpenAI, queries your Salesforce org
through the REST API, and visualises everything on a live web dashboard
that stays pinned to the current topic.

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

CRM entity extraction (customer, contact, deal amount, stage) runs
against an OpenAI chat model, keeping Claude reserved for context
management.

## Requirements

- Python 3.10 or newer
- A working microphone
- An OpenAI API key (Whisper transcription + entity extraction)
- An Anthropic API key (Claude — used only for topic detection)
- Salesforce username, password, and security token

On Linux you may need PortAudio system libraries for microphone capture:

```bash
# Debian / Ubuntu
sudo apt-get install -y portaudio19-dev libsndfile1

# macOS (Homebrew)
brew install portaudio libsndfile
```

## Setup

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
| `OPENAI_API_KEY` | yes | OpenAI API key — used for Whisper transcription and entity extraction |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key — used **only** for topic-shift detection |
| `SF_USERNAME` | yes | Salesforce login email |
| `SF_PASSWORD` | yes | Salesforce password |
| `SF_SECURITY_TOKEN` | yes | Salesforce security token (sent to your email by Salesforce) |
| `SF_DOMAIN` | no | `login` (default) for production orgs, `test` for sandboxes |
| `HOST` | no | Bind address for the web server (default `127.0.0.1`) |
| `PORT` | no | Port for the web server (default `8000`) |
| `AUDIO_CHUNK_SECONDS` | no | Seconds of audio per Whisper request (default `5`) |
| `AUDIO_SAMPLE_RATE` | no | Mic sample rate in Hz (default `16000`) |

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
                       → backend/entities.py       (OpenAI — extract CRM entities)
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
│   ├── transcribe.py         OpenAI Whisper wrapper
│   ├── context.py            Anthropic Claude — topic-shift detection
│   ├── entities.py           OpenAI — CRM entity extraction
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
