# One-click setup script for Meeting Assistant (Windows)
# Usage: Right-click this file and choose "Run with PowerShell"
#        Or from a PowerShell terminal: .\setup.ps1
#
# If you see an execution policy error, run this first (once, as Administrator):
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

$ErrorActionPreference = "Stop"

function Write-Info    { param($msg) Write-Host "[setup] $msg" -ForegroundColor Green }
function Write-Warn    { param($msg) Write-Host "[setup] $msg" -ForegroundColor Yellow }
function Write-Err     { param($msg) Write-Host "[setup] $msg" -ForegroundColor Red }
function Write-Heading { param($msg) Write-Host "`n$msg" -ForegroundColor Cyan }

# ── 1. Check Python version ────────────────────────────────────────────────────
Write-Heading "Checking Python version..."

$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>$null
        if ($ver) {
            $parts = $ver -split "\."
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10)) {
                $pythonCmd = $cmd
                Write-Info "Found $cmd ($ver) — OK"
                break
            } else {
                Write-Warn "$cmd found but version is $ver (need 3.10+), skipping"
            }
        }
    } catch { }
}

if (-not $pythonCmd) {
    Write-Err "Python 3.10 or newer is required but was not found."
    Write-Err "Download it from https://www.python.org/downloads/"
    Write-Err "Make sure to check 'Add Python to PATH' during installation."
    Read-Host "Press Enter to exit"
    exit 1
}

# ── 2. Note on system audio libraries ─────────────────────────────────────────
Write-Heading "PortAudio / audio library note..."
Write-Warn "On Windows, sounddevice uses pre-bundled binaries — no extra install needed."
Write-Warn "If you hit audio errors, make sure your microphone is connected and enabled."

# ── 3. Create virtual environment ─────────────────────────────────────────────
Write-Heading "Setting up Python virtual environment (.venv)..."

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if (Test-Path ".venv") {
    Write-Info ".venv already exists — skipping creation"
} else {
    & $pythonCmd -m venv .venv
    Write-Info "Created .venv"
}

$activateScript = Join-Path $scriptDir ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activateScript)) {
    Write-Err "Could not find .venv\Scripts\Activate.ps1 — venv creation may have failed."
    Read-Host "Press Enter to exit"
    exit 1
}

. $activateScript
Write-Info "Activated .venv"

# ── 4. Install Python dependencies ────────────────────────────────────────────
Write-Heading "Installing Python dependencies from requirements.txt..."

python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
Write-Info "All Python dependencies installed"

# ── 5. Configure .env ─────────────────────────────────────────────────────────
Write-Heading "Configuring environment variables..."

