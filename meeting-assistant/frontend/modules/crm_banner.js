// Persistent banner + panel-greying for graceful Salesforce degradation.
import { dom } from "./dom.js";
import { state } from "./state.js";

function ensureBanner() {
  if (dom.crmBanner) return dom.crmBanner;
  const banner = document.createElement("div");
  banner.id = "crmBanner";
  banner.className = "crm-banner";
  banner.hidden = true;
  banner.innerHTML =
    '<span class="crm-banner-dot"></span>' +
    '<span class="crm-banner-text">Salesforce is offline — CRM data is unavailable.</span>' +
    '<a class="crm-banner-reauth" href="/oauth/authorize">Reconnect</a>';
  document.body.insertBefore(banner, document.body.firstChild);
  dom.crmBanner = banner;
  return banner;
}

export function setCrmStatus(online, reason) {
  state.crmOnline = !!online;
  const banner = ensureBanner();
  banner.hidden = !!online;
  if (!online) {
    const textEl = banner.querySelector(".crm-banner-text");
    if (textEl) {
      const msg = reason
        ? `Salesforce is offline — CRM data is unavailable. (${reason})`
        : "Salesforce is offline — CRM data is unavailable.";
      textEl.textContent = msg;
    }
    // Show reauth link only when the reason is auth-related.
    const reauth = banner.querySelector(".crm-banner-reauth");
    if (reauth) {
      reauth.hidden = !(reason && reason.includes("auth"));
    }
  }
  document.body.classList.toggle("crm-offline", !online);
}
