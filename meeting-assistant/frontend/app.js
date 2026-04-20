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
  const sensitivityEl = document.getElementById("sensitivity");
  const historySearchEl = document.getElementById("historySearch");
  let searchQuery = "";

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
  const MAX_LINES_PER_TOPIC = 200;
  const topics = []; // [{ id, label, startedAt, lines: [{ts,text}], entities, crm }]
  let currentId = null;
  let viewingId = null; // null => viewing live (current)
  let nextId = 1;

  function getTopic(id) {
    return topics.find((t) => t.id === id) || null;
  }
  function currentTopic() { return getTopic(currentId); }
  function viewedTopic() {
    return getTopic(viewingId != null ? viewingId : currentId);
  }
  function isViewingLive() {
    return viewingId == null || viewingId === currentId;
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
    if (!items || items.length === 0) {
      renderRecordsEmpty(accountsEl, "No matches");
      return;
    }
    accountsEl.innerHTML = "";
    for (const a of items) {
      const li = document.createElement("li");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = a.Name || "(unnamed)";
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent =
        [a.Industry, a.Type, a.Website].filter(Boolean).join(" · ") || a.Id || "";
      li.appendChild(name);
      li.appendChild(meta);
      accountsEl.appendChild(li);
    }
  }

  function renderOpportunities(items) {
    if (!items || items.length === 0) {
      renderRecordsEmpty(opportunitiesEl, "No matches");
      return;
    }
    opportunitiesEl.innerHTML = "";
    for (const o of items) {
      const li = document.createElement("li");
      const name = document.createElement("div");
      name.className = "name";
      name.textContent = o.Name || "(unnamed)";
      const meta = document.createElement("div");
      meta.className = "meta";
      const accountName = o.Account && o.Account.Name ? o.Account.Name : "";
      meta.textContent = [accountName, o.StageName, fmtAmount(o.Amount), o.CloseDate]
        .filter(Boolean)
        .join(" · ");
      li.appendChild(name);
      li.appendChild(meta);
      opportunitiesEl.appendChild(li);
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
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeRegExp(s) {
    return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function highlightHtml(text, query) {
    const safe = escapeHtml(text);
    if (!query) return safe;
    const re = new RegExp(escapeRegExp(query), "gi");
    return safe.replace(re, (m) => `<mark class="search-hit">${m}</mark>`);
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
      banner.textContent = headerNote
        ? `${headerNote}: ${headerLabel}`
        : `Topic: ${headerLabel}`;
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
      if (searchQuery) {
        const needle = searchQuery.toLowerCase();
        const firstHit = Array.from(
          transcriptEl.querySelectorAll(".line:not(.error) .text")
        ).find((el) => el.textContent.toLowerCase().includes(needle));
        if (firstHit) {
          firstHit.scrollIntoView({ block: "center" });
          return;
        }
      }
    }
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
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }

  function renderHistoryList() {
    historyListEl.innerHTML = "";
    if (topics.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No topics yet";
      historyListEl.appendChild(li);
      return;
    }
    // newest first
    const ordered = topics
      .slice()
      .reverse()
      .filter((t) => topicMatchesQuery(t, searchQuery));
    if (ordered.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No topics match your search";
      historyListEl.appendChild(li);
      return;
    }
    for (const t of ordered) {
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
      time.textContent = new Date(t.startedAt * 1000).toLocaleTimeString();
      li.appendChild(label);
      li.appendChild(time);
      li.addEventListener("click", () => viewTopic(t.id));
      historyListEl.appendChild(li);
    }
  }

  function renderViewedTopic() {
    const t = viewedTopic();
    if (!t) {
      setTopicLabel("", false);
      renderTranscriptLines([], null, null);
      renderEntities({});
      renderCrm({});
      return;
    }
    const viewingPast = !isViewingLive();
    setTopicLabel(t.label, viewingPast);
    renderTranscriptLines(
      t.lines,
      t.label,
      viewingPast ? "Past topic" : "New topic"
    );
    renderEntities(t.entities);
    renderCrm(t.crm);
  }

  function updateHistoryModeUi() {
    const viewingPast = !isViewingLive();
    historyModeEl.hidden = !viewingPast;
    document.body.classList.toggle("history-mode", viewingPast);
  }

  function viewTopic(id) {
    if (id === currentId) {
      viewingId = null;
    } else {
      viewingId = id;
    }
    updateHistoryModeUi();
    renderViewedTopic();
    renderHistoryList();
  }

  function backToLive() {
    viewingId = null;
    updateHistoryModeUi();
    renderViewedTopic();
    renderHistoryList();
  }

  backToLiveBtn.addEventListener("click", backToLive);

  historySearchEl.addEventListener("input", () => {
    searchQuery = historySearchEl.value.trim();
    renderHistoryList();
    renderViewedTopic();
  });

  async function loadSensitivity() {
    try {
      const res = await fetch("/settings");
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.sensitivity) {
        sensitivityEl.value = data.sensitivity;
        lastConfirmedSensitivity = data.sensitivity;
      }
    } catch (e) {
      console.error("Failed to load settings", e);
    }
  }

  let lastConfirmedSensitivity = sensitivityEl.value;

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

  function startNewTopic(label, ts) {
    const topic = {
      id: nextId++,
      label: label || "Untitled topic",
      startedAt: ts || Date.now() / 1000,
      lines: [],
      entities: {},
      crm: {},
    };
    topics.push(topic);
    while (topics.length > MAX_HISTORY) topics.shift();
    currentId = topic.id;
    return topic;
  }

  function handleEvent(evt) {
    if (evt.type === "topic_shift") {
      startNewTopic(evt.label, evt.ts);
      // A new live topic always pulls the user back to live view.
      viewingId = null;
      updateHistoryModeUi();
      renderViewedTopic();
      renderHistoryList();
      flashTopic();
      return;
    }

    if (evt.type === "transcript") {
      const t = currentTopic();
      if (!t) return;
      // Drop events whose tag doesn't match the current live topic.
      if (evt.topic_label && evt.topic_label !== t.label) return;
      t.lines.push({ ts: evt.ts, text: evt.text });
      if (t.lines.length > MAX_LINES_PER_TOPIC) {
        t.lines.splice(0, t.lines.length - MAX_LINES_PER_TOPIC);
      }
      if (isViewingLive()) appendTranscriptLine(evt.text, evt.ts);
      if (searchQuery) renderHistoryList();
      return;
    }

    if (evt.type === "entities") {
      const t = currentTopic();
      if (!t) return;
      if (evt.topic_label && evt.topic_label !== t.label) return;
      t.entities = evt.entities || {};
      if (isViewingLive()) renderEntities(t.entities);
      if (searchQuery) renderHistoryList();
      return;
    }

    if (evt.type === "crm") {
      const t = currentTopic();
      if (!t) return;
      if (evt.topic_label && evt.topic_label !== t.label) return;
      t.crm = evt.data || {};
      if (isViewingLive()) renderCrm(t.crm);
      return;
    }

    if (evt.type === "settings") {
      if (evt.sensitivity) {
        sensitivityEl.value = evt.sensitivity;
        lastConfirmedSensitivity = evt.sensitivity;
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
      }
      // Only render to the transcript panel when the user is on live;
      // history mode stays read-only for the topic being inspected.
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
    ws.onopen = () => setStatus(true);
    ws.onclose = () => {
      setStatus(false);
      setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (msg) => {
      try { handleEvent(JSON.parse(msg.data)); }
      catch (e) { console.error("Bad message", e, msg.data); }
    };
  }

  // Initial render
  renderHistoryList();
  updateHistoryModeUi();
  loadSensitivity();

  connect();
})();
