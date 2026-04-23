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
    '<span class="crm-banner-text">Salesforce is offline — CRM data is unavailable.</span>';
  document.body.insertBefore(banner, document.body.firstChild);
  dom.crmBanner = banner;
  return banner;
}

export function setCrmStatus(online, reason) {
  state.crmOnline = !!online;
  const banner = ensureBanner();
  banner.hidden = !!online;
  if (!online && reason) {
    const textEl = banner.querySelector(".crm-banner-text");
    if (textEl) {
      textEl.textContent = `Salesforce is offline — CRM data is unavailable. (${reason})`;
    }
  }
  document.body.classList.toggle("crm-offline", !online);
}
