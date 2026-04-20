#!/usr/bin/env bash
# One-click setup script for Meeting Assistant (macOS / Linux)
# Usage: bash setup.sh

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()    { echo -e "${GREEN}[setup]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[setup]${RESET} $*"; }
error()   { echo -e "${RED}[setup]${RESET} $*" >&2; }
heading() { echo -e "\n${BOLD}$*${RESET}"; }

# ── 1. Check Python version ────────────────────────────────────────────────────
heading "Checking Python version..."

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c 'import sys; print(sys.version_info[:2])')
        major=$("$cmd" -c 'import sys; print(sys.version_info.major)')
        minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)')
        if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; }; then
            PYTHON="$cmd"
            info "Found $cmd ($ver) — OK"
            break
        else
            warn "$cmd found but version is $ver (need 3.10+), skipping"
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.10 or newer is required but was not found."
    error "Install it from https://www.python.org/downloads/ and re-run this script."
    exit 1
fi

# ── 2. Install system audio libraries ─────────────────────────────────────────
heading "Checking system audio libraries (PortAudio / libsndfile)..."

OS="$(uname -s)"
case "$OS" in
    Darwin)
        if command -v brew &>/dev/null; then
            info "Homebrew detected — installing portaudio and libsndfile if needed..."
            brew install portaudio libsndfile
        else
            warn "Homebrew not found. If you hit PortAudio errors, install it:"
            warn "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            warn "  brew install portaudio libsndfile"
        fi
        ;;
    Linux)
        if command -v apt-get &>/dev/null; then
            info "apt-get detected — installing portaudio19-dev and libsndfile1..."
            sudo apt-get install -y portaudio19-dev libsndfile1
        elif command -v dnf &>/dev/null; then
            info "dnf detected — installing portaudio-devel and libsndfile..."
            sudo dnf install -y portaudio-devel libsndfile
        elif command -v pacman &>/dev/null; then
            info "pacman detected — installing portaudio and libsndfile..."
            sudo pacman -S --noconfirm portaudio libsndfile
        else
            warn "Could not detect a supported package manager."
            warn "Make sure portaudio and libsndfile development libraries are installed."
        fi
        ;;
    *)
        warn "Unknown OS '$OS'. Skipping system library installation."
        warn "Ensure PortAudio and libsndfile are available before running the app."
        ;;
esac

# ── 3. Create virtual environment ─────────────────────────────────────────────
heading "Setting up Python virtual environment (.venv)..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv" ]; then
    info ".venv already exists — skipping creation"
else
    "$PYTHON" -m venv .venv
    info "Created .venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate
info "Activated .venv"

# ── 4. Install Python dependencies ────────────────────────────────────────────
heading "Installing Python dependencies from requirements.txt..."

pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
info "All Python dependencies installed"

# ── 5. Configure .env ─────────────────────────────────────────────────────────
heading "Configuring environment variables..."

if [ -f ".env" ]; then
    warn ".env already exists — skipping credential prompts."
    warn "Edit .env manually if you need to update any values."
else
    echo
    echo "You will be prompted for each required credential."
    echo "Optional fields can be left blank to use the defaults shown."
    echo

    prompt_required() {
        local var="$1"
        local label="$2"
        local value=""
        while [ -z "$value" ]; do
            read -rp "  ${label}: " value
            if [ -z "$value" ]; then
                warn "  This field is required — please enter a value."
            fi
        done
        echo "$var=$value"
    }

    prompt_optional() {
        local var="$1"
        local label="$2"
        local default="$3"
        read -rp "  ${label} [${default}]: " value
        value="${value:-$default}"
        echo "$var=$value"
    }

    echo -e "${BOLD}── API Keys ──────────────────────────────────────────${RESET}"
    LINE_OPENAI=$(prompt_required  "OPENAI_API_KEY"    "OpenAI API key (Whisper + entity extraction)")
    LINE_ANTHROPIC=$(prompt_required "ANTHROPIC_API_KEY" "Anthropic API key (Claude topic detection)")

    echo
    echo -e "${BOLD}── Salesforce Credentials ────────────────────────────${RESET}"
    LINE_SF_USER=$(prompt_required  "SF_USERNAME"       "Salesforce login email")
    LINE_SF_PASS=$(prompt_required  "SF_PASSWORD"       "Salesforce password")
    LINE_SF_TOK=$(prompt_required   "SF_SECURITY_TOKEN" "Salesforce security token")
    LINE_SF_DOM=$(prompt_optional   "SF_DOMAIN"         "SF_DOMAIN (login=production, test=sandbox)" "login")

    echo
    echo -e "${BOLD}── Server Settings (optional) ────────────────────────${RESET}"
    LINE_HOST=$(prompt_optional "HOST" "Bind address" "127.0.0.1")
    LINE_PORT=$(prompt_optional "PORT" "Port"         "8000")

    echo
    echo -e "${BOLD}── Audio Settings (optional) ─────────────────────────${RESET}"
    LINE_CHUNK=$(prompt_optional "AUDIO_CHUNK_SECONDS" "Audio chunk length in seconds" "5")
    LINE_RATE=$(prompt_optional  "AUDIO_SAMPLE_RATE"   "Microphone sample rate (Hz)"  "16000")

    cat > .env <<EOF
# Generated by setup.sh — edit any value and restart the app to apply changes.

# OpenAI API key (used for Whisper transcription and CRM entity extraction)
${LINE_OPENAI}

# Anthropic API key (used ONLY for Claude-powered topic-shift detection)
${LINE_ANTHROPIC}

# Salesforce credentials
${LINE_SF_USER}
${LINE_SF_PASS}
${LINE_SF_TOK}
# "login" for production orgs, "test" for sandboxes
${LINE_SF_DOM}

# Server settings
${LINE_HOST}
${LINE_PORT}

# Audio capture settings
${LINE_CHUNK}
${LINE_RATE}
EOF

    info ".env written successfully"
fi

# ── Done ───────────────────────────────────────────────────────────────────────
heading "Setup complete!"

# Read the actual host/port that will be used (from .env if present, with defaults)
_host=$(grep -E '^HOST=' .env 2>/dev/null | cut -d= -f2)
_port=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2)
_host="${_host:-127.0.0.1}"
_port="${_port:-8000}"

echo
echo "To start the assistant, run:"
echo
echo "  source .venv/bin/activate"
echo "  python app.py"
echo
echo "Then open http://${_host}:${_port} in your browser."
echo
