import { dom } from "./dom.js";
import {
  state, MAX_HISTORY, getTopic, viewedTopic, isViewingLive,
} from "./state.js";
import {
  fmtSessionLabel, highlightHtml, topicMatchesQuery, getMatchSnippet, persistTopics,
} from "./utils.js";
import {
  setTopicLabel, renderTranscriptLines,
} from "./transcript.js";
import { renderEntities } from "./entities.js";
import { renderCrm } from "./charts.js";

export function renderViewedTopic() {
  const t = viewedTopic();
  if (!t) {
    setTopicLabel("", false);
    renderTranscriptLines([], null, null);
    renderEntities({}); renderCrm({});
    return;
  }
  const viewingPast = !isViewingLive();
  setTopicLabel(t.label, viewingPast);
  renderTranscriptLines(t.lines, t.label, viewingPast ? "Past topic" : "New topic");
  renderEntities(t.entities); renderCrm(t.crm);
}

export function updateHistoryModeUi() {
  const viewingPast = !isViewingLive();
  dom.historyMode.hidden = !viewingPast;
  document.body.classList.toggle("history-mode", viewingPast);
}

export function viewTopic(id) {
  if (id === state.currentId) state.viewingId = null;
  else state.viewingId = id;
  updateHistoryModeUi(); renderViewedTopic(); renderHistoryList();
}

async function _loadAndViewTopic(id) {
  const t = getTopic(id);
  if (!t) return;
  if (t.fromServer && t.serverId && t.lines.length === 0) {
    try {
      const res = await fetch(`/history/${encodeURIComponent(t.serverId)}`);
      if (res.ok) {
        const data = await res.json();
        t.lines = Array.isArray(data.lines)
          ? data.lines.map((l) => ({ ts: l.ts, text: l.text })) : [];
        t.entities = data.entities && typeof data.entities === "object" ? data.entities : {};
        t.crm = data.crm && typeof data.crm === "object" ? data.crm : {};
      }
    } catch (e) {
      console.warn("Could not load meeting details from server:", e);
    }
  }
  viewTopic(id);
}

export function backToLive() {
  state.viewingId = null;
  updateHistoryModeUi(); renderViewedTopic(); renderHistoryList();
}

export function deleteTopic(id) {
  if (id === state.currentId) return;
  const t = getTopic(id);
  const label = t ? (t.label || "Untitled topic") : "this topic";
  if (!confirm(`Delete "${label}" from history? This cannot be undone.`)) return;
  const idx = state.topics.findIndex((x) => x.id === id);
  if (idx === -1) return;
  const serverId = t && t.serverId ? t.serverId : null;
  state.topics.splice(idx, 1);
  if (state.viewingId === id) {
    state.viewingId = null;
    updateHistoryModeUi();
  }
  persistTopics();
  renderHistoryList();
  renderViewedTopic();
  if (serverId) {
    fetch(`/history/${encodeURIComponent(serverId)}`, { method: "DELETE" })
      .catch((e) => console.warn("Could not delete meeting from server:", e));
  }
}

export function clearAllHistory() {
  const pastTopics = state.topics.filter((t) => t.id !== state.currentId);
  if (pastTopics.length === 0) return;
  if (!confirm(`Clear ${pastTopics.length} past topic(s) from history? This cannot be undone.`)) return;
  const serverIds = pastTopics.map((t) => t.serverId).filter(Boolean);
  const live = state.currentId != null ? getTopic(state.currentId) : null;
  state.topics.length = 0;
  if (live) state.topics.push(live);
  state.viewingId = null;
  persistTopics();
  updateHistoryModeUi();
  renderHistoryList();
  renderViewedTopic();
  for (const sid of serverIds) {
    fetch(`/history/${encodeURIComponent(sid)}`, { method: "DELETE" })
      .catch((e) => console.warn("Could not delete server meeting during clear:", e));
  }
}

