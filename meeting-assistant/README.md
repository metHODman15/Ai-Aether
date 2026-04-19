# Meeting Assistant

A local, real-time meeting assistant that listens to your microphone, transcribes
the conversation with OpenAI Whisper, extracts CRM-relevant entities (customer,
contact, deal amount, stage) with Anthropic Claude, queries your Salesforce org
through the REST API, and visualises everything on a live web dashboard.

Everything runs locally on your machine. No data is stored on disk.

## Requirements

- Python 3.10 or newer
- A working microphone
- An OpenAI API key (Whisper)
- An Anthropic API key (Claude)
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
| `OPENAI_API_KEY` | yes | OpenAI API key used for Whisper transcription |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key used for Claude entity extraction |
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

Open <http://127.0.0.1:8000> in your browser. As soon as the page loads it
opens a WebSocket back to the server and you'll see:

- A live transcript that updates every few seconds
- The extracted entities (customer, contact, amount, stage, keywords)
- A pie chart showing the distribution of matching Opportunity stages
- A line graph of matching Opportunity amounts over time
- Lists of matching Salesforce Accounts and Opportunities

Stop the server with `Ctrl+C`.

## How it works

```
mic → backend/audio.py → backend/transcribe.py (Whisper)
                       → backend/entities.py    (Claude)
                       → backend/salesforce_client.py (REST API)
                       → backend/hub.py → WebSocket → frontend/
```

Each chunk of audio flows through transcription, entity extraction, and a
Salesforce lookup. Every step broadcasts a JSON event to all connected
browsers.

## Project layout

```
meeting-assistant/
├── app.py                    FastAPI server + capture/transcribe pipeline
├── backend/
│   ├── audio.py              Microphone capture (sounddevice)
│   ├── transcribe.py         OpenAI Whisper wrapper
│   ├── entities.py           Anthropic Claude extraction
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

- **"Missing required environment variable"** — copy `.env.example` to `.env`
  and fill in the missing key.
- **No microphone detected / PortAudio errors** — install the system PortAudio
  package as noted above and ensure your OS allows mic access for the terminal.
- **Salesforce auth fails** — confirm your security token is current; reset it
  in Salesforce under "My Personal Information → Reset My Security Token".
  Sandbox orgs require `SF_DOMAIN=test`.
- **Empty charts** — the dashboard only shows data when Claude extracts an
  entity that matches an Account or Opportunity in your org.
