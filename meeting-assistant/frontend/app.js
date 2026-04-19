(() => {
  const statusEl = document.getElementById("status");
  const transcriptEl = document.getElementById("transcript");
  const entitiesEl = document.getElementById("entities");
  const accountsEl = document.getElementById("accounts");
  const opportunitiesEl = document.getElementById("opportunities");

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
      plugins: { legend: { labels: { color: "#e2e8f0" } } },
      scales: {
        x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(148,163,184,0.1)" } },
        y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(148,163,184,0.1)" } },
      },
    },
  });

  function setStatus(connected) {
    statusEl.textContent = connected ? "Connected" : "Disconnected";
    statusEl.classList.toggle("connected", connected);
    statusEl.classList.toggle("disconnected", !connected);
  }

  function appendTranscript(text, ts) {
    const time = new Date(ts * 1000).toLocaleTimeString();
    const line = document.createElement("div");
    line.className = "line";
    line.innerHTML = `<span class="ts">${time}</span><span class="text"></span>`;
    line.querySelector(".text").textContent = text;
    transcriptEl.appendChild(line);
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }

  function fmtAmount(n) {
    if (n == null) return "—";
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD", maximumFractionDigits: 0,
    }).format(n);
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

  function renderRecords(listEl, items, render) {
    listEl.innerHTML = "";
    if (!items || items.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No matches";
      listEl.appendChild(li);
      return;
    }
    for (const item of items) {
      const li = document.createElement("li");
      li.innerHTML = render(item);
      listEl.appendChild(li);
    }
  }

  function updateCrm(data) {
    renderRecords(accountsEl, data.accounts, (a) => `
      <div class="name"></div>
      <div class="meta"></div>
    `);
    accountsEl.querySelectorAll("li:not(.empty)").forEach((li, i) => {
      const a = data.accounts[i];
      li.querySelector(".name").textContent = a.Name || "(unnamed)";
      const meta = [a.Industry, a.Type, a.Website].filter(Boolean).join(" · ");
      li.querySelector(".meta").textContent = meta || a.Id;
    });

    renderRecords(opportunitiesEl, data.opportunities, () => `
      <div class="name"></div>
      <div class="meta"></div>
    `);
    opportunitiesEl.querySelectorAll("li:not(.empty)").forEach((li, i) => {
      const o = data.opportunities[i];
      li.querySelector(".name").textContent = o.Name || "(unnamed)";
      const accountName = o.Account && o.Account.Name ? o.Account.Name : "";
      const parts = [
        accountName,
        o.StageName,
        fmtAmount(o.Amount),
        o.CloseDate,
      ].filter(Boolean);
      li.querySelector(".meta").textContent = parts.join(" · ");
    });

    const dist = data.stage_distribution || [];
    stagePie.data.labels = dist.map((d) => d.stage);
    stagePie.data.datasets[0].data = dist.map((d) => d.count);
    stagePie.update();

    const timeline = data.amount_timeline || [];
    amountLine.data.labels = timeline.map((t) => t.date);
    amountLine.data.datasets[0].data = timeline.map((t) => t.amount);
    amountLine.update();
  }

  function handleEvent(evt) {
    if (evt.type === "transcript") {
      appendTranscript(evt.text, evt.ts);
    } else if (evt.type === "entities") {
      updateEntities(evt.entities);
    } else if (evt.type === "crm") {
      updateCrm(evt.data);
    } else if (evt.type === "error") {
      const line = document.createElement("div");
      line.className = "line";
      line.style.color = "#f87171";
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
