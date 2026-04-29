// Persistent banner + panel-greying for graceful Salesforce degradation.
import { dom } from "./dom.js";
import { state } from "./state.js";

// Mirrors backend `MCP_TIMEOUT_REASON` in mcp_client.py. When the offline
// reason matches this exact string, the dashboard knows the background
// recovery probe is actively retrying, and we render a "Reconnecting…"
// hint so reps don't think the app has given up.
const MCP_TIMEOUT_REASON = "Salesforce MCP server timed out";

// Friendly, rep-facing copy keyed by the raw backend reason string. The
// backend reasons (e.g. "Salesforce MCP server timed out",
// "auth_required", "mcp_tools_unavailable") are useful for engineers but
// confusing in-product, so the banner translates them here. Centralising
// the mapping keeps "what does the user see for reason X?" answerable in
// one place — to add a new reason, drop another entry into this object.
const DEFAULT_OFFLINE_MESSAGE =
  "Salesforce is offline — CRM data is unavailable.";
const REASON_MESSAGES = {
  [MCP_TIMEOUT_REASON]:
    "Salesforce is slow to respond — we're retrying in the background.",
  auth_required:
    "Salesforce needs you to sign in again to load CRM data.",
  mcp_tools_unavailable:
    "Salesforce is connected, but its CRM tools aren't responding right now.",
};

function messageForReason(reason) {
  if (reason && Object.prototype.hasOwnProperty.call(REASON_MESSAGES, reason)) {
    return REASON_MESSAGES[reason];
  }
  return DEFAULT_OFFLINE_MESSAGE;
}

// How long the per-click "still timing out" status text lingers before
// fading away — long enough to read, short enough not to interfere with
// the next click.
const RETRY_FEEDBACK_MS = 4000;

// How often the "Last connected X ago" line refreshes while the banner
// is visible. 15s is granular enough that a "1 min ago" line ticks over
// promptly without burning CPU during a longer outage.
const LAST_ONLINE_TICK_MS = 15000;

let _retryInFlight = false;
let _retryFeedbackTimer = null;
let _lastOnlineTimer = null;

function ensureBanner() {
  if (dom.crmBanner) return dom.crmBanner;
  const banner = document.createElement("div");
  banner.id = "crmBanner";
  banner.className = "crm-banner";
  banner.hidden = true;
  banner.innerHTML =
    '<span class="crm-banner-dot"></span>' +
    '<span class="crm-banner-text">' + DEFAULT_OFFLINE_MESSAGE + '</span>' +
    '<span class="crm-banner-reconnecting" hidden>' +
      '<span class="crm-banner-reconnecting-dot"></span>' +
      '<span class="crm-banner-reconnecting-text">Reconnecting…</span>' +
    '</span>' +
    '<button type="button" class="crm-banner-retry" hidden>Retry now</button>' +
    '<span class="crm-banner-retry-feedback" hidden></span>' +
    '<a class="crm-banner-reauth" href="/oauth/authorize">Reconnect</a>' +
    '<span class="crm-banner-last-online" hidden></span>';
  document.body.insertBefore(banner, document.body.firstChild);
  dom.crmBanner = banner;
  const retryBtn = banner.querySelector(".crm-banner-retry");
  if (retryBtn) retryBtn.addEventListener("click", onRetryClick);
  return banner;
}

function setRetryFeedback(message, kind) {
  const banner = ensureBanner();
  const fb = banner.querySelector(".crm-banner-retry-feedback");
  if (!fb) return;
  if (_retryFeedbackTimer !== null) {
    clearTimeout(_retryFeedbackTimer);
    _retryFeedbackTimer = null;
  }
  if (!message) {
    fb.hidden = true;
    fb.textContent = "";
    fb.classList.remove("is-success", "is-failure");
    return;
  }
  fb.textContent = message;
  fb.classList.toggle("is-success", kind === "success");
  fb.classList.toggle("is-failure", kind === "failure");
  fb.hidden = false;
  _retryFeedbackTimer = setTimeout(() => {
    fb.hidden = true;
    fb.textContent = "";
    fb.classList.remove("is-success", "is-failure");
    _retryFeedbackTimer = null;
  }, RETRY_FEEDBACK_MS);
}

// Format the gap between `nowMs` and `tsMs` as a friendly relative
// phrase for the offline banner. Resolution is deliberately coarse:
// reps want to gauge staleness ("a few minutes" vs "an hour") rather
// than read precise seconds, and the matching tick interval is 15s.
function formatLastOnline(tsMs, nowMs) {
  const diffSec = Math.max(0, Math.floor((nowMs - tsMs) / 1000));
  if (diffSec < 60) return "Last connected just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) {
    return `Last connected ${diffMin} min ago`;
  }
  const diffHr = Math.floor(diffMin / 60);
  return `Last connected ${diffHr} hr ago`;
}

function updateLastOnlineDisplay() {
  const banner = ensureBanner();
  const el = banner.querySelector(".crm-banner-last-online");
  if (!el) return;
  // Hide entirely when we have no confirmed online observation yet
  // this session — the spec calls for this so the banner doesn't lie
  // about a connection it never actually saw.
  if (state.crmLastOnlineAt == null) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.textContent = formatLastOnline(state.crmLastOnlineAt, Date.now());
  el.hidden = false;
}