if (Test-Path ".env") {
    Write-Warn ".env already exists — skipping credential prompts."
    Write-Warn "Edit .env manually if you need to update any values."
} else {
    Write-Host ""
    Write-Host "You will be prompted for each required credential."
    Write-Host "Optional fields can be left blank to use the defaults shown."
    Write-Host ""

    function Prompt-Required {
        param([string]$VarName, [string]$Label)
        $value = ""
        while ([string]::IsNullOrWhiteSpace($value)) {
            $value = Read-Host "  $Label"
            if ([string]::IsNullOrWhiteSpace($value)) {
                Write-Warn "  This field is required — please enter a value."
            }
        }
        return "$VarName=$($value.Trim())"
    }

    function Prompt-Optional {
        param([string]$VarName, [string]$Label, [string]$Default)
        $raw = Read-Host "  $Label [$Default]"
        $value = if ([string]::IsNullOrWhiteSpace($raw)) { $Default } else { $raw.Trim() }
        return "$VarName=$value"
    }

    function Prompt-EncryptionKey {
        $raw = Read-Host "  Encryption key (leave blank to auto-generate)"
        if ([string]::IsNullOrWhiteSpace($raw)) {
            $value = python -c "import secrets; print(secrets.token_hex(32))"
            Write-Info "  Auto-generated ENCRYPTION_KEY"
        } else {
            $value = $raw.Trim()
        }
        return "ENCRYPTION_KEY=$value"
    }

    Write-Host "── Transcription backend ─────────────────────────────" -ForegroundColor Cyan
    Write-Host "  Two Whisper backends are available:"
    Write-Host "    openai  — Audio sent to the OpenAI Whisper API (default, no extra install)"
    Write-Host "    local   — Transcription runs entirely on-device with faster-whisper (free, private)"
    $whisperBackendValue = ""
    while ($whisperBackendValue -ne "openai" -and $whisperBackendValue -ne "local") {
        $raw = Read-Host "  Choose backend [openai]"
        $whisperBackendValue = if ([string]::IsNullOrWhiteSpace($raw)) { "openai" } else { $raw.Trim().ToLower() }
        if ($whisperBackendValue -ne "openai" -and $whisperBackendValue -ne "local") {
            Write-Warn "  Enter 'openai' or 'local'."
        }
    }
    $lineWhisperBackend = "WHISPER_BACKEND=$whisperBackendValue"

    if ($whisperBackendValue -eq "local") {
        Write-Info "  Selected: local backend — audio never leaves your machine."
        Write-Info "  You'll need to run:  pip install faster-whisper"
        Write-Info "  Model weights are downloaded automatically on first start."
        $needOpenAI = $false
    } else {
        Write-Info "  Selected: openai backend — audio sent to OpenAI Whisper API."
        $needOpenAI = $true
    }

    Write-Host ""
    Write-Host "── API Keys ──────────────────────────────────────────" -ForegroundColor Cyan
    $lineAnthropic = Prompt-Required "ANTHROPIC_API_KEY" "Anthropic API key (Claude — topic detection and entity extraction)"
    if ($needOpenAI) {
        $lineOpenAI = Prompt-Required "OPENAI_API_KEY" "OpenAI API key (Whisper transcription)"
    } else {
        Write-Info "  OPENAI_API_KEY not required in local mode (entity extraction uses Anthropic)."
        $lineOpenAI = Prompt-Optional "OPENAI_API_KEY" "OpenAI API key (optional — only needed if you switch to openai backend)" ""
    }

    Write-Host ""
    Write-Host "── Salesforce OAuth (Connected App) ──────────────────" -ForegroundColor Cyan
    Write-Host "  Create a Connected App in Salesforce Setup -> App Manager."
    Write-Host "  Set the callback URL to: http://localhost:8000/oauth/callback"
    Write-Host "  (adjust the port if you change PORT below)"
    Write-Host ""
    $lineSfClientId     = Prompt-Required "SF_CLIENT_ID"     "Salesforce Connected App Consumer Key"
    $lineSfClientSecret = Prompt-Required "SF_CLIENT_SECRET" "Salesforce Connected App Consumer Secret"
    $lineSfLoginUrl     = Prompt-Optional "SF_LOGIN_URL"     "Salesforce login URL" "https://login.salesforce.com"
    $lineEncKey         = Prompt-EncryptionKey

    Write-Host ""
    Write-Host "── Server Settings (optional) ────────────────────────" -ForegroundColor Cyan
    $lineHost = Prompt-Optional "HOST" "Bind address" "127.0.0.1"
    $linePort = Prompt-Optional "PORT" "Port"         "8000"

    Write-Host ""
    Write-Host "── Audio Settings (optional) ─────────────────────────" -ForegroundColor Cyan
    $lineChunk = Prompt-Optional "AUDIO_CHUNK_SECONDS" "Audio chunk length in seconds" "5"
    $lineRate  = Prompt-Optional "AUDIO_SAMPLE_RATE"   "Microphone sample rate (Hz)"  "16000"

    $envContent = @"
# Generated by setup.ps1 — edit any value and restart the app to apply changes.

# Anthropic API key — Claude powers both topic-shift detection and CRM entity extraction.
$lineAnthropic

# OpenAI API key — Whisper transcription only.
# Required when WHISPER_BACKEND=openai (the default). Not needed for WHISPER_BACKEND=local.
$lineOpenAI

# Transcription backend: "openai" (default) or "local" (faster-whisper, on-device)
$lineWhisperBackend

# Salesforce OAuth 2.0 — Connected App credentials
$lineSfClientId
$lineSfClientSecret
# "https://login.salesforce.com" for production orgs, "https://test.salesforce.com" for sandboxes
$lineSfLoginUrl

# Fernet encryption key for storing OAuth tokens at rest (hex string).
# Keep this secret — regenerating it will invalidate stored tokens.
$lineEncKey

# Server settings
$lineHost
$linePort

# Audio capture settings
$lineChunk
$lineRate
"@

    Set-Content -Path ".env" -Value $envContent -Encoding UTF8
    Write-Info ".env written successfully"
}

# ── Done ───────────────────────────────────────────────────────────────────────
Write-Heading "Setup complete!"

# Read the actual host/port that will be used (from .env if present, with defaults)
$envHost = "127.0.0.1"
$envPort = "8000"
if (Test-Path ".env") {
    foreach ($line in Get-Content ".env") {
        if ($line -match '^HOST=(.+)$')  { $envHost = $Matches[1].Trim() }
        if ($line -match '^PORT=(.+)$')  { $envPort = $Matches[1].Trim() }
    }
}

Write-Host ""
Write-Host "To start the assistant, open a new PowerShell window and run:"
Write-Host ""
Write-Host "  cd $scriptDir"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python app.py"
Write-Host ""
Write-Host "Then open http://${envHost}:${envPort} in your browser."
Write-Host "On first launch, click 'Connect to Salesforce' to authorize via OAuth."
Write-Host ""
Read-Host "Press Enter to exit"
