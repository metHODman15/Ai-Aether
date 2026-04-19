(() => {
  const statusEl = document.getElementById("status");
  const transcriptEl = document.getElementById("transcript");
  const entitiesEl = document.getElementById("entities");
  const accountsEl = document.getElementById("accounts");
  const opportunitiesEl = document.getElementById("opportunities");
  const topicEl = document.getElementById("topic");
  const topicLabelEl = topicEl.querySelector(".topic-label");

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

  let currentTopic = "";

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

  function setTopicLabel(label) {
    currentTopic = label || "";
    topicLabelEl.textContent = label || "Waiting for first topic…";
    topicEl.classList.toggle("active", !!label);
  }

  function clearForTopicShift(newLabel) {
    setTopicLabel(newLabel);

    transcriptEl.innerHTML = "";
    const banner = document.createElement("div");
    banner.className = "topic-shift-banner";
    banner.textContent = `New topic: ${newLabel}`;
    transcriptEl.appendChild(banner);
    transcriptEl.scrollTop = transcriptEl.scrollHeight;

    setField("customer_name", null);
    setField("contact_name", null);
    setField("deal_amount", null);
    setField("deal_stage", null);
    setField("keywords", []);

    renderRecordsEmpty(accountsEl, "No matches yet");
    renderRecordsEmpty(opportunitiesEl, "No matches yet");

    stagePie.data.labels = [];
    stagePie.data.datasets[0].data = [];
    stagePie.update();
    amountLine.data.labels = [];
    amountLine.data.datasets[0].data = [];
    amountLine.update();

    topicEl.classList.add("flash");
    setTimeout(() => topicEl.classList.remove("flash"), 800);
  }

  function appendTranscript(text, ts) {
    const placeholder = transcriptEl.querySelector(".empty-state");
    if (placeholder) placeholder.remove();
    const time = new Date(ts * 1000).toLocaleTimeString();
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span class="ts"></span><span class="text"></span>`;
    line.querySelector(".ts").textContent = time;
    line.querySelector(".text").textContent = text;
    transcriptEl.appendChild(line);
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
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

  function updateEntities(entities) {
    setField("customer_name", entities.customer_name);
    setField("contact_name", entities.contact_name);
    setField("deal_amount", entities.deal_amount);
    setField("deal_stage", entities.deal_stage);
    setField("keywords", entities.keywords || []);
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

  function updateCrm(data) {
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

  function topicMatches(eventTopic) {
    // Events tagged with a different topic than the one currently shown
    // are stale and should be ignored to keep the view pinned.
    if (!eventTopic) return true;
    if (!currentTopic) return true;
    return eventTopic === currentTopic;
  }

  function handleEvent(evt) {
    if (evt.type === "topic_shift") {
      clearForTopicShift(evt.label || "Untitled topic");
      return;
    }
    if (evt.type === "transcript") {
      if (!topicMatches(evt.topic_label)) return;
      appendTranscript(evt.text, evt.ts);
    } else if (evt.type === "entities") {
      if (!topicMatches(evt.topic_label)) return;
      updateEntities(evt.entities);
    } else if (evt.type === "crm") {
      if (!topicMatches(evt.topic_label)) return;
      updateCrm(evt.data);
    } else if (evt.type === "error") {
      const placeholder = transcriptEl.querySelector(".empty-state");
      if (placeholder) placeholder.remove();
      const line = document.createElement("div");
      line.className = "line error";
      line.textContent = `[${evt.stage} error] ${evt.message}`;
      transcriptEl.appendChild(line);
      transcriptEl.scrollTop = transcriptEl.scrollHeight;
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

  connect();
})();
