// WebSocket-event dispatcher. Splitting this out keeps both the demo
// driver and the real WebSocket client pointing at one canonical
// reducer for incoming events.
import { dom } from "./dom.js";
import { state, MAX_LINES_PER_TOPIC, currentTopic, isViewingLive } from "./state.js";
import { scheduleSave } from "./utils.js";
import {
  appendTranscriptLine, refreshSearchHitsQuiet, flashTopic,
} from "./transcript.js";
import { renderEntities } from "./entities.js";
import { renderCrm } from "./charts.js";
import {
  renderHistoryList, renderViewedTopic, updateHistoryModeUi, startNewTopic,
} from "./history.js";
import {
  showDocError, appendDocUnit, appendDocUnitError, requestDocSummary,
} from "./document.js";
import { setCrmStatus } from "./crm_banner.js";

export function handleEvent(evt) {
  if (evt.type === "document_error") {
    showDocError(evt.message || "Document processing failed");
    dom.docProgressLabel.textContent = "Failed";
    return;
  }

  if (evt.type === "document_start") {
    dom.docTitle.textContent = evt.filename || "Document";
    dom.docProgressLabel.textContent = `0 of ${evt.total_units} units`;
    dom.docProgressBar.style.width = "0%";
    return;
  }

  if (evt.type === "document_unit") {
    state.docUnitsData.push({
      unit_index: evt.unit_index, text: evt.text || "",
      entities: evt.entities || {}, crm: evt.crm || {},
    });
    appendDocUnit(evt);
    const pct = Math.round((evt.unit_index + 1) / evt.total_units * 100);
    dom.docProgressBar.style.width = `${pct}%`;
    dom.docProgressLabel.textContent = `${evt.unit_index + 1} of ${evt.total_units}`;
    return;
  }

  if (evt.type === "document_unit_error") {
    appendDocUnitError(evt);
    return;
  }

  if (evt.type === "document_done") {
    dom.docProgressLabel.textContent = `Done — ${evt.processed} of ${evt.total_units} processed`;
    dom.docProgressBar.style.width = "100%";
    if (state.docUnitsData.length > 0) dom.docDownloadCsv.hidden = false;
    requestDocSummary(state.docUnitsData.slice());
    return;
  }

  if (evt.type === "topic_shift") {
    startNewTopic(evt.label, evt.ts, evt.meeting_id || null);
    state.viewingId = null;
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
      if (state.searchQuery) refreshSearchHitsQuiet();
    }
    if (state.searchQuery) renderHistoryList();
    scheduleSave();
    return;
  }

  if (evt.type === "entities") {
    const t = currentTopic();
    if (!t) return;
    if (evt.topic_label && evt.topic_label !== t.label) return;
    t.entities = evt.entities || {};
    if (isViewingLive()) renderEntities(t.entities);
    if (state.searchQuery) renderHistoryList();
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
      dom.sensitivity.value = evt.sensitivity;
      state.lastConfirmedSensitivity = evt.sensitivity;
    }
    if (evt.audio_chunk_seconds != null) {
      dom.audioChunk.value = evt.audio_chunk_seconds;
      state.lastConfirmedAudioChunk = evt.audio_chunk_seconds;
    }
    if (evt.audio_sample_rate != null) {
      dom.audioSampleRate.value = String(evt.audio_sample_rate);
      state.lastConfirmedSampleRate = evt.audio_sample_rate;
    }
    return;
  }

  if (evt.type === "crm_offline") {
    setCrmStatus(false, evt.reason || "Salesforce is unreachable");
    return;
  }

  if (evt.type === "crm_online") {
    setCrmStatus(true);
    return;
  }

  if (evt.type === "error") {
    const t = currentTopic();
    const errLine = {
      ts: evt.ts || Date.now() / 1000,
      text: `[${evt.stage} error] ${evt.message}`,
      error: true,
    };
    if (t) {
      t.lines.push(errLine);
      if (t.lines.length > MAX_LINES_PER_TOPIC) {
        t.lines.splice(0, t.lines.length - MAX_LINES_PER_TOPIC);
      }
      scheduleSave();
    }
    if (isViewingLive()) {
      const placeholder = dom.transcript.querySelector(".empty-state");
      if (placeholder) placeholder.remove();
      const line = document.createElement("div");
      line.className = "line error";
      line.textContent = errLine.text;
      dom.transcript.appendChild(line);
      dom.transcript.scrollTop = dom.transcript.scrollHeight;
    }
  }
}
