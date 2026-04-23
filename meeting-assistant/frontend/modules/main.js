// Application entry point. Wires DOM events to module functions and
// kicks off initial data loads + the WebSocket connection.
import { dom } from "./dom.js";
import { state } from "./state.js";
import { loadPersistedTopics } from "./utils.js";
import {
  renderHistoryList, updateHistoryModeUi, backToLive, clearAllHistory,
  loadServerHistory, renderViewedTopic,
} from "./history.js";
import { navigateHit } from "./transcript.js";
import { loadSettings, wireSettingsHandlers } from "./settings.js";
import {
  handleUpload, exitDocMode, applyDocFilter, downloadDocCSV,
} from "./document.js";
import { startDemo, stopDemo } from "./demo.js";
import { connect } from "./websocket.js";

// History panel
dom.backToLive.addEventListener("click", backToLive);
dom.clearHistory.addEventListener("click", clearAllHistory);
dom.searchPrev.addEventListener("click", () => navigateHit(-1));
dom.searchNext.addEventListener("click", () => navigateHit(1));
dom.historySearch.addEventListener("input", () => {
  state.searchQuery = dom.historySearch.value.trim();
  renderHistoryList(); renderViewedTopic();
});

// Settings panel
wireSettingsHandlers();

// Document mode
dom.uploadBtn.addEventListener("click", () => dom.uploadInput.click());
dom.uploadInput.addEventListener("change", handleUpload);
dom.docBackToLive.addEventListener("click", exitDocMode);
dom.docSearch.addEventListener("input", applyDocFilter);
dom.docStageFilter.addEventListener("change", applyDocFilter);
dom.docCustomerFilter.addEventListener("input", applyDocFilter);
dom.docDownloadCsv.addEventListener("click", downloadDocCSV);

// Demo mode
if (dom.demoBtn) {
  dom.demoBtn.addEventListener("click", () => {
    if (state.demoActive) stopDemo(); else startDemo();
  });
}
if (dom.demoOverlayBtn) {
  dom.demoOverlayBtn.addEventListener("click", startDemo);
}

// Boot sequence
loadPersistedTopics();
renderHistoryList();
updateHistoryModeUi();
loadSettings();
loadServerHistory();
connect();
