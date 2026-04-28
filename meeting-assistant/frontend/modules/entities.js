import { dom } from "./dom.js";
import { fmtAmount } from "./utils.js";

function setField(name, value) {
  const el = dom.entities.querySelector(`[data-field="${name}"]`);
  if (!el) return;
  if (Array.isArray(value)) {
    el.textContent = value.length ? value.join(", ") : "—";
  } else if (name === "deal_amount") {
    el.textContent = value == null ? "—" : fmtAmount(value);
  } else {
    el.textContent = value || "—";
  }
}

export function renderEntities(entities) {
  const e = entities || {};
  setField("customer_name", e.customer_name);
  setField("contact_name", e.contact_name);
  setField("deal_amount", e.deal_amount);
  setField("deal_stage", e.deal_stage);
  setField("keywords", e.keywords || []);
}
