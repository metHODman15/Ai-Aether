(() => {
  const statusEl = document.getElementById("status");
  const transcriptEl = document.getElementById("transcript");
  const entitiesEl = document.getElementById("entities");
  const accountsEl = document.getElementById("accounts");
  const opportunitiesEl = document.getElementById("opportunities");
  const topicEl = document.getElementById("topic");
  const topicLabelEl = topicEl.querySelector(".topic-label");
  const historyListEl = document.getElementById("historyList");
  const historyModeEl = document.getElementById("historyMode");
  const backToLiveBtn = document.getElementById("backToLive");
  const clearHistoryBtn = document.getElementById("clearHistoryBtn");
  const sensitivityEl = document.getElementById("sensitivity");
  const audioChunkEl = document.getElementById("audioChunkSeconds");
  const audioSampleRateEl = document.getElementById("audioSampleRate");
  const historySearchEl = document.getElementById("historySearch");
  const searchNavEl = document.getElementById("searchNav");
  const searchCountEl = document.getElementById("searchCount");
  const searchPrevBtn = document.getElementById("searchPrev");
  const searchNextBtn = document.getElementById("searchNext");

  // Document mode elements
  const liveViewEl = document.getElementById("liveView");
  const docViewEl = document.getElementById("docView");
  const docUnitsEl = document.getElementById("docUnits");
  const docTitleEl = document.getElementById("docTitle");
  const docProgressLabelEl = document.getElementById("docProgressLabel");
  const docProgressBarEl = document.getElementById("docProgressBar");
  const docBackToLiveBtn = document.getElementById("docBackToLive");
  const docSearchEl = document.getElementById("docSearch");
  const docStageFilterEl = document.getElementById("docStageFilter");
  const docCustomerFilterEl = document.getElementById("docCustomerFilter");
  const docUnitCountEl = document.getElementById("docUnitCount");
  const uploadBtn = document.getElementById("uploadBtn");
  const uploadInput = document.getElementById("uploadInput");
  const docSummaryEl = document.getElementById("docSummary");
  const docSummaryBodyEl = document.getElementById("docSummaryBody");
  const docDownloadCsvBtn = document.getElementById("docDownloadCsv");

  let searchQuery = "";
  let docUnitsData = [];
  let searchHits = [];
  let currentHitIndex = -1;
  let docChartInstances = [];
  const seenStages = new Set();

  let demoActive = false;
  let demoTimers = [];

  const PALETTE = [
    "#38bdf8", "#a78bfa", "#f472b6", "#fb923c",
    "#facc15", "#34d399", "#f87171", "#60a5fa",
    "#c084fc", "#fbbf24",
  ];

  const stagePie = new Chart(document.getElementById("stagePie"), {
    type: "pie",
    data: { labels: [], datasets: [{ data: [], backgroundColor: PALETTE }] },
    options: {
      responsive: true,
      animation: { duration: 400 },
      plugins: {
        legend: { position: "bottom", labels: { color: "#e2e8f0" } },
      },
    },
  });

  const amountLine = new Chart(document.getElementById("amountLine"), {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label: "Deal amount",
        data: [],
        borderColor: "#38bdf8",
        backgroundColor: "rgba(56,189,248,0.15)",
        fill: true,
        tension: 0.3,
        pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      animation: { duration: 400 },
      plugins: { legend: { labels: { color: "#e2e8f0" } } },
      scales: {
        x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(148,163,184,0.1)" } },
        y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(148,163,184,0.1)" } },
      },
    },
  });

  const MAX_HISTORY = 10;
  const MAX_ARCHIVE = 2000;
  const MAX_LINES_PER_TOPIC = 200;
  const STORAGE_KEY = "meetingAssistant_topics";
  const topics = [];
  let currentId = null;
  let viewingId = null;
  let nextId = 1;
  let _saveTimer = null;
  let currentSessionId = null;

  function fmtSessionLabel(sessionId) {
    const d = new Date(sessionId * 1000);
    const datePart = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
    const timePart = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    return `${datePart} \u00b7 ${timePart}`;
  }

  function getTopic(id) { return topics.find((t) => t.id === id) || null; }
  function currentTopic() { return getTopic(currentId); }
  function viewedTopic() { return getTopic(viewingId != null ? viewingId : currentId); }
  function isViewingLive() { return viewingId == null || viewingId === currentId; }

  function scheduleSave() {
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(persistTopics, 1500);
  }

  function persistTopics() {
    try {
      const toStore = topics.slice(-MAX_ARCHIVE);
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ nextId, topics: toStore }));
    } catch (e) {
      console.warn("Could not persist topics to localStorage:", e);
    }
  }

  function normalizeTopic(t) {
    const startedAt = typeof t.startedAt === "number" ? t.startedAt : Date.now() / 1000;
    return {
      id: typeof t.id === "number" ? t.id : nextId++,
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

  function loadPersistedTopics() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (!Array.isArray(data.topics)) return;
      for (const t of data.topics) {
        if (t && typeof t === "object") topics.push(normalizeTopic(t));
      }
      if (typeof data.nextId === "number" && data.nextId > nextId) nextId = data.nextId;
    } catch (e) {
      console.warn("Could not restore topics from localStorage:", e);
    }
  }

  function setStatus(connected) {
    if (demoActive) return;
    statusEl.textContent = connected ? "Connected" : "Disconnected";
    statusEl.classList.toggle("connected", connected);
    statusEl.classList.toggle("disconnected", !connected);
  }

  function setDemoStatus() {
    statusEl.textContent = "Demo Mode";
    statusEl.classList.remove("disconnected");
    statusEl.classList.add("connected");
    statusEl.classList.add("demo-status");
  }

  function fmtAmount(n) {
    if (n == null) return "—";
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD", maximumFractionDigits: 0,
    }).format(n);
  }

  function setTopicLabel(label, viewing) {
    topicLabelEl.textContent = label
      ? (viewing ? `${label} (history)` : label)
      : "Waiting for first topic…";
    topicEl.classList.toggle("active", !!label);
  }

  function flashTopic() {
    topicEl.classList.add("flash");
    setTimeout(() => topicEl.classList.remove("flash"), 800);
  }

  function setField(name, value) {
    const el = entitiesEl.querySelector(`[data-field="${name}"]`);
    if (!el) return;
    if (Array.isArray(value)) {
      el.textContent = value.length ? value.join(", ") : "—";
    } else if (name === "deal_amount") {
      el.textContent = value == null ? "—" : fmtAmount(value);
    } else {
      el.textContent = value || "—";
    }
  }

  function renderEntities(entities) {
    const e = entities || {};
    setField("customer_name", e.customer_name);
    setField("contact_name", e.contact_name);
    setField("deal_amount", e.deal_amount);
    setField("deal_stage", e.deal_stage);
    setField("keywords", e.keywords || []);
  }

  function renderRecordsEmpty(listEl, message) {
    listEl.innerHTML = "";
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = message;
    listEl.appendChild(li);
  }

  function renderAccounts(items) {
    if (!items || items.length === 0) { renderRecordsEmpty(accountsEl, "No matches"); return; }
    accountsEl.innerHTML = "";
    for (const a of items) {
      const li = document.createElement("li");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = a.Name || "(unnamed)";
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = [a.Industry, a.Type, a.Website].filter(Boolean).join(" · ") || a.Id || "";
      li.appendChild(name); li.appendChild(meta); accountsEl.appendChild(li);
    }
  }

  function renderOpportunities(items) {
    if (!items || items.length === 0) { renderRecordsEmpty(opportunitiesEl, "No matches"); return; }
    opportunitiesEl.innerHTML = "";
    for (const o of items) {
      const li = document.createElement("li");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = o.Name || "(unnamed)";
      const meta = document.createElement("div");
      meta.className = "meta";
      const accountName = o.Account && o.Account.Name ? o.Account.Name : "";
      meta.textContent = [accountName, o.StageName, fmtAmount(o.Amount), o.CloseDate].filter(Boolean).join(" · ");
      li.appendChild(name); li.appendChild(meta); opportunitiesEl.appendChild(li);
    }
  }

  function renderCrm(crm) {
    const data = crm || {};
    renderAccounts(data.accounts);
    renderOpportunities(data.opportunities);
    const dist = data.stage_distribution || [];
    stagePie.data.labels = dist.map((d) => d.stage);
    stagePie.data.datasets[0].data = dist.map((d) => d.count);
    stagePie.update();
    const timeline = data.amount_timeline || [];
    amountLine.data.labels = timeline.map((t) => t.date);
    amountLine.data.datasets[0].data = timeline.map((t) => t.amount);
    amountLine.update();
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function escapeRegExp(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function highlightHtml(text, query) {
    const safe = escapeHtml(text);
    if (!query) return safe;
    const re = new RegExp(escapeRegExp(query), "gi");
    return safe.replace(re, (m) => `<mark class="search-hit">${m}</mark>`);
  }

  function collectSearchHits() {
    searchHits = Array.from(transcriptEl.querySelectorAll("mark.search-hit"));
    currentHitIndex = searchHits.length > 0 ? 0 : -1;
    searchHits.forEach((el, i) => el.classList.toggle("search-hit-current", i === 0));
    updateSearchNav();
    if (currentHitIndex >= 0) searchHits[0].scrollIntoView({ block: "nearest" });
  }

  function updateSearchNav() {
    if (searchHits.length === 0) { searchNavEl.hidden = true; return; }
    searchNavEl.hidden = false;
    searchCountEl.textContent = `${currentHitIndex + 1} of ${searchHits.length}`;
    if (currentHitIndex >= 0) searchHits[currentHitIndex].scrollIntoView({ block: "nearest" });
  }

  function navigateHit(dir) {
    if (searchHits.length === 0) return;
    searchHits[currentHitIndex]?.classList.remove("search-hit-current");
    currentHitIndex = (currentHitIndex + dir + searchHits.length) % searchHits.length;
    searchHits[currentHitIndex].classList.add("search-hit-current");
    updateSearchNav();
  }

  function refreshSearchHitsQuiet() {
    const prev = searchHits[currentHitIndex] || null;
    searchHits = Array.from(transcriptEl.querySelectorAll("mark.search-hit"));
    if (searchHits.length === 0) { currentHitIndex = -1; searchNavEl.hidden = true; return; }
    const newIdx = prev ? searchHits.indexOf(prev) : -1;
    currentHitIndex = newIdx >= 0 ? newIdx : 0;
    searchHits.forEach((el, i) => el.classList.toggle("search-hit-current", i === currentHitIndex));
    searchNavEl.hidden = false;
    searchCountEl.textContent = `${currentHitIndex + 1} of ${searchHits.length}`;
  }

  function entityValuesText(entities) {
    const e = entities || {};
    const parts = [];
    if (e.customer_name) parts.push(e.customer_name);
    if (e.contact_name) parts.push(e.contact_name);
    if (e.deal_stage) parts.push(e.deal_stage);
    if (e.deal_amount != null) parts.push(String(e.deal_amount));
    if (Array.isArray(e.keywords)) parts.push(e.keywords.join(" "));
    return parts.join(" ");
  }

  function topicMatchesQuery(t, q) {
    if (!q) return true;
    const needle = q.toLowerCase();
    if ((t.label || "").toLowerCase().includes(needle)) return true;
    if (entityValuesText(t.entities).toLowerCase().includes(needle)) return true;
    for (const line of t.lines || []) {
      if ((line.text || "").toLowerCase().includes(needle)) return true;
    }
    return false;
  }

  function snippetAround(text, query) {
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

  function getMatchSnippet(t, q) {
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

  function renderTranscriptLines(lines, headerLabel, headerNote) {
    transcriptEl.innerHTML = "";
    if (headerLabel) {
      const banner = document.createElement("div");
      banner.className = "topic-shift-banner";
      banner.textContent = headerNote ? `${headerNote}: ${headerLabel}` : `Topic: ${headerLabel}`;
      transcriptEl.appendChild(banner);
    }
    if (!lines || lines.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No transcript captured for this topic.";
      transcriptEl.appendChild(empty);
    } else {
      for (const line of lines) {
        if (line.error) {
          const el = document.createElement("div");
          el.className = "line error";
          el.textContent = line.text;
          transcriptEl.appendChild(el);
        } else {
          appendTranscriptLine(line.text, line.ts);
        }
      }
      if (searchQuery) { collectSearchHits(); return; }
    }
    searchHits = []; currentHitIndex = -1; searchNavEl.hidden = true;
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }

  function appendTranscriptLine(text, ts) {
    const placeholder = transcriptEl.querySelector(".empty-state");
    if (placeholder) placeholder.remove();
    const time = new Date(ts * 1000).toLocaleTimeString();
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span class="ts"></span><span class="text"></span>`;
    line.querySelector(".ts").textContent = time;
    const textEl = line.querySelector(".text");
    if (searchQuery && text.toLowerCase().includes(searchQuery.toLowerCase())) {
      textEl.innerHTML = highlightHtml(text, searchQuery);
    } else {
      textEl.textContent = text;
    }
    transcriptEl.appendChild(line);
    if (!searchQuery) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }

  function renderHistoryList() {
    historyListEl.innerHTML = "";
    if (topics.length === 0) {
      const li = document.createElement("li");
      li.className = "empty"; li.textContent = "No topics yet";
      historyListEl.appendChild(li); return;
    }
    const reversed = topics.slice().reverse();
    const pool = searchQuery ? reversed : reversed.slice(0, MAX_HISTORY);
    const ordered = pool.filter((t) => topicMatchesQuery(t, searchQuery));
    if (ordered.length === 0) {
      const li = document.createElement("li");
      li.className = "empty"; li.textContent = "No topics match your search";
      historyListEl.appendChild(li); return;
    }
    if (searchQuery && topics.length > MAX_HISTORY) {
      const info = document.createElement("li");
      info.className = "search-scope-note";
      info.textContent = `Searching all ${topics.length} topics`;
      historyListEl.appendChild(info);
    }
    let lastSessionKey = undefined;
    let firstDivider = true;
    for (const t of ordered) {
      const sessionKey = t.sessionId != null ? t.sessionId : t.startedAt;
      if (sessionKey !== lastSessionKey) {
        const divider = document.createElement("li");
        divider.className = firstDivider ? "h-session-divider h-session-divider--first" : "h-session-divider";
        divider.textContent = fmtSessionLabel(sessionKey);
        historyListEl.appendChild(divider);
        lastSessionKey = sessionKey;
        firstDivider = false;
      }
      const li = document.createElement("li");
      const isLive = t.id === currentId;
      const isActive = (viewingId != null ? t.id === viewingId : isLive);
      if (isLive) li.classList.add("live");
      if (isActive) li.classList.add("active");
      if (t.fromServer && !isLive) li.classList.add("from-server");
      const label = document.createElement("div");
      label.className = "h-label";
      const labelText = t.label || "Untitled topic";
      const labelMatches = searchQuery && labelText.toLowerCase().includes(searchQuery.toLowerCase());
      if (labelMatches) {
        label.innerHTML = highlightHtml(labelText, searchQuery);
      } else {
        label.textContent = labelText;
      }
      if (t.fromServer && !isLive) {
        const tag = document.createElement("span");
        tag.className = "h-server-tag";
        tag.title = "Loaded from server history";
        tag.textContent = "saved";
        label.appendChild(tag);
      }
      if (searchQuery && !labelMatches) {
        const snippet = getMatchSnippet(t, searchQuery);
        if (snippet) {
          const snippetEl = document.createElement("div");
          snippetEl.className = "h-snippet";
          snippetEl.innerHTML = highlightHtml(snippet, searchQuery);
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
      if (searchQuery) {
        const dateStr = topicDate.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
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
        delBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteTopic(t.id); });
        li.appendChild(delBtn);
      }
      li.appendChild(time);
      li.addEventListener("click", () => _loadAndViewTopic(t.id));
      historyListEl.appendChild(li);
    }
  }

  function renderViewedTopic() {
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

  function updateHistoryModeUi() {
    const viewingPast = !isViewingLive();
    historyModeEl.hidden = !viewingPast;
    document.body.classList.toggle("history-mode", viewingPast);
  }

  function viewTopic(id) {
    if (id === currentId) { viewingId = null; } else { viewingId = id; }
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
            ? data.lines.map((l) => ({ ts: l.ts, text: l.text }))
            : [];
          t.entities = data.entities && typeof data.entities === "object" ? data.entities : {};
          t.crm = data.crm && typeof data.crm === "object" ? data.crm : {};
        }
      } catch (e) {
        console.warn("Could not load meeting details from server:", e);
      }
    }
    viewTopic(id);
  }

  function backToLive() {
    viewingId = null;
    updateHistoryModeUi(); renderViewedTopic(); renderHistoryList();
  }

  function deleteTopic(id) {
    if (id === currentId) return;
    const t = getTopic(id);
    const label = t ? (t.label || "Untitled topic") : "this topic";
    if (!confirm(`Delete "${label}" from history? This cannot be undone.`)) return;
    const idx = topics.findIndex((t) => t.id === id);
    if (idx === -1) return;
    const serverId = t && t.serverId ? t.serverId : null;
    topics.splice(idx, 1);
    if (viewingId === id) {
      viewingId = null;
      updateHistoryModeUi();
    }
    persistTopics();
    renderHistoryList();
    renderViewedTopic();
    if (serverId) {
      fetch(`/history/${encodeURIComponent(serverId)}`, { method: "DELETE" }).catch((e) => {
        console.warn("Could not delete meeting from server:", e);
      });
    }
  }

  function clearAllHistory() {
    const pastTopics = topics.filter((t) => t.id !== currentId);
    if (pastTopics.length === 0) return;
    if (!confirm(`Clear ${pastTopics.length} past topic(s) from history? This cannot be undone.`)) return;
    const serverIds = pastTopics.map((t) => t.serverId).filter(Boolean);
    const live = currentId != null ? getTopic(currentId) : null;
    topics.length = 0;
    if (live) topics.push(live);
    viewingId = null;
    persistTopics();
    updateHistoryModeUi();
    renderHistoryList();
    renderViewedTopic();
    for (const sid of serverIds) {
      fetch(`/history/${encodeURIComponent(sid)}`, { method: "DELETE" }).catch((e) => {
        console.warn("Could not delete server meeting during clear:", e);
      });
    }
  }

  backToLiveBtn.addEventListener("click", backToLive);
  clearHistoryBtn.addEventListener("click", clearAllHistory);
  searchPrevBtn.addEventListener("click", () => navigateHit(-1));
  searchNextBtn.addEventListener("click", () => navigateHit(1));

  historySearchEl.addEventListener("input", () => {
    searchQuery = historySearchEl.value.trim();
    renderHistoryList(); renderViewedTopic();
  });

  async function loadSettings() {
    try {
      const res = await fetch("/settings");
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.sensitivity) {
        sensitivityEl.value = data.sensitivity;
        lastConfirmedSensitivity = data.sensitivity;
      }
      if (data && data.audio_chunk_seconds != null) {
        audioChunkEl.value = data.audio_chunk_seconds;
        if (data.audio_chunk_seconds_min != null) audioChunkEl.min = data.audio_chunk_seconds_min;
        if (data.audio_chunk_seconds_max != null) audioChunkEl.max = data.audio_chunk_seconds_max;
        lastConfirmedAudioChunk = data.audio_chunk_seconds;
      }
      if (data && Array.isArray(data.audio_sample_rate_options) && data.audio_sample_rate_options.length) {
        const currentVal = data.audio_sample_rate != null ? data.audio_sample_rate : Number(audioSampleRateEl.value);
        audioSampleRateEl.innerHTML = "";
        for (const rate of data.audio_sample_rate_options) {
          const opt = document.createElement("option");
          opt.value = String(rate);
          opt.textContent = rate.toLocaleString() + " Hz";
          if (rate === currentVal) opt.selected = true;
          audioSampleRateEl.appendChild(opt);
        }
        if (data.audio_sample_rate != null) {
          audioSampleRateEl.value = String(data.audio_sample_rate);
          lastConfirmedSampleRate = data.audio_sample_rate;
        }
      } else if (data && data.audio_sample_rate != null) {
        audioSampleRateEl.value = String(data.audio_sample_rate);
        lastConfirmedSampleRate = data.audio_sample_rate;
      }
    } catch (e) { console.error("Failed to load settings", e); }
  }

  let lastConfirmedSensitivity = sensitivityEl.value;
  let lastConfirmedAudioChunk = Number(audioChunkEl.value);
  let lastConfirmedSampleRate = Number(audioSampleRateEl.value);

  function showSettingFeedback(el, success) {
    const existing = el.parentElement.querySelector(".setting-feedback");
    if (existing) existing.remove();
    const badge = document.createElement("span");
    badge.className = "setting-feedback " + (success ? "setting-feedback--ok" : "setting-feedback--err");
    badge.textContent = success ? "Saved \u2713" : "Failed";
    el.insertAdjacentElement("afterend", badge);
    setTimeout(() => badge.classList.add("setting-feedback--hide"), 1600);
    setTimeout(() => badge.remove(), 2100);
  }

  sensitivityEl.addEventListener("change", async () => {
    const value = sensitivityEl.value;
    sensitivityEl.disabled = true;
    let ok = false;
    try {
      const res = await fetch("/settings/sensitivity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sensitivity: value }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      lastConfirmedSensitivity = data.sensitivity || value;
      sensitivityEl.value = lastConfirmedSensitivity;
      ok = true;
    } catch (e) {
      console.error("Failed to update sensitivity", e);
      sensitivityEl.value = lastConfirmedSensitivity;
    } finally {
      sensitivityEl.disabled = false;
      showSettingFeedback(sensitivityEl, ok);
    }
  });

  audioChunkEl.addEventListener("change", async () => {
    const value = Number(audioChunkEl.value);
    audioChunkEl.disabled = true;
    let ok = false;
    try {
      const res = await fetch("/settings/audio_chunk_seconds", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_chunk_seconds: value }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      lastConfirmedAudioChunk = data.audio_chunk_seconds != null ? data.audio_chunk_seconds : value;
      audioChunkEl.value = lastConfirmedAudioChunk;
      ok = true;
    } catch (e) {
      console.error("Failed to update audio chunk seconds", e);
      audioChunkEl.value = lastConfirmedAudioChunk;
    } finally {
      audioChunkEl.disabled = false;
      showSettingFeedback(audioChunkEl, ok);
    }
  });

  audioSampleRateEl.addEventListener("change", async () => {
    const value = Number(audioSampleRateEl.value);
    audioSampleRateEl.disabled = true;
    let ok = false;
    try {
      const res = await fetch("/settings/audio_sample_rate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_sample_rate: value }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      lastConfirmedSampleRate = data.audio_sample_rate != null ? data.audio_sample_rate : value;
      audioSampleRateEl.value = String(lastConfirmedSampleRate);
      ok = true;
    } catch (e) {
      console.error("Failed to update audio sample rate", e);
      audioSampleRateEl.value = String(lastConfirmedSampleRate);
    } finally {
      audioSampleRateEl.disabled = false;
      showSettingFeedback(audioSampleRateEl, ok);
    }
  });

  function startNewTopic(label, ts, serverId) {
    if (!currentSessionId) currentSessionId = Date.now() / 1000;
    const topic = {
      id: nextId++,
      label: label || "Untitled topic",
      startedAt: ts || Date.now() / 1000,
      sessionId: currentSessionId,
      lines: [], entities: {}, crm: {},
      serverId: serverId || null,
      fromServer: false,
    };
    topics.push(topic);
    currentId = topic.id;
    scheduleSave();
    return topic;
  }

  // ── Document mode ─────────────────────────────────────────────────────────

  uploadBtn.addEventListener("click", () => uploadInput.click());
  uploadInput.addEventListener("change", handleUpload);
  docBackToLiveBtn.addEventListener("click", exitDocMode);

  async function handleUpload() {
    const file = uploadInput.files[0];
    if (!file) return;
    if (demoActive) return;
    uploadInput.value = "";
    enterDocMode(file.name);
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/upload", { method: "POST", body: formData });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        showDocError(err.detail || "Upload failed");
      }
    } catch (e) {
      showDocError(String(e));
    }
  }

  function enterDocMode(filename) {
    for (const ch of docChartInstances) { try { ch.destroy(); } catch (_) {} }
    docChartInstances = [];
    docUnitsEl.innerHTML = "";
    docUnitsData = [];
    docSummaryEl.hidden = true;
    docSummaryBodyEl.innerHTML = "";
    docDownloadCsvBtn.hidden = true;
    docTitleEl.textContent = filename;
    docProgressLabelEl.textContent = "Processing…";
    docProgressBarEl.style.width = "0%";
    docSearchEl.value = "";
    docStageFilterEl.innerHTML = '<option value="">All stages</option>';
    seenStages.clear();
    docCustomerFilterEl.value = "";
    docUnitCountEl.textContent = "";
    liveViewEl.hidden = true;
    docViewEl.hidden = false;
  }

  function applyDocFilter() {
    const raw = docSearchEl.value.trim();
    const q = raw.toLowerCase();
    const stageQ = docStageFilterEl.value;
    const customerQ = docCustomerFilterEl.value.trim().toLowerCase();
    const cards = docUnitsEl.querySelectorAll(".doc-unit-card");
    let visible = 0;
    for (const card of cards) {
      const haystack = (card.dataset.search || "").toLowerCase();
      const textMatch = q === "" || haystack.includes(q);
      const stageMatch = !stageQ || (card.dataset.stage || "") === stageQ;
      const customerMatch = !customerQ || (card.dataset.customer || "").includes(customerQ);
      const match = textMatch && stageMatch && customerMatch;
      card.hidden = !match;
      if (match) visible++;

      const textEl = card.querySelector(".doc-unit-text[data-original]");
      if (textEl) {
        if (match && raw) {
          textEl.innerHTML = highlightHtml(textEl.dataset.original, raw);
        } else {
          textEl.textContent = textEl.dataset.original;
        }
      }

      const dds = card.querySelectorAll(".doc-unit-entities dd[data-original]");
      for (const dd of dds) {
        if (match && raw) {
          dd.innerHTML = highlightHtml(dd.dataset.original, raw);
        } else {
          dd.textContent = dd.dataset.original;
        }
      }
    }
    const total = cards.length;
    docUnitCountEl.textContent = total > 0 ? `${visible} of ${total} unit${total !== 1 ? "s" : ""}` : "";
  }

  docSearchEl.addEventListener("input", applyDocFilter);
  docStageFilterEl.addEventListener("change", applyDocFilter);
  docCustomerFilterEl.addEventListener("input", applyDocFilter);

  function exitDocMode() {
    liveViewEl.hidden = false;
    docViewEl.hidden = true;
  }

  function showDocError(msg) {
    const errEl = document.createElement("div");
    errEl.className = "doc-error";
    errEl.textContent = `Error: ${msg}`;
    docUnitsEl.prepend(errEl);
    docProgressLabelEl.textContent = "Failed";
  }

  async function requestDocSummary(units) {
    docSummaryBodyEl.innerHTML = '<span class="doc-summary-loading">Generating summary\u2026</span>';
    docSummaryEl.hidden = false;
    docSummaryEl.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const res = await fetch("/summarise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ units }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const text = (data.summary || "").trim();
      docSummaryBodyEl.innerHTML = "";
      if (!text) {
        const p = document.createElement("p");
        p.className = "doc-summary-empty";
        p.textContent = "No summary available.";
        docSummaryBodyEl.appendChild(p);
      } else {
        const ul = document.createElement("ul");
        ul.className = "doc-summary-list";
        for (const raw of text.split("\n")) {
          const line = raw.replace(/^[•\-\*]\s*/, "").trim();
          if (!line) continue;
          const li = document.createElement("li");
          li.textContent = line;
          ul.appendChild(li);
        }
        docSummaryBodyEl.appendChild(ul);
      }
    } catch (e) {
      docSummaryBodyEl.innerHTML = `<span class="doc-summary-error">Summary unavailable: ${escapeHtml(String(e))}</span>`;
    }
  }

  function appendDocUnit(evt) {
    const card = document.createElement("div");
    card.className = "doc-unit-card";

    const header = document.createElement("div");
    header.className = "doc-unit-header";
    header.textContent = `Unit ${evt.unit_index + 1} of ${evt.total_units}`;
    card.appendChild(header);

    const textWrap = document.createElement("div");
    textWrap.className = "doc-unit-text";
    textWrap.dataset.original = evt.text;
    textWrap.textContent = evt.text;
    card.appendChild(textWrap);

    const entities = evt.entities || {};
    const entDiv = document.createElement("dl");
    entDiv.className = "doc-unit-entities";
    const entityFields = [
      ["Customer", entities.customer_name || "—"],
      ["Contact", entities.contact_name || "—"],
      ["Amount", entities.deal_amount != null ? fmtAmount(entities.deal_amount) : "—"],
      ["Stage", entities.deal_stage || "—"],
      ["Keywords", (entities.keywords || []).join(", ") || "—"],
    ];
    for (const [label, value] of entityFields) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.dataset.original = value;
      dd.textContent = value;
      entDiv.appendChild(dt);
      entDiv.appendChild(dd);
    }
    card.appendChild(entDiv);

    const crm = evt.crm || {};
    const chartsRow = document.createElement("div");
    chartsRow.className = "doc-unit-charts";

    const pieWrap = document.createElement("div");
    pieWrap.className = "doc-unit-chart-wrap";
    const pieTitle = document.createElement("div");
    pieTitle.className = "doc-unit-chart-title";
    pieTitle.textContent = "Opportunity Stages";
    const pieCanvas = document.createElement("canvas");
    pieWrap.appendChild(pieTitle); pieWrap.appendChild(pieCanvas);
    chartsRow.appendChild(pieWrap);

    const lineWrap = document.createElement("div");
    lineWrap.className = "doc-unit-chart-wrap";
    const lineTitle = document.createElement("div");
    lineTitle.className = "doc-unit-chart-title";
    lineTitle.textContent = "Deal Amounts";
    const lineCanvas = document.createElement("canvas");
    lineWrap.appendChild(lineTitle); lineWrap.appendChild(lineCanvas);
    chartsRow.appendChild(lineWrap);

    card.appendChild(chartsRow);

    const recordsDiv = document.createElement("div");
    recordsDiv.className = "doc-unit-records";

    const accHeader = document.createElement("h4");
    accHeader.textContent = "Accounts";
    recordsDiv.appendChild(accHeader);
    const accList = document.createElement("ul");
    accList.className = "record-list";
    if ((crm.accounts || []).length === 0) {
      accList.innerHTML = '<li class="empty">No matches</li>';
    } else {
      for (const a of crm.accounts) {
        const li = document.createElement("li");
        li.innerHTML = `<div class="name">${escapeHtml(a.Name || "(unnamed)")}</div>` +
          `<div class="meta">${escapeHtml([a.Industry, a.Type].filter(Boolean).join(" · "))}</div>`;
        accList.appendChild(li);
      }
    }
    recordsDiv.appendChild(accList);

    const oppHeader = document.createElement("h4");
    oppHeader.textContent = "Opportunities";
    recordsDiv.appendChild(oppHeader);
    const oppList = document.createElement("ul");
    oppList.className = "record-list";
    if ((crm.opportunities || []).length === 0) {
      oppList.innerHTML = '<li class="empty">No matches</li>';
    } else {
      for (const o of crm.opportunities) {
        const li = document.createElement("li");
        const accountName = o.Account && o.Account.Name ? o.Account.Name : "";
        li.innerHTML = `<div class="name">${escapeHtml(o.Name || "(unnamed)")}</div>` +
          `<div class="meta">${escapeHtml([accountName, o.StageName, fmtAmount(o.Amount), o.CloseDate].filter(Boolean).join(" · "))}</div>`;
        oppList.appendChild(li);
      }
    }
    recordsDiv.appendChild(oppList);
    card.appendChild(recordsDiv);

    const searchParts = [
      evt.text || "",
      entities.customer_name || "",
      entities.contact_name || "",
      entities.deal_stage || "",
      (entities.keywords || []).join(" "),
      ...(crm.accounts || []).map((a) => [a.Name, a.Industry, a.Type].filter(Boolean).join(" ")),
      ...(crm.opportunities || []).map((o) => [
        o.Name,
        o.StageName,
        o.Account && o.Account.Name ? o.Account.Name : "",
      ].filter(Boolean).join(" ")),
    ];
    card.dataset.search = searchParts.join(" ");

    const stageKey = (entities.deal_stage || "").toLowerCase().trim();
    card.dataset.stage = stageKey;
    card.dataset.customer = (entities.customer_name || "").toLowerCase().trim();

    if (stageKey && !seenStages.has(stageKey)) {
      seenStages.add(stageKey);
      const opt = document.createElement("option");
      opt.value = stageKey;
      opt.textContent = entities.deal_stage;
      docStageFilterEl.appendChild(opt);
    }

    docUnitsEl.appendChild(card);
    applyDocFilter();

    const dist = crm.stage_distribution || [];
    const pieChart = new Chart(pieCanvas, {
      type: "pie",
      data: { labels: dist.map((d) => d.stage), datasets: [{ data: dist.map((d) => d.count), backgroundColor: PALETTE }] },
      options: {
        responsive: true,
        animation: { duration: 300 },
        plugins: { legend: { position: "bottom", labels: { color: "#e2e8f0", font: { size: 10 } } } },
      },
    });
    docChartInstances.push(pieChart);

    const timeline = crm.amount_timeline || [];
    const lineChart = new Chart(lineCanvas, {
      type: "line",
      data: {
        labels: timeline.map((t) => t.date),
        datasets: [{
          label: "Amount",
          data: timeline.map((t) => t.amount),
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56,189,248,0.15)",
          fill: true, tension: 0.3, pointRadius: 3,
        }],
      },
      options: {
        responsive: true,
        animation: { duration: 300 },
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#94a3b8", font: { size: 10 } }, grid: { color: "rgba(148,163,184,0.1)" } },
          y: { ticks: { color: "#94a3b8", font: { size: 10 } }, grid: { color: "rgba(148,163,184,0.1)" } },
        },
      },
    });
    docChartInstances.push(lineChart);

    if (!card.hidden) card.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  function appendDocUnitError(evt) {
    const card = document.createElement("div");
    card.className = "doc-unit-card doc-unit-error";
    card.innerHTML =
      `<div class="doc-unit-header">Unit ${evt.unit_index + 1} — Error</div>` +
      `<div class="doc-unit-text">${escapeHtml(evt.message || "Unknown error")}</div>`;
    card.dataset.search = evt.message || "";
    docUnitsEl.appendChild(card);
    applyDocFilter();
  }

  // ── CSV export ────────────────────────────────────────────────────────────

  function csvCell(val) {
    const s = val == null ? "" : String(val);
    return `"${s.replace(/"/g, '""')}"`;
  }

  function downloadDocCSV() {
    const headers = [
      "Unit Number",
      "Text Excerpt",
      "Customer Name",
      "Contact Name",
      "Deal Amount",
      "Deal Stage",
      "Keywords",
      "Matched Account Names",
      "Matched Opportunity Names",
      "Matched Opportunity Stages",
      "Matched Opportunity Amounts",
    ];

    const rows = docUnitsData.map((u, i) => {
      const ent = u.entities || {};
      const crm = u.crm || {};
      const accounts = (crm.accounts || []).map(a => a.Name || "").filter(Boolean).join("; ");
      const oppNames  = (crm.opportunities || []).map(o => o.Name || "").filter(Boolean).join("; ");
      const oppStages = (crm.opportunities || []).map(o => o.StageName || "").filter(Boolean).join("; ");
      const oppAmounts = (crm.opportunities || []).map(o => o.Amount != null ? o.Amount : "").join("; ");
      const textExcerpt = (u.text || "").slice(0, 300);
      return [
        u.unit_index != null ? u.unit_index + 1 : i + 1,
        textExcerpt,
        ent.customer_name || "",
        ent.contact_name || "",
        ent.deal_amount != null ? ent.deal_amount : "",
        ent.deal_stage || "",
        (ent.keywords || []).join(", "),
        accounts,
        oppNames,
        oppStages,
        oppAmounts,
      ].map(csvCell).join(",");
    });

    const csv = [headers.map(csvCell).join(","), ...rows].join("\r\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const filename = (docTitleEl.textContent || "document").replace(/\.[^.]+$/, "") + "_analysis.csv";
    a.href = url;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  docDownloadCsvBtn.addEventListener("click", downloadDocCSV);

  // ── WebSocket event handler ───────────────────────────────────────────────

  function handleEvent(evt) {
    if (evt.type === "document_start") {
      docTitleEl.textContent = evt.filename || "Document";
      docProgressLabelEl.textContent = `0 of ${evt.total_units} units`;
      docProgressBarEl.style.width = "0%";
      return;
    }

    if (evt.type === "document_unit") {
      docUnitsData.push({ unit_index: evt.unit_index, text: evt.text || "", entities: evt.entities || {}, crm: evt.crm || {} });
      appendDocUnit(evt);
      const pct = Math.round((evt.unit_index + 1) / evt.total_units * 100);
      docProgressBarEl.style.width = `${pct}%`;
      docProgressLabelEl.textContent = `${evt.unit_index + 1} of ${evt.total_units}`;
      return;
    }

    if (evt.type === "document_unit_error") {
      appendDocUnitError(evt);
      return;
    }

    if (evt.type === "document_done") {
      docProgressLabelEl.textContent = `Done — ${evt.processed} of ${evt.total_units} processed`;
      docProgressBarEl.style.width = "100%";
      if (docUnitsData.length > 0) { docDownloadCsvBtn.hidden = false; }
      requestDocSummary(docUnitsData.slice());
      return;
    }

    if (evt.type === "topic_shift") {
      startNewTopic(evt.label, evt.ts, evt.meeting_id || null);
      viewingId = null;
      updateHistoryModeUi(); renderViewedTopic(); renderHistoryList();
      flashTopic();
      return;
    }

    if (evt.type === "transcript") {
      const t = currentTopic();
      if (!t) return;
      if (evt.topic_label && evt.topic_label !== t.label) return;
      t.lines.push({ ts: evt.ts, text: evt.text });
      if (t.lines.length > MAX_LINES_PER_TOPIC) {
        t.lines.splice(0, t.lines.length - MAX_LINES_PER_TOPIC);
      }
      if (isViewingLive()) {
        appendTranscriptLine(evt.text, evt.ts);
        if (searchQuery) refreshSearchHitsQuiet();
      }
      if (searchQuery) renderHistoryList();
      scheduleSave();
      return;
    }

    if (evt.type === "entities") {
      const t = currentTopic();
      if (!t) return;
      if (evt.topic_label && evt.topic_label !== t.label) return;
      t.entities = evt.entities || {};
      if (isViewingLive()) renderEntities(t.entities);
      if (searchQuery) renderHistoryList();
      scheduleSave();
      return;
    }

    if (evt.type === "crm") {
      const t = currentTopic();
      if (!t) return;
      if (evt.topic_label && evt.topic_label !== t.label) return;
      t.crm = evt.data || {};
      if (isViewingLive()) renderCrm(t.crm);
      scheduleSave();
      return;
    }

    if (evt.type === "settings") {
      if (evt.sensitivity) {
        sensitivityEl.value = evt.sensitivity;
        lastConfirmedSensitivity = evt.sensitivity;
      }
      if (evt.audio_chunk_seconds != null) {
        audioChunkEl.value = evt.audio_chunk_seconds;
        lastConfirmedAudioChunk = evt.audio_chunk_seconds;
      }
      if (evt.audio_sample_rate != null) {
        audioSampleRateEl.value = String(evt.audio_sample_rate);
        lastConfirmedSampleRate = evt.audio_sample_rate;
      }
      return;
    }

    if (evt.type === "error") {
      const t = currentTopic();
      const errLine = { ts: evt.ts || Date.now() / 1000, text: `[${evt.stage} error] ${evt.message}`, error: true };
      if (t) {
        t.lines.push(errLine);
        if (t.lines.length > MAX_LINES_PER_TOPIC) {
          t.lines.splice(0, t.lines.length - MAX_LINES_PER_TOPIC);
        }
        scheduleSave();
      }
      if (isViewingLive()) {
        const placeholder = transcriptEl.querySelector(".empty-state");
        if (placeholder) placeholder.remove();
        const line = document.createElement("div");
        line.className = "line error";
        line.textContent = errLine.text;
        transcriptEl.appendChild(line);
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
      }
    }
  }

  function connect() {
    if (demoActive) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    ws.onopen = () => { currentSessionId = Date.now() / 1000; setStatus(true); hideDemoBanner(); };
    ws.onclose = () => { currentSessionId = null; setStatus(false); if (!demoActive) setTimeout(connect, 2000); showDemoBanner(); };
    ws.onerror = () => ws.close();
    ws.onmessage = (msg) => {
      try { handleEvent(JSON.parse(msg.data)); }
      catch (e) { console.error("Bad message", e, msg.data); }
    };
  }

  // ── Demo / Mock mode ─────────────────────────────────────────────────────

  const demoBtn = document.getElementById("demoBtn");
  const demoBanner = document.getElementById("demoBanner");

  function showDemoBanner() {
    if (demoBanner && !demoActive) demoBanner.hidden = false;
  }

  function hideDemoBanner() {
    if (demoBanner) demoBanner.hidden = true;
  }

  function startDemo() {
    if (demoActive) return;
    demoActive = true;
    if (demoBtn) { demoBtn.textContent = "Stop Demo"; demoBtn.classList.add("demo-active"); }
    hideDemoBanner();
    setDemoStatus();

    topics.splice(0, topics.length);
    currentId = null; viewingId = null; nextId = 1;
    currentSessionId = Date.now() / 1000;
    renderHistoryList();
    updateHistoryModeUi();
    renderViewedTopic();

    const now = Date.now() / 1000;

    function after(ms, fn) { demoTimers.push(setTimeout(fn, ms)); }

    // ── Topic 1: Q2 Sales Pipeline Review ────────────────────────────────
    after(400,  () => handleEvent({ type: "topic_shift", label: "Q2 Sales Pipeline Review", ts: now }));
    after(1200, () => handleEvent({ type: "transcript", ts: now + 1, text: "Alright team, let's kick off the Q2 pipeline review.", topic_label: "Q2 Sales Pipeline Review" }));
    after(2400, () => handleEvent({ type: "transcript", ts: now + 2, text: "TechCorp is moving into Proposal — Sarah Johnson confirmed the $450K infrastructure renewal deal.", topic_label: "Q2 Sales Pipeline Review" }));
    after(3600, () => handleEvent({ type: "transcript", ts: now + 3, text: "Close date is end of June. We need the legal addendum signed by the 20th.", topic_label: "Q2 Sales Pipeline Review" }));
    after(4200, () => handleEvent({ type: "entities", topic_label: "Q2 Sales Pipeline Review", entities: { customer_name: "TechCorp", contact_name: "Sarah Johnson", deal_amount: 450000, deal_stage: "Proposal", keywords: ["Q2", "pipeline", "renewal", "infrastructure"] } }));
    after(4800, () => handleEvent({ type: "transcript", ts: now + 5, text: "Globalink is still in Qualification — budget approval is pending until mid-May.", topic_label: "Q2 Sales Pipeline Review" }));
    after(5800, () => handleEvent({ type: "transcript", ts: now + 6, text: "We should follow up with their CFO next week to unblock the decision.", topic_label: "Q2 Sales Pipeline Review" }));
    after(6600, () => handleEvent({ type: "crm", topic_label: "Q2 Sales Pipeline Review", data: {
      accounts: [
        { Id: "001A", Name: "TechCorp", Industry: "Technology", Type: "Customer", Website: "techcorp.io" },
        { Id: "001B", Name: "Globalink", Industry: "Logistics", Type: "Prospect", Website: "globalink.com" },
      ],
      opportunities: [
        { Id: "006A", Name: "TechCorp Infrastructure Renewal", StageName: "Proposal", Amount: 450000, CloseDate: "2026-06-30", Account: { Name: "TechCorp" } },
        { Id: "006B", Name: "Globalink Platform License", StageName: "Qualification", Amount: 95000, CloseDate: "2026-07-15", Account: { Name: "Globalink" } },
        { Id: "006C", Name: "Globalink API Add-on", StageName: "Qualification", Amount: 30000, CloseDate: "2026-07-15", Account: { Name: "Globalink" } },
      ],
      stage_distribution: [
        { stage: "Proposal", count: 3 }, { stage: "Qualification", count: 5 },
        { stage: "Closed Won", count: 2 }, { stage: "Negotiation", count: 1 },
      ],
      amount_timeline: [
        { date: "Jan", amount: 120000 }, { date: "Feb", amount: 280000 },
        { date: "Mar", amount: 310000 }, { date: "Apr", amount: 450000 },
      ],
    } }));

    // ── Topic 2: Product Roadmap ──────────────────────────────────────────
    after(9000,  () => handleEvent({ type: "topic_shift", label: "Product Roadmap & Integrations", ts: now + 10 }));
    after(10000, () => handleEvent({ type: "transcript", ts: now + 11, text: "StartupXYZ wants native CRM integration by Q3 — that's their primary blocker for expanding.", topic_label: "Product Roadmap & Integrations" }));
    after(11200, () => handleEvent({ type: "transcript", ts: now + 12, text: "Mike Chen said they'd increase the contract to $120K if we ship the API by August.", topic_label: "Product Roadmap & Integrations" }));
    after(12000, () => handleEvent({ type: "entities", topic_label: "Product Roadmap & Integrations", entities: { customer_name: "StartupXYZ", contact_name: "Mike Chen", deal_amount: 120000, deal_stage: "Qualification", keywords: ["API", "integration", "Q3", "roadmap"] } }));
    after(12800, () => handleEvent({ type: "transcript", ts: now + 14, text: "Engineering estimates 6 weeks for the connector — feasible if we start the sprint Monday.", topic_label: "Product Roadmap & Integrations" }));
    after(13800, () => handleEvent({ type: "transcript", ts: now + 15, text: "Two enterprise inbounds from last week's demo are also keen — I'll follow up tomorrow.", topic_label: "Product Roadmap & Integrations" }));
    after(14600, () => handleEvent({ type: "crm", topic_label: "Product Roadmap & Integrations", data: {
      accounts: [
        { Id: "002A", Name: "StartupXYZ", Industry: "SaaS", Type: "Prospect", Website: "startupxyz.dev" },
      ],
      opportunities: [
        { Id: "007A", Name: "StartupXYZ CRM Integration", StageName: "Qualification", Amount: 120000, CloseDate: "2026-08-31", Account: { Name: "StartupXYZ" } },
      ],
      stage_distribution: [
        { stage: "Qualification", count: 2 }, { stage: "Proposal", count: 1 },
      ],
      amount_timeline: [
        { date: "Mar", amount: 50000 }, { date: "Apr", amount: 80000 }, { date: "May", amount: 120000 },
      ],
    } }));

    // ── Topic 3: MegaCorp Contract Renewal ───────────────────────────────
    after(17000, () => handleEvent({ type: "topic_shift", label: "MegaCorp Contract Renewal", ts: now + 20 }));
    after(18000, () => handleEvent({ type: "transcript", ts: now + 21, text: "MegaCorp renews in 60 days. David Lee wants to upgrade to the enterprise tier with custom SLA.", topic_label: "MegaCorp Contract Renewal" }));
    after(19200, () => handleEvent({ type: "transcript", ts: now + 22, text: "The full package — renewal plus premium support plus SLA add-on — lands at $850K ARR.", topic_label: "MegaCorp Contract Renewal" }));
    after(20000, () => handleEvent({ type: "entities", topic_label: "MegaCorp Contract Renewal", entities: { customer_name: "MegaCorp Inc", contact_name: "David Lee", deal_amount: 850000, deal_stage: "Negotiation", keywords: ["renewal", "enterprise", "SLA", "upsell"] } }));
    after(21000, () => handleEvent({ type: "transcript", ts: now + 24, text: "Legal is reviewing the SLA amendment. We're targeting signature by end of month.", topic_label: "MegaCorp Contract Renewal" }));
    after(22000, () => handleEvent({ type: "transcript", ts: now + 25, text: "Once signed, we schedule the migration call with their infra team. Great meeting, everyone.", topic_label: "MegaCorp Contract Renewal" }));
    after(22800, () => handleEvent({ type: "crm", topic_label: "MegaCorp Contract Renewal", data: {
      accounts: [
        { Id: "003A", Name: "MegaCorp Inc", Industry: "Manufacturing", Type: "Customer", Website: "megacorp.com" },
      ],
      opportunities: [
        { Id: "008A", Name: "MegaCorp Enterprise Renewal", StageName: "Negotiation", Amount: 850000, CloseDate: "2026-06-15", Account: { Name: "MegaCorp Inc" } },
        { Id: "008B", Name: "MegaCorp Premium Support", StageName: "Proposal", Amount: 120000, CloseDate: "2026-06-15", Account: { Name: "MegaCorp Inc" } },
        { Id: "008C", Name: "MegaCorp SLA Add-on", StageName: "Negotiation", Amount: 45000, CloseDate: "2026-06-15", Account: { Name: "MegaCorp Inc" } },
      ],
      stage_distribution: [
        { stage: "Negotiation", count: 4 }, { stage: "Proposal", count: 3 },
        { stage: "Closed Won", count: 7 }, { stage: "Qualification", count: 2 },
      ],
      amount_timeline: [
        { date: "Jan", amount: 200000 }, { date: "Feb", amount: 350000 },
        { date: "Mar", amount: 580000 }, { date: "Apr", amount: 850000 },
      ],
    } }));

    // ── Demo document upload after live meeting ───────────────────────────
    after(26000, () => {
      enterDocMode("demo_meeting_minutes.pdf");
      const TOTAL = 4;
      handleEvent({ type: "document_start", filename: "demo_meeting_minutes.pdf", total_units: TOTAL });
      const units = [
        { text: "Q2 pipeline review: TechCorp ($450K, Proposal) and Globalink ($95K, Qualification) discussed. Follow-up with CFO required.",
          entities: { customer_name: "TechCorp", contact_name: "Sarah Johnson", deal_amount: 450000, deal_stage: "Proposal", keywords: ["Q2", "pipeline"] },
          crm: { accounts: [{ Id: "001A", Name: "TechCorp", Industry: "Technology", Type: "Customer" }], opportunities: [{ Id: "006A", Name: "TechCorp Infrastructure Renewal", StageName: "Proposal", Amount: 450000, CloseDate: "2026-06-30", Account: { Name: "TechCorp" } }], stage_distribution: [{ stage: "Proposal", count: 1 }], amount_timeline: [{ date: "Apr", amount: 450000 }] } },
        { text: "StartupXYZ CRM integration deal: Mike Chen confirmed $120K contract contingent on API delivery by August.",
          entities: { customer_name: "StartupXYZ", contact_name: "Mike Chen", deal_amount: 120000, deal_stage: "Qualification", keywords: ["API", "integration"] },
          crm: { accounts: [{ Id: "002A", Name: "StartupXYZ", Industry: "SaaS", Type: "Prospect" }], opportunities: [{ Id: "007A", Name: "StartupXYZ CRM Integration", StageName: "Qualification", Amount: 120000, CloseDate: "2026-08-31", Account: { Name: "StartupXYZ" } }], stage_distribution: [{ stage: "Qualification", count: 1 }], amount_timeline: [{ date: "May", amount: 120000 }] } },
        { text: "MegaCorp renewal: David Lee targeting enterprise tier at $850K ARR. Legal reviewing SLA amendment, targeting EOM signature.",
          entities: { customer_name: "MegaCorp Inc", contact_name: "David Lee", deal_amount: 850000, deal_stage: "Negotiation", keywords: ["renewal", "enterprise"] },
          crm: { accounts: [{ Id: "003A", Name: "MegaCorp Inc", Industry: "Manufacturing", Type: "Customer" }], opportunities: [{ Id: "008A", Name: "MegaCorp Enterprise Renewal", StageName: "Negotiation", Amount: 850000, CloseDate: "2026-06-15", Account: { Name: "MegaCorp Inc" } }], stage_distribution: [{ stage: "Negotiation", count: 2 }], amount_timeline: [{ date: "Apr", amount: 850000 }] } },
        { text: "Action items: follow up Globalink CFO, start StartupXYZ API sprint Monday, send MegaCorp SLA draft to legal team.",
          entities: { customer_name: "", contact_name: "", deal_amount: null, deal_stage: "", keywords: ["action items", "follow-up", "sprint"] },
          crm: { accounts: [], opportunities: [], stage_distribution: [], amount_timeline: [] } },
      ];
      units.forEach((u, i) => {
        demoTimers.push(setTimeout(() => handleEvent({ type: "document_unit", unit_index: i, total_units: TOTAL, text: u.text, entities: u.entities, crm: u.crm }), (i + 1) * 1800));
      });
      demoTimers.push(setTimeout(() => handleEvent({ type: "document_done", processed: TOTAL, total_units: TOTAL }), (TOTAL + 1) * 1800));
    });
  }

  function stopDemo() {
    demoActive = false;
    demoTimers.forEach(clearTimeout);
    demoTimers = [];
    if (demoBtn) { demoBtn.textContent = "Try Demo"; demoBtn.classList.remove("demo-active"); }
    statusEl.classList.remove("demo-status");
    setStatus(false);
    connect();
  }

  if (demoBtn) {
    demoBtn.addEventListener("click", () => { if (demoActive) stopDemo(); else startDemo(); });
  }
  document.getElementById("demoOverlayBtn")?.addEventListener("click", startDemo);

  async function loadServerHistory() {
    try {
      const res = await fetch("/history");
      if (!res.ok) return;
      const meetings = await res.json();
      if (!Array.isArray(meetings) || meetings.length === 0) return;
      const knownServerIds = new Set(topics.map((t) => t.serverId).filter(Boolean));
      let added = 0;
      for (const m of meetings) {
        if (!m || !m.id) continue;
        if (knownServerIds.has(m.id)) continue;
        const stub = normalizeTopic({
          label: m.label || "Untitled topic",
          startedAt: m.started_at,
          sessionId: m.session_id,
          lines: [],
          entities: {},
          crm: {},
          serverId: m.id,
          fromServer: true,
        });
        topics.push(stub);
        added++;
      }
      if (added > 0) {
        topics.sort((a, b) => a.startedAt - b.startedAt);
        renderHistoryList();
      }
    } catch (e) {
      console.warn("Could not load server history:", e);
    }
  }

  loadPersistedTopics();
  renderHistoryList();
  updateHistoryModeUi();
  loadSettings();
  loadServerHistory();
  connect();
})();
