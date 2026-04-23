// Chart.js instances for the live view + CRM record list rendering.
import { dom } from "./dom.js";
import { PALETTE } from "./state.js";
import { fmtAmount } from "./utils.js";

export const stagePie = new Chart(dom.stagePieCanvas, {
  type: "pie",
  data: { labels: [], datasets: [{ data: [], backgroundColor: PALETTE }] },
  options: {
    responsive: true,
    animation: { duration: 400 },
    plugins: { legend: { position: "bottom", labels: { color: "#e2e8f0" } } },
  },
});

export const amountLine = new Chart(dom.amountLineCanvas, {
  type: "line",
  data: {
    labels: [],
    datasets: [{
      label: "Deal amount",
      data: [],
      borderColor: "#38bdf8",
      backgroundColor: "rgba(56,189,248,0.15)",
      fill: true, tension: 0.3, pointRadius: 3,
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

function renderRecordsEmpty(listEl, message) {
  listEl.innerHTML = "";
  const li = document.createElement("li");
  li.className = "empty";
  li.textContent = message;
  listEl.appendChild(li);
}

function renderAccounts(items) {
  if (!items || items.length === 0) {
    renderRecordsEmpty(dom.accounts, "No matches");
    return;
  }
  dom.accounts.innerHTML = "";
  for (const a of items) {
    const li = document.createElement("li");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = a.Name || "(unnamed)";
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [a.Industry, a.Type, a.Website].filter(Boolean).join(" · ") || a.Id || "";
    li.appendChild(name); li.appendChild(meta);
    dom.accounts.appendChild(li);
  }
}

function renderOpportunities(items) {
  if (!items || items.length === 0) {
    renderRecordsEmpty(dom.opportunities, "No matches");
    return;
  }
  dom.opportunities.innerHTML = "";
  for (const o of items) {
    const li = document.createElement("li");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = o.Name || "(unnamed)";
    const meta = document.createElement("div");
    meta.className = "meta";
    const accountName = o.Account && o.Account.Name ? o.Account.Name : "";
    meta.textContent = [accountName, o.StageName, fmtAmount(o.Amount), o.CloseDate].filter(Boolean).join(" · ");
    li.appendChild(name); li.appendChild(meta);
    dom.opportunities.appendChild(li);
  }
}

export function renderCrm(crm) {
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
