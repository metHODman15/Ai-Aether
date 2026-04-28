import { dom } from "./dom.js";
import { state } from "./state.js";
import { handleEvent } from "./events.js";
import { enterDocMode } from "./document.js";
import {
  renderHistoryList, renderViewedTopic, updateHistoryModeUi,
} from "./history.js";
import { setDemoStatus, clearDemoStatus, connect } from "./websocket.js";

function hideDemoBanner() {
  if (dom.demoBanner) dom.demoBanner.hidden = true;
}

export function startDemo() {
  if (state.demoActive) return;
  state.demoActive = true;
  if (dom.demoBtn) {
    dom.demoBtn.textContent = "Stop Demo";
    dom.demoBtn.classList.add("demo-active");
  }
  hideDemoBanner();
  setDemoStatus();

  state.topics.splice(0, state.topics.length);
  state.currentId = null; state.viewingId = null; state.nextId = 1;
  state.currentSessionId = Date.now() / 1000;
  renderHistoryList();
  updateHistoryModeUi();
  renderViewedTopic();

  const now = Date.now() / 1000;
  const after = (ms, fn) => state.demoTimers.push(setTimeout(fn, ms));

  // Topic 1: Q2 Sales Pipeline Review
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

  // Topic 2: Product Roadmap & Integrations
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

  // Topic 3: MegaCorp Contract Renewal
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

  // Demo document upload after live meeting
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
      state.demoTimers.push(setTimeout(() => handleEvent({
        type: "document_unit", unit_index: i, total_units: TOTAL,
        text: u.text, entities: u.entities, crm: u.crm,
      }), (i + 1) * 1800));
    });
    state.demoTimers.push(setTimeout(() => handleEvent({
      type: "document_done", processed: TOTAL, total_units: TOTAL,
    }), (TOTAL + 1) * 1800));
  });
}

export function stopDemo() {
  state.demoActive = false;
  state.demoTimers.forEach(clearTimeout);
  state.demoTimers = [];
  if (dom.demoBtn) {
    dom.demoBtn.textContent = "Try Demo";
    dom.demoBtn.classList.remove("demo-active");
  }
  clearDemoStatus();
  connect();
}
