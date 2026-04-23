// WebSocket client with exponential-backoff reconnection.
//
// The status badge surfaces "Connecting…", "Connected", "Reconnecting…",
// or "Disconnected" so a brief network blip is visible to the user but
// does not look like a crash. The cached topic state in `state.topics`
// is preserved across reconnects so the UI never flashes empty.
import { dom } from "./dom.js";
import { state } from "./state.js";
import { handleEvent } from "./events.js";

const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

let ws = null;
let backoffMs = MIN_BACKOFF_MS;
let reconnectTimer = null;
let everConnected = false;

function setStatus(label, cls) {
  if (state.demoActive) return;
  dom.status.textContent = label;
  dom.status.classList.remove("connected", "disconnected", "reconnecting", "demo-status");
  dom.status.classList.add(cls);
}

export function setDemoStatus() {
  dom.status.textContent = "Demo Mode";
  dom.status.classList.remove("disconnected", "reconnecting");
  dom.status.classList.add("connected", "demo-status");
}

export function clearDemoStatus() {
  dom.status.classList.remove("demo-status");
}

function showDemoBanner() {
  if (dom.demoBanner && !state.demoActive) dom.demoBanner.hidden = false;
}

function hideDemoBanner() {
  if (dom.demoBanner) dom.demoBanner.hidden = true;
}

function scheduleReconnect() {
  if (state.demoActive) return;
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    setStatus("Reconnecting\u2026", "reconnecting");
    connect();
  }, backoffMs);
  backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
}

export function connect() {
  if (state.demoActive) return;
  clearTimeout(reconnectTimer);

  // First attempt shows "Connecting…" — subsequent attempts during a
  // dropped connection show "Reconnecting…" so the user can tell.
  setStatus(everConnected ? "Reconnecting\u2026" : "Connecting\u2026",
            everConnected ? "reconnecting" : "disconnected");

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${window.location.host}/ws`;
  try {
    ws = new WebSocket(url);
  } catch (e) {
    console.warn("WebSocket constructor failed:", e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    everConnected = true;
    backoffMs = MIN_BACKOFF_MS;
    state.currentSessionId = Date.now() / 1000;
    setStatus("Connected", "connected");
    hideDemoBanner();
  };

  ws.onclose = () => {
    state.currentSessionId = null;
    if (state.demoActive) return;
    setStatus(
      everConnected ? "Reconnecting\u2026" : "Disconnected",
      everConnected ? "reconnecting" : "disconnected",
    );
    if (!everConnected) showDemoBanner();
    scheduleReconnect();
  };

  ws.onerror = () => {
    try { ws.close(); } catch (_) {}
  };

  ws.onmessage = (msg) => {
    try { handleEvent(JSON.parse(msg.data)); }
    catch (e) { console.error("Bad message", e, msg.data); }
  };
}

export function resetReconnectBackoff() {
  backoffMs = MIN_BACKOFF_MS;
}