export function renderHistoryList() {
  dom.historyList.innerHTML = "";
  if (state.topics.length === 0) {
    const li = document.createElement("li");
    li.className = "empty"; li.textContent = "No topics yet";
    dom.historyList.appendChild(li); return;
  }
  const reversed = state.topics.slice().reverse();
  const pool = state.searchQuery ? reversed : reversed.slice(0, MAX_HISTORY);
  const ordered = pool.filter((t) => topicMatchesQuery(t, state.searchQuery));
  if (ordered.length === 0) {
    const li = document.createElement("li");
    li.className = "empty"; li.textContent = "No topics match your search";
    dom.historyList.appendChild(li); return;
  }
  if (state.searchQuery && state.topics.length > MAX_HISTORY) {
    const info = document.createElement("li");
    info.className = "search-scope-note";
    info.textContent = `Searching all ${state.topics.length} topics`;
    dom.historyList.appendChild(info);
  }
  let lastSessionKey = undefined;
  let firstDivider = true;
  for (const t of ordered) {
    const sessionKey = t.sessionId != null ? t.sessionId : t.startedAt;
    if (sessionKey !== lastSessionKey) {
      const divider = document.createElement("li");
      divider.className = firstDivider
        ? "h-session-divider h-session-divider--first" : "h-session-divider";
      divider.textContent = fmtSessionLabel(sessionKey);
      dom.historyList.appendChild(divider);
      lastSessionKey = sessionKey;
      firstDivider = false;
    }
    const li = document.createElement("li");
    const isLive = t.id === state.currentId;
    const isActive = (state.viewingId != null ? t.id === state.viewingId : isLive);
    if (isLive) li.classList.add("live");
    if (isActive) li.classList.add("active");
    if (t.fromServer && !isLive) li.classList.add("from-server");
    const label = document.createElement("div");
    label.className = "h-label";
    const labelText = t.label || "Untitled topic";
    const labelMatches = state.searchQuery && labelText.toLowerCase().includes(state.searchQuery.toLowerCase());
    if (labelMatches) label.innerHTML = highlightHtml(labelText, state.searchQuery);
    else label.textContent = labelText;
    if (t.fromServer && !isLive) {
      const tag = document.createElement("span");
      tag.className = "h-server-tag";
      tag.title = "Loaded from server history";
      tag.textContent = "saved";
      label.appendChild(tag);
    }
    if (state.searchQuery && !labelMatches) {
      const snippet = getMatchSnippet(t, state.searchQuery);
      if (snippet) {
        const snippetEl = document.createElement("div");
        snippetEl.className = "h-snippet";
        snippetEl.innerHTML = highlightHtml(snippet, state.searchQuery);
        li.appendChild(label);
        li.appendChild(snippetEl);
      } else {
        li.appendChild(label);
      }
    } else {
      li.appendChild(label);
    }
    const time = document.createElement("div");
    time.className = "h-time";
    const topicDate = new Date(t.startedAt * 1000);
    if (state.searchQuery) {
      const dateStr = topicDate.toLocaleDateString(undefined, {
        month: "short", day: "numeric", year: "numeric",
      });
      time.textContent = `${dateStr} \u00b7 ${topicDate.toLocaleTimeString()}`;
    } else {
      time.textContent = topicDate.toLocaleTimeString();
    }
    if (!isLive) {
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "h-delete-btn";
      delBtn.title = "Delete this topic";
      delBtn.textContent = "×";
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation(); deleteTopic(t.id);
      });
      li.appendChild(delBtn);
    }
    li.appendChild(time);
    li.addEventListener("click", () => _loadAndViewTopic(t.id));
    dom.historyList.appendChild(li);
  }
}

export async function loadServerHistory() {
  try {
    const res = await fetch("/history");
    if (!res.ok) return;
    const meetings = await res.json();
    if (!Array.isArray(meetings) || meetings.length === 0) return;
    const knownServerIds = new Set(state.topics.map((t) => t.serverId).filter(Boolean));
    const { normalizeTopic } = await import("./utils.js");
    let added = 0;
    for (const m of meetings) {
      if (!m || !m.id) continue;
      if (knownServerIds.has(m.id)) continue;
      const stub = normalizeTopic({
        label: m.label || "Untitled topic",
        startedAt: m.started_at,
        sessionId: m.session_id,
        lines: [], entities: {}, crm: {},
        serverId: m.id, fromServer: true,
      });
      state.topics.push(stub);
      added++;
    }
    if (added > 0) {
      state.topics.sort((a, b) => a.startedAt - b.startedAt);
      renderHistoryList();
    }
  } catch (e) {
    console.warn("Could not load server history:", e);
  }
}

export function startNewTopic(label, ts, serverId) {
  if (!state.currentSessionId) state.currentSessionId = Date.now() / 1000;
  const topic = {
    id: state.nextId++,
    label: label || "Untitled topic",
    startedAt: ts || Date.now() / 1000,
    sessionId: state.currentSessionId,
    lines: [], entities: {}, crm: {},
    serverId: serverId || null,
    fromServer: false,
  };
  state.topics.push(topic);
  state.currentId = topic.id;
  // schedule a save (lazy import avoids a top-level cycle).
  import("./utils.js").then(({ scheduleSave }) => scheduleSave());
  return topic;
}
