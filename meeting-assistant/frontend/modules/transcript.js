import { dom } from "./dom.js";
import { state } from "./state.js";
import { highlightHtml } from "./utils.js";

export function setTopicLabel(label, viewing) {
  dom.topicLabel.textContent = label
    ? (viewing ? `${label} (history)` : label)
    : "Waiting for first topic…";
  dom.topic.classList.toggle("active", !!label);
}

export function flashTopic() {
  dom.topic.classList.add("flash");
  setTimeout(() => dom.topic.classList.remove("flash"), 800);
}

export function appendTranscriptLine(text, ts) {
  const placeholder = dom.transcript.querySelector(".empty-state");
  if (placeholder) placeholder.remove();
  const time = new Date(ts * 1000).toLocaleTimeString();
  const line = document.createElement("div");
  line.className = "line";
  line.innerHTML = `<span class="ts"></span><span class="text"></span>`;
  line.querySelector(".ts").textContent = time;
  const textEl = line.querySelector(".text");
  if (state.searchQuery && text.toLowerCase().includes(state.searchQuery.toLowerCase())) {
    textEl.innerHTML = highlightHtml(text, state.searchQuery);
  } else {
    textEl.textContent = text;
  }
  dom.transcript.appendChild(line);
  if (!state.searchQuery) dom.transcript.scrollTop = dom.transcript.scrollHeight;
}

export function renderTranscriptLines(lines, headerLabel, headerNote) {
  dom.transcript.innerHTML = "";
  if (headerLabel) {
    const banner = document.createElement("div");
    banner.className = "topic-shift-banner";
    banner.textContent = headerNote ? `${headerNote}: ${headerLabel}` : `Topic: ${headerLabel}`;
    dom.transcript.appendChild(banner);
  }
  if (!lines || lines.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No transcript captured for this topic.";
    dom.transcript.appendChild(empty);
  } else {
    for (const line of lines) {
      // Skip lines that were already surfaced as a banner in the live view
      // (transcription-stage errors); they carry no extra value as raw red text.
      if (line.bannerHandled) continue;
      if (line.error) {
        const el = document.createElement("div");
        el.className = "line error";
        el.textContent = line.text;
        dom.transcript.appendChild(el);
      } else {
        appendTranscriptLine(line.text, line.ts);
      }
    }
    if (state.searchQuery) { collectSearchHits(); return; }
  }
  state.searchHits = []; state.currentHitIndex = -1; dom.searchNav.hidden = true;
  dom.transcript.scrollTop = dom.transcript.scrollHeight;
}

export function collectSearchHits() {
  state.searchHits = Array.from(dom.transcript.querySelectorAll("mark.search-hit"));
  state.currentHitIndex = state.searchHits.length > 0 ? 0 : -1;
  state.searchHits.forEach((el, i) => el.classList.toggle("search-hit-current", i === 0));
  updateSearchNav();
  if (state.currentHitIndex >= 0) state.searchHits[0].scrollIntoView({ block: "nearest" });
}

export function updateSearchNav() {
  if (state.searchHits.length === 0) { dom.searchNav.hidden = true; return; }
  dom.searchNav.hidden = false;
  dom.searchCount.textContent = `${state.currentHitIndex + 1} of ${state.searchHits.length}`;
  if (state.currentHitIndex >= 0) state.searchHits[state.currentHitIndex].scrollIntoView({ block: "nearest" });
}

export function navigateHit(dir) {
  if (state.searchHits.length === 0) return;
  state.searchHits[state.currentHitIndex]?.classList.remove("search-hit-current");
  state.currentHitIndex = (state.currentHitIndex + dir + state.searchHits.length) % state.searchHits.length;
  state.searchHits[state.currentHitIndex].classList.add("search-hit-current");
  updateSearchNav();
}

export function refreshSearchHitsQuiet() {
  const prev = state.searchHits[state.currentHitIndex] || null;
  state.searchHits = Array.from(dom.transcript.querySelectorAll("mark.search-hit"));
  if (state.searchHits.length === 0) {
    state.currentHitIndex = -1; dom.searchNav.hidden = true; return;
  }
  const newIdx = prev ? state.searchHits.indexOf(prev) : -1;
  state.currentHitIndex = newIdx >= 0 ? newIdx : 0;
  state.searchHits.forEach((el, i) => el.classList.toggle("search-hit-current", i === state.currentHitIndex));
  dom.searchNav.hidden = false;
  dom.searchCount.textContent = `${state.currentHitIndex + 1} of ${state.searchHits.length}`;
}