function startLastOnlineTicker() {
  // Render once immediately so the line appears the same tick the
  // banner becomes visible, then tick on a coarse interval.
  updateLastOnlineDisplay();
  if (_lastOnlineTimer !== null) return;
  _lastOnlineTimer = setInterval(updateLastOnlineDisplay, LAST_ONLINE_TICK_MS);
}

function stopLastOnlineTicker() {
  if (_lastOnlineTimer !== null) {
    clearInterval(_lastOnlineTimer);
    _lastOnlineTimer = null;
  }
  const banner = ensureBanner();
  const el = banner.querySelector(".crm-banner-last-online");
  if (el) {
    el.hidden = true;
    el.textContent = "";
  }
}

// Format the cooldown-coalesced hint shown when the backend short-circuits
// our click into a recent probe. Resolution is in whole seconds — the
// cooldown is only a few seconds wide, so anything finer would jitter
// without telling the rep anything new. Anything below 1s rounds up to
// "1 second" so the message never reads as "0 seconds ago" (which would
// look like a fresh probe and defeat the point of the hint).
function formatJustChecked(ageSeconds, online) {
  const secs = Math.max(1, Math.round(Number(ageSeconds) || 0));
  const unit = secs === 1 ? "second" : "seconds";
  const tail = online ? "still online" : "still offline";
  return `Just checked ${secs} ${unit} ago — ${tail}`;
}

async function onRetryClick(ev) {
  // Guard against rapid-fire clicks even though the button is also
  // disabled while in flight — defence in depth against any DOM race.
  if (_retryInFlight) return;
  const btn = ev.currentTarget;
  _retryInFlight = true;
  btn.disabled = true;
  const originalLabel = btn.textContent;
  btn.textContent = "Retrying…";
  setRetryFeedback("");
  try {
    const res = await fetch("/salesforce/retry", { method: "POST" });
    let online = false;
    let cached = false;
    let ageSeconds = 0;
    if (res.ok) {
      try {
        const data = await res.json();
        online = !!(data && data.online);
        cached = !!(data && data.cached);
        ageSeconds = Number((data && data.age_seconds) || 0);
      } catch (_) {
        online = false;
      }
    }
    if (cached) {
      // The backend coalesced this click into a recent probe rather
      // than opening a fresh MCP session. Tell the rep their click was
      // registered and surface the age of the underlying check, so it
      // doesn't look like the button did nothing.
      setRetryFeedback(
        formatJustChecked(ageSeconds, online),
        online ? "success" : "failure",
      );
    } else if (online) {
      // The backend probe also broadcasts crm_online via the WebSocket,
      // which will hide the banner outright; the success message is just
      // a brief confirmation in case the banner re-appears moments later.
      setRetryFeedback("Back online", "success");
    } else {
      setRetryFeedback("Still timing out", "failure");
    }
  } catch (_) {
    setRetryFeedback("Still timing out", "failure");
  } finally {
    _retryInFlight = false;
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

export function setCrmStatus(online, reason) {
  state.crmOnline = !!online;
  const banner = ensureBanner();
  banner.hidden = !!online;
  const reconnecting = banner.querySelector(".crm-banner-reconnecting");
  const retryBtn = banner.querySelector(".crm-banner-retry");
  if (!online) {
    const textEl = banner.querySelector(".crm-banner-text");
    if (textEl) {
      // Always render through the centralised mapping so we never leak a
      // raw backend reason like "mcp_tools_unavailable" into the UI;
      // unknown reasons fall back to the generic offline copy.
      textEl.textContent = messageForReason(reason);
    }
    // Show reauth link only when the reason is auth-related.
    const reauth = banner.querySelector(".crm-banner-reauth");
    if (reauth) {
      reauth.hidden = !(reason && reason.includes("auth"));
    }
    // Show the "Reconnecting…" hint *and* the "Retry now" button only
    // for the MCP-timeout reason — that's the failure mode the backend
    // recovery probe handles. Other offline reasons (auth_required,
    // mcp_tools_unavailable) need explicit user action via the reauth
    // link, so a probe wouldn't help and the controls would mislead.
    const isTimeout = reason === MCP_TIMEOUT_REASON;
    if (reconnecting) reconnecting.hidden = !isTimeout;
    if (retryBtn) {
      retryBtn.hidden = !isTimeout;
      if (!isTimeout) {
        // Drop any stale per-click feedback so it doesn't carry over to
        // a different offline cause.
        setRetryFeedback("");
      }
    }
    // Start (or refresh) the live "Last connected …" line so reps can
    // gauge how stale the greyed-out CRM panels are. The ticker no-ops
    // its display when crmLastOnlineAt is null (never confirmed online
    // this session), so it's safe to call unconditionally here.
    startLastOnlineTicker();
  } else {
    // Back online — hide hint, retry button, and any lingering feedback
    // immediately so they don't carry over to the next offline event.
    if (reconnecting) reconnecting.hidden = true;
    if (retryBtn) retryBtn.hidden = true;
    setRetryFeedback("");
    stopLastOnlineTicker();
  }
  document.body.classList.toggle("crm-offline", !online);
}
