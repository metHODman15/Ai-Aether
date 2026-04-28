// Salesforce OAuth UI module.
// Handles auth_required, auth_success, and auth_status events; shows and
// hides the authorization panel overlay; exposes helpers to crm_banner.js.

import { state } from "./state.js";
import { dom } from "./dom.js";

// ── Panel helpers ────────────────────────────────────────────────────────────

export function showAuthPanel(msg) {
  if (!dom.authPanel) return;
  if (msg && dom.authMessage) dom.authMessage.textContent = msg;
  dom.authPanel.hidden = false;
}

export function hideAuthPanel() {
  if (!dom.authPanel) return;
  dom.authPanel.hidden = true;
}

export function isAuthorized() {
  return state.sfAuthorized;
}

// ── Event handlers ───────────────────────────────────────────────────────────

export function handleAuthRequired(evt) {
  state.sfAuthorized = false;
  const msg =
    "Connect Salesforce to see live CRM data alongside your transcript.";
  showAuthPanel(msg);
  // Update the connect-button href in case it changed.
  if (dom.authConnectBtn) {
    dom.authConnectBtn.href = evt.authorize_url || "/oauth/authorize";
  }
}

export function handleAuthSuccess() {
  state.sfAuthorized = true;
  hideAuthPanel();
}

export function handleAuthStatus(evt) {
  state.sfAuthorized = !!(evt.authorized);
  if (!state.sfAuthorized) {
    handleAuthRequired(evt);
  } else {
    hideAuthPanel();
  }
}
