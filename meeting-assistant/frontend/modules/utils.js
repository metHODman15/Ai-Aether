// Pure helpers — no DOM, no state mutation.
import { state, MAX_ARCHIVE, STORAGE_KEY } from "./state.js";

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function highlightHtml(text, query) {
  const safe = escapeHtml(text);
  if (!query) return safe;
  const re = new RegExp(escapeRegExp(query), "gi");
  return safe.replace(re, (m) => `<mark class="search-hit">${m}</mark>`);
}

export function fmtAmount(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
  }).format(n);
}

export function fmtSessionLabel(sessionId) {
  const d = new Date(sessionId * 1000);
  const datePart = d.toLocaleDateString(undefined, {
    weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
  const timePart = d.toLocaleTimeString(undefined, {
    hour: "numeric", minute: "2-digit",
  });
  return `${datePart} \u00b7 ${timePart}`;
}

export function csvCell(val) {
  const s = val == null ? "" : String(val);
  return `"${s.replace(/"/g, '""')}"`;
}

export function normalizeTopic(t) {
  const startedAt = typeof t.startedAt === "number" ? t.startedAt : Date.now() / 1000;
  return {
    id: typeof t.id === "number" ? t.id : state.nextId++,
    label: typeof t.label === "string" ? t.label : "Untitled topic",
    startedAt,
    sessionId: typeof t.sessionId === "number" ? t.sessionId : startedAt,
    lines: Array.isArray(t.lines) ? t.lines : [],
    entities: t.entities && typeof t.entities === "object" ? t.entities : {},
    crm: t.crm && typeof t.crm === "object" ? t.crm : {},
    serverId: typeof t.serverId === "string" ? t.serverId : null,
    fromServer: !!t.fromServer,
  };
}

export function persistTopics() {
  try {
    const toStore = state.topics.slice(-MAX_ARCHIVE);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      nextId: state.nextId, topics: toStore,
    }));
  } catch (e) {
    console.warn("Could not persist topics to localStorage:", e);
  }
}

export function scheduleSave() {
  clearTimeout(state._saveTimer);
  state._saveTimer = setTimeout(persistTopics, 1500);
}

export function loadPersistedTopics() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!Array.isArray(data.topics)) return;
    for (const t of data.topics) {
      if (t && typeof t === "object") state.topics.push(normalizeTopic(t));
    }
    if (typeof data.nextId === "number" && data.nextId > state.nextId) {
      state.nextId = data.nextId;
    }
  } catch (e) {
    console.warn("Could not restore topics from localStorage:", e);
  }
}

export function entityValuesText(entities) {
  const e = entities || {};
  const parts = [];
  if (e.customer_name) parts.push(e.customer_name);
  if (e.contact_name) parts.push(e.contact_name);
  if (e.deal_stage) parts.push(e.deal_stage);
  if (e.deal_amount != null) parts.push(String(e.deal_amount));
  if (Array.isArray(e.keywords)) parts.push(e.keywords.join(" "));
  return parts.join(" ");
}

export function topicMatchesQuery(t, q) {
  if (!q) return true;
  const needle = q.toLowerCase();
  if ((t.label || "").toLowerCase().includes(needle)) return true;
  if (entityValuesText(t.entities).toLowerCase().includes(needle)) return true;
  for (const line of t.lines || []) {
    if ((line.text || "").toLowerCase().includes(needle)) return true;
  }
  return false;
}

export function snippetAround(text, query) {
  const MAX = 80;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return null;
  const matchLen = query.length;
  const half = Math.floor((MAX - matchLen) / 2);
  let start = Math.max(0, idx - half);
  let end = Math.min(text.length, start + MAX);
  if (end - start < MAX) start = Math.max(0, end - MAX);
  const prefix = start > 0 ? "\u2026" : "";
  const suffix = end < text.length ? "\u2026" : "";
  return prefix + text.slice(start, end) + suffix;
}

export function getMatchSnippet(t, q) {
  if (!q) return null;
  const needle = q.toLowerCase();
  const e = t.entities || {};
  const entityFields = [
    e.customer_name, e.contact_name, e.deal_stage,
    e.deal_amount != null ? String(e.deal_amount) : null,
  ].filter(Boolean).map(String);
  if (Array.isArray(e.keywords)) entityFields.push(...e.keywords.filter(Boolean).map(String));
  for (const val of entityFields) {
    if (val.toLowerCase().includes(needle)) return snippetAround(val, q);
  }
  for (const line of t.lines || []) {
    const text = line.text || "";
    if (text.toLowerCase().includes(needle)) return snippetAround(text, q);
  }
  return null;
}
