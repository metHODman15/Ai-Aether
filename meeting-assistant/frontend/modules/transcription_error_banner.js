// Collapsed banner for consecutive transcription failures (Whisper / Claude /
// OpenAI). Replaces the raw red "[stage error] …" lines in the live view with
// a single friendly notice + a retry affordance that sends a signal to the
// backend and clears the error state.
import { dom } from "./dom.js";
import { sendMessage } from "./websocket.js";

const TRANSCRIPTION_STAGES = new Set(["transcribe", "context", "extract"]);

let consecutiveErrors = 0;

function ensureBanner() {
  if (dom.transcriptionErrorBanner) return dom.transcriptionErrorBanner;
  const banner = document.createElement("div");
  banner.id = "transcriptionErrorBanner";
  banner.className = "transcription-error-banner";
  banner.hidden = true;
  banner.innerHTML =
    '<span class="te-banner-icon" aria-hidden="true">⚠</span>' +
    '<span class="te-banner-text">Transcription is having trouble — some audio may be missing.</span>' +
    '<button class="te-banner-retry" type="button">Retry</button>';
  banner.querySelector(".te-banner-retry").addEventListener("click", () => {
    // Send a retry signal to the backend so it knows the user wants
    // the pipeline to attempt the current audio segment again, then
    // clear the local error state to hide the banner.
    sendMessage({ type: "retry_transcription" });
    clearTranscriptionErrors();
  });
  // Insert at the top of the live-view panel so it stays contextual to the
  // transcript, not the whole page.
  if (dom.liveView) {
    dom.liveView.insertBefore(banner, dom.liveView.firstChild);
  } else {
    document.body.insertBefore(banner, document.body.firstChild);
  }
  dom.transcriptionErrorBanner = banner;
  return banner;
}

function updateBannerText(banner) {
  const textEl = banner.querySelector(".te-banner-text");
  if (!textEl) return;
  if (consecutiveErrors <= 1) {
    textEl.textContent =
      "Transcription is having trouble — some audio may be missing.";
  } else {
    textEl.textContent =
      `Transcription is having trouble (${consecutiveErrors} failures) — some audio may be missing.`;
  }
}

/**
 * Call this whenever an "error" event arrives for a transcription stage.
 * Only stages in TRANSCRIPTION_STAGES are tracked; others are ignored.
 * Returns true if the error was handled (caller should skip inline rendering).
 */
export function recordTranscriptionError(stage) {
  if (!TRANSCRIPTION_STAGES.has(stage)) return false;
  consecutiveErrors += 1;
  const banner = ensureBanner();
  updateBannerText(banner);
  banner.hidden = false;
  return true;
}

/**
 * Call this when a successful transcript line or topic_shift arrives,
 * indicating the pipeline has recovered.
 */
export function clearTranscriptionErrors() {
  consecutiveErrors = 0;
  const banner = ensureBanner();
  banner.hidden = true;
}
