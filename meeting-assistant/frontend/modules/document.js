import { dom } from "./dom.js";
import { state, PALETTE } from "./state.js";
import { escapeHtml, fmtAmount, csvCell } from "./utils.js";

export function enterDocMode(filename) {
  for (const ch of state.docChartInstances) {
    try { ch.destroy(); } catch (_) {}
  }
  state.docChartInstances = [];
  dom.docUnits.innerHTML = "";
  state.docUnitsData = [];
  dom.docSummary.hidden = true;
  dom.docSummaryBody.innerHTML = "";
  dom.docDownloadCsv.hidden = true;
  dom.docTitle.textContent = filename;
  dom.docProgressLabel.textContent = "Processing…";
  dom.docProgressBar.style.width = "0%";
  dom.docSearch.value = "";
  dom.docStageFilter.innerHTML = '<option value="">All stages</option>';
  state.seenStages.clear();
  dom.docCustomerFilter.value = "";
  dom.docUnitCount.textContent = "";
  dom.liveView.hidden = true;
  dom.docView.hidden = false;
}

export function exitDocMode() {
  dom.liveView.hidden = false;
  dom.docView.hidden = true;
}

export function showDocError(msg) {
  const errEl = document.createElement("div");
  errEl.className = "doc-error";
  errEl.textContent = `Error: ${msg}`;
  dom.docUnits.prepend(errEl);
  dom.docProgressLabel.textContent = "Failed";
}

export function applyDocFilter() {
  const raw = dom.docSearch.value.trim();
  const q = raw.toLowerCase();
  const stageQ = dom.docStageFilter.value;
  const customerQ = dom.docCustomerFilter.value.trim().toLowerCase();
  const cards = dom.docUnits.querySelectorAll(".doc-unit-card");
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
        textEl.innerHTML = highlightHtmlLocal(textEl.dataset.original, raw);
      } else {
        textEl.textContent = textEl.dataset.original;
      }
    }
    const dds = card.querySelectorAll(".doc-unit-entities dd[data-original]");
    for (const dd of dds) {
      if (match && raw) {
        dd.innerHTML = highlightHtmlLocal(dd.dataset.original, raw);
      } else {
        dd.textContent = dd.dataset.original;
      }
    }
  }
  const total = cards.length;
  dom.docUnitCount.textContent = total > 0
    ? `${visible} of ${total} unit${total !== 1 ? "s" : ""}` : "";
}

function highlightHtmlLocal(text, query) {
  const safe = escapeHtml(text);
  if (!query) return safe;
  const re = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  return safe.replace(re, (m) => `<mark class="search-hit">${m}</mark>`);
}

export async function handleUpload() {
  const file = dom.uploadInput.files[0];
  if (!file) return;
  if (state.demoActive) return;
  dom.uploadInput.value = "";
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

export async function requestDocSummary(units) {
  dom.docSummaryBody.innerHTML = '<span class="doc-summary-loading">Generating summary\u2026</span>';
  dom.docSummary.hidden = false;
  dom.docSummary.scrollIntoView({ behavior: "smooth", block: "start" });
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
    dom.docSummaryBody.innerHTML = "";
    if (!text) {
      const p = document.createElement("p");
      p.className = "doc-summary-empty";
      p.textContent = "No summary available.";
      dom.docSummaryBody.appendChild(p);
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
      dom.docSummaryBody.appendChild(ul);
    }
  } catch (e) {
    dom.docSummaryBody.innerHTML =
      `<span class="doc-summary-error">Summary unavailable: ${escapeHtml(String(e))}</span>`;
  }
}

export function appendDocUnit(evt) {
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
      o.Name, o.StageName,
      o.Account && o.Account.Name ? o.Account.Name : "",
    ].filter(Boolean).join(" ")),
  ];
  card.dataset.search = searchParts.join(" ");

  const stageKey = (entities.deal_stage || "").toLowerCase().trim();
  card.dataset.stage = stageKey;
  card.dataset.customer = (entities.customer_name || "").toLowerCase().trim();

  if (stageKey && !state.seenStages.has(stageKey)) {
    state.seenStages.add(stageKey);
    const opt = document.createElement("option");
    opt.value = stageKey;
    opt.textContent = entities.deal_stage;
    dom.docStageFilter.appendChild(opt);
  }

  dom.docUnits.appendChild(card);
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
  state.docChartInstances.push(pieChart);

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
  state.docChartInstances.push(lineChart);

  if (!card.hidden) card.scrollIntoView({ behavior: "smooth", block: "end" });
}

export function appendDocUnitError(evt) {
  const card = document.createElement("div");
  card.className = "doc-unit-card doc-unit-error";
  card.innerHTML =
    `<div class="doc-unit-header">Unit ${evt.unit_index + 1} — Error</div>` +
    `<div class="doc-unit-text">${escapeHtml(evt.message || "Unknown error")}</div>`;
  card.dataset.search = evt.message || "";
  dom.docUnits.appendChild(card);
  applyDocFilter();
}

export function downloadDocCSV() {
  const headers = [
    "Unit Number", "Text Excerpt", "Customer Name", "Contact Name",
    "Deal Amount", "Deal Stage", "Keywords",
    "Matched Account Names", "Matched Opportunity Names",
    "Matched Opportunity Stages", "Matched Opportunity Amounts",
  ];
  const rows = state.docUnitsData.map((u, i) => {
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
      ent.customer_name || "", ent.contact_name || "",
      ent.deal_amount != null ? ent.deal_amount : "", ent.deal_stage || "",
      (ent.keywords || []).join(", "),
      accounts, oppNames, oppStages, oppAmounts,
    ].map(csvCell).join(",");
  });
  const csv = [headers.map(csvCell).join(","), ...rows].join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const filename = (dom.docTitle.textContent || "document").replace(/\.[^.]+$/, "") + "_analysis.csv";
  a.href = url; a.download = filename; a.style.display = "none";
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
