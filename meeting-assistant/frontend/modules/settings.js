import { dom } from "./dom.js";
import { state } from "./state.js";

export async function loadSettings() {
  try {
    const res = await fetch("/settings");
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.sensitivity) {
      dom.sensitivity.value = data.sensitivity;
      state.lastConfirmedSensitivity = data.sensitivity;
    }
    if (data && data.audio_chunk_seconds != null) {
      dom.audioChunk.value = data.audio_chunk_seconds;
      if (data.audio_chunk_seconds_min != null) dom.audioChunk.min = data.audio_chunk_seconds_min;
      if (data.audio_chunk_seconds_max != null) dom.audioChunk.max = data.audio_chunk_seconds_max;
      state.lastConfirmedAudioChunk = data.audio_chunk_seconds;
    }
    if (data && Array.isArray(data.audio_sample_rate_options) && data.audio_sample_rate_options.length) {
      const currentVal = data.audio_sample_rate != null ? data.audio_sample_rate : Number(dom.audioSampleRate.value);
      dom.audioSampleRate.innerHTML = "";
      for (const rate of data.audio_sample_rate_options) {
        const opt = document.createElement("option");
        opt.value = String(rate);
        opt.textContent = rate.toLocaleString() + " Hz";
        if (rate === currentVal) opt.selected = true;
        dom.audioSampleRate.appendChild(opt);
      }
      if (data.audio_sample_rate != null) {
        dom.audioSampleRate.value = String(data.audio_sample_rate);
        state.lastConfirmedSampleRate = data.audio_sample_rate;
      }
    } else if (data && data.audio_sample_rate != null) {
      dom.audioSampleRate.value = String(data.audio_sample_rate);
      state.lastConfirmedSampleRate = data.audio_sample_rate;
    }
  } catch (e) { console.error("Failed to load settings", e); }
}

function showSettingFeedback(el, success) {
  const existing = el.parentElement.querySelector(".setting-feedback");
  if (existing) existing.remove();
  const badge = document.createElement("span");
  badge.className = "setting-feedback " + (success ? "setting-feedback--ok" : "setting-feedback--err");
  badge.textContent = success ? "Saved \u2713" : "Failed";
  el.insertAdjacentElement("afterend", badge);
  setTimeout(() => badge.classList.add("setting-feedback--hide"), 1600);
  setTimeout(() => badge.remove(), 2100);
}

export function wireSettingsHandlers() {
  dom.sensitivity.addEventListener("change", async () => {
    const value = dom.sensitivity.value;
    dom.sensitivity.disabled = true;
    let ok = false;
    try {
      const res = await fetch("/settings/sensitivity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sensitivity: value }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.lastConfirmedSensitivity = data.sensitivity || value;
      dom.sensitivity.value = state.lastConfirmedSensitivity;
      ok = true;
    } catch (e) {
      console.error("Failed to update sensitivity", e);
      dom.sensitivity.value = state.lastConfirmedSensitivity;
    } finally {
      dom.sensitivity.disabled = false;
      showSettingFeedback(dom.sensitivity, ok);
    }
  });

  dom.audioChunk.addEventListener("change", async () => {
    const value = Number(dom.audioChunk.value);
    dom.audioChunk.disabled = true;
    let ok = false;
    try {
      const res = await fetch("/settings/audio_chunk_seconds", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_chunk_seconds: value }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.lastConfirmedAudioChunk = data.audio_chunk_seconds != null ? data.audio_chunk_seconds : value;
      dom.audioChunk.value = state.lastConfirmedAudioChunk;
      ok = true;
    } catch (e) {
      console.error("Failed to update audio chunk seconds", e);
      dom.audioChunk.value = state.lastConfirmedAudioChunk;
    } finally {
      dom.audioChunk.disabled = false;
      showSettingFeedback(dom.audioChunk, ok);
    }
  });

  dom.audioSampleRate.addEventListener("change", async () => {
    const value = Number(dom.audioSampleRate.value);
    dom.audioSampleRate.disabled = true;
    let ok = false;
    try {
      const res = await fetch("/settings/audio_sample_rate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_sample_rate: value }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.lastConfirmedSampleRate = data.audio_sample_rate != null ? data.audio_sample_rate : value;
      dom.audioSampleRate.value = String(state.lastConfirmedSampleRate);
      ok = true;
    } catch (e) {
      console.error("Failed to update audio sample rate", e);
      dom.audioSampleRate.value = String(state.lastConfirmedSampleRate);
    } finally {
      dom.audioSampleRate.disabled = false;
      showSettingFeedback(dom.audioSampleRate, ok);
    }
  });
}
