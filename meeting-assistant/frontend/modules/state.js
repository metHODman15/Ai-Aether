// Shared mutable application state. Modules import this singleton and
// mutate it directly; doing so avoids the noise of per-field
// getter/setter exports while still keeping the state in one place.

export const PALETTE = [
  "#38bdf8", "#a78bfa", "#f472b6", "#fb923c",
  "#facc15", "#34d399", "#f87171", "#60a5fa",
  "#c084fc", "#fbbf24",
];

export const MAX_HISTORY = 10;
export const MAX_ARCHIVE = 2000;
export const MAX_LINES_PER_TOPIC = 200;
export const STORAGE_KEY = "meetingAssistant_topics";

export const state = {
  // History / topic tracking
  topics: [],
  currentId: null,
  viewingId: null,
  nextId: 1,
  currentSessionId: null,
  _saveTimer: null,

  // Search
  searchQuery: "",
  searchHits: [],
  currentHitIndex: -1,

  // Document mode
  docUnitsData: [],
  docChartInstances: [],
  seenStages: new Set(),

  // Demo mode
  demoActive: false,
  demoTimers: [],

  // Settings (mirror of last server-confirmed values)
  lastConfirmedSensitivity: "balanced",
  lastConfirmedAudioChunk: 5,
  lastConfirmedSampleRate: 16000,

  // CRM availability (graceful degradation)
  crmOnline: true,
};

export function getTopic(id) {
  return state.topics.find((t) => t.id === id) || null;
}

export function currentTopic() {
  return getTopic(state.currentId);
}

export function viewedTopic() {
  return getTopic(state.viewingId != null ? state.viewingId : state.currentId);
}

export function isViewingLive() {
  return state.viewingId == null || state.viewingId === state.currentId;
}
