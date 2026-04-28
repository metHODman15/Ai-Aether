// Centralised DOM lookups. Importing modules pull elements from `dom`
// rather than re-querying the document; this keeps test paths simple
// and surfaces missing IDs at module-load time rather than on first use.

const $ = (id) => document.getElementById(id);

export const dom = {
  status: $("status"),
  transcript: $("transcript"),
  entities: $("entities"),
  accounts: $("accounts"),
  opportunities: $("opportunities"),
  topic: $("topic"),
  topicLabel: null,  // filled in below

  historyList: $("historyList"),
  historyMode: $("historyMode"),
  backToLive: $("backToLive"),
  clearHistory: $("clearHistoryBtn"),

  sensitivity: $("sensitivity"),
  audioChunk: $("audioChunkSeconds"),
  audioSampleRate: $("audioSampleRate"),

  historySearch: $("historySearch"),
  searchNav: $("searchNav"),
  searchCount: $("searchCount"),
  searchPrev: $("searchPrev"),
  searchNext: $("searchNext"),

  liveView: $("liveView"),
  docView: $("docView"),
  docUnits: $("docUnits"),
  docTitle: $("docTitle"),
  docProgressLabel: $("docProgressLabel"),
  docProgressBar: $("docProgressBar"),
  docBackToLive: $("docBackToLive"),
  docSearch: $("docSearch"),
  docStageFilter: $("docStageFilter"),
  docCustomerFilter: $("docCustomerFilter"),
  docUnitCount: $("docUnitCount"),
  docSummary: $("docSummary"),
  docSummaryBody: $("docSummaryBody"),
  docDownloadCsv: $("docDownloadCsv"),

  uploadBtn: $("uploadBtn"),
  uploadInput: $("uploadInput"),

  demoBtn: $("demoBtn"),
  demoBanner: $("demoBanner"),
  demoOverlayBtn: $("demoOverlayBtn"),

  stagePieCanvas: $("stagePie"),
  amountLineCanvas: $("amountLine"),

  stagePieLoading: $("stagePieLoading"),
  amountLineLoading: $("amountLineLoading"),
  accountsLoading: $("accountsLoading"),
  opportunitiesLoading: $("opportunitiesLoading"),

  // CRM-offline banner — created lazily in main.js if missing.
  crmBanner: $("crmBanner"),

  // Transcription-error banner — created lazily by transcription_error_banner.js.
  transcriptionErrorBanner: $("transcriptionErrorBanner"),

  // Salesforce OAuth authorization panel.
  authPanel: $("authPanel"),
  authMessage: $("authMessage"),
  authConnectBtn: $("authConnectBtn"),
};

if (dom.topic) dom.topicLabel = dom.topic.querySelector(".topic-label");
