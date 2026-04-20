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
    statusEl.textContent = connected ? "Connected" : "Disconnected";
    statusEl.classList.toggle("connected", connected);
    statusEl.classList.toggle("disconnected", !connected);
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
      const label = document.createElement("div");
      label.className = "h-label";
      const labelText = t.label || "Untitled topic";
      if (searchQuery && labelText.toLowerCase().includes(searchQuery.toLowerCase())) {
        label.innerHTML = highlightHtml(labelText, searchQuery);
      } else {
        label.textContent = labelText;
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
      li.appendChild(label); li.appendChild(time);
      li.addEventListener("click", () => viewTopic(t.id));
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
    topics.splice(idx, 1);
    if (viewingId === id) {
      viewingId = null;
      updateHistoryModeUi();
    }
    persistTopics();
    renderHistoryList();
    renderViewedTopic();
  }

  function clearAllHistory() {
    const pastCount = topics.filter((t) => t.id !== currentId).length;
    if (pastCount === 0) return;
    if (!confirm(`Clear ${pastCount} past topic(s) from history? This cannot be undone.`)) return;
    const live = currentId != null ? getTopic(currentId) : null;
    topics.length = 0;
    if (live) topics.push(live);
    viewingId = null;
    persistTopics();
    updateHistoryModeUi();
    renderHistoryList();
    renderViewedTopic();
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

  sensitivityEl.addEventListener("change", async () => {
    const value = sensitivityEl.value;
    sensitivityEl.disabled = true;
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
    } catch (e) {
      console.error("Failed to update sensitivity", e);
      sensitivityEl.value = lastConfirmedSensitivity;
    } finally {
      sensitivityEl.disabled = false;
    }
  });

  audioChunkEl.addEventListener("change", async () => {
    const value = Number(audioChunkEl.value);
    audioChunkEl.disabled = true;
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
    } catch (e) {
      console.error("Failed to update audio chunk seconds", e);
      audioChunkEl.value = lastConfirmedAudioChunk;
    } finally {
      audioChunkEl.disabled = false;
    }
  });

  audioSampleRateEl.addEventListener("change", async () => {
    const value = Number(audioSampleRateEl.value);
    audioSampleRateEl.disabled = true;
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
    } catch (e) {
      console.error("Failed to update audio sample rate", e);
      audioSampleRateEl.value = String(lastConfirmedSampleRate);
    } finally {
      audioSampleRateEl.disabled = false;
    }
  });

  function startNewTopic(label, ts) {
    if (!currentSessionId) currentSessionId = Date.now() / 1000;
    const topic = {
      id: nextId++,
      label: label || "Untitled topic",
      startedAt: ts || Date.now() / 1000,
      sessionId: currentSessionId,
      lines: [], entities: {}, crm: {},
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
    docUnitCountEl.textContent = "";
    liveViewEl.hidden = true;
    docViewEl.hidden = false;
  }

  function applyDocFilter() {
    const q = docSearchEl.value.trim().toLowerCase();
    const cards = docUnitsEl.querySelectorAll(".doc-unit-card");
    let visible = 0;
    for (const card of cards) {
      const haystack = (card.dataset.search || "").toLowerCase();
      const match = q === "" || haystack.includes(q);
      card.hidden = !match;
      if (match) visible++;
    }
    const total = cards.length;
    docUnitCountEl.textContent = total > 0 ? `${visible} of ${total} unit${total !== 1 ? "s" : ""}` : "";
  }

  docSearchEl.addEventListener("input", applyDocFilter);

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
    textWrap.textContent = evt.text;
    card.appendChild(textWrap);

    const entities = evt.entities || {};
    const entDiv = document.createElement("dl");
    entDiv.className = "doc-unit-entities";
    entDiv.innerHTML = `
      <dt>Customer</dt><dd>${escapeHtml(entities.customer_name || "—")}</dd>
      <dt>Contact</dt><dd>${escapeHtml(entities.contact_name || "—")}</dd>
      <dt>Amount</dt><dd>${entities.deal_amount != null ? fmtAmount(entities.deal_amount) : "—"}</dd>
      <dt>Stage</dt><dd>${escapeHtml(entities.deal_stage || "—")}</dd>
      <dt>Keywords</dt><dd>${escapeHtml((entities.keywords || []).join(", ") || "—")}</dd>
    `;
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
      startNewTopic(evt.label, evt.ts);
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
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    ws.onopen = () => { currentSessionId = Date.now() / 1000; setStatus(true); };
    ws.onclose = () => { currentSessionId = null; setStatus(false); setTimeout(connect, 2000); };
    ws.onerror = () => ws.close();
    ws.onmessage = (msg) => {
      try { handleEvent(JSON.parse(msg.data)); }
      catch (e) { console.error("Bad message", e, msg.data); }
    };
  }

  loadPersistedTopics();
  renderHistoryList();
  updateHistoryModeUi();
  loadSettings();
  connect();
})();
