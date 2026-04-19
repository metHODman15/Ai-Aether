"""Conversation context manager backed by Anthropic Claude.

Claude is used here for one job only: deciding whether each new
transcript chunk continues the current topic or shifts to a new one.
It also returns a short, human-readable label and a running summary
that the rest of the pipeline keeps as the topic's context.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TypedDict

from anthropic import Anthropic, APIError

logger = logging.getLogger(__name__)


class ContextDecision(TypedDict):
    shift: bool
    topic_label: str
    summary: str


SYSTEM_PROMPT = """You are a meeting context tracker. You receive the
current topic's label and a rolling summary, plus the latest transcript
chunk from a live sales call. Decide whether the latest chunk continues
the same topic or shifts to a new one.

Return ONLY a single JSON object with this shape:
{
  "shift": boolean,        // true if the topic clearly changed
  "topic_label": string,   // a 2-6 word label for the (new or current) topic
  "summary": string        // <= 50 words rolling summary of the current topic
}

A "topic" is a coherent subject of discussion: a specific customer,
deal, opportunity, product line, or operational subject. Small tangents
or follow-up clarifications are NOT shifts. Treat a shift as real only
when the new chunk is clearly about a different subject (different
customer, different deal, different product, etc.).

If there is no current topic yet (empty label), set shift=true and
propose a label for the new chunk.

Return only the JSON object — no markdown, no commentary."""


class ContextManager:
    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-latest"):
        self._client = Anthropic(api_key=api_key)
        self._model = model

    async def evaluate(
        self,
        current_label: str,
        current_summary: str,
        transcript_chunk: str,
    ) -> ContextDecision:
        if not transcript_chunk.strip():
            return ContextDecision(
                shift=False, topic_label=current_label, summary=current_summary
            )
        return await asyncio.to_thread(
            self._evaluate_sync, current_label, current_summary, transcript_chunk
        )

    def _evaluate_sync(
        self,
        current_label: str,
        current_summary: str,
        transcript_chunk: str,
    ) -> ContextDecision:
        user_payload = json.dumps(
            {
                "current_topic_label": current_label or "",
                "current_topic_summary": current_summary or "",
                "latest_transcript": transcript_chunk,
            }
        )
        for attempt in range(2):
            try:
                msg = self._client.messages.create(
                    model=self._model,
                    max_tokens=300,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_payload}],
                )
                raw = "".join(
                    block.text
                    for block in msg.content
                    if getattr(block, "type", "") == "text"
                ).strip()
                return _parse(raw, current_label, current_summary)
            except APIError as exc:
                logger.warning(
                    "Claude context API error (attempt %d/2): %s", attempt + 1, exc
                )
        return ContextDecision(
            shift=False, topic_label=current_label, summary=current_summary
        )


def _parse(
    raw: str, current_label: str, current_summary: str
) -> ContextDecision:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return ContextDecision(
            shift=False, topic_label=current_label, summary=current_summary
        )
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse Claude context JSON: %s", exc)
        return ContextDecision(
            shift=False, topic_label=current_label, summary=current_summary
        )

    shift = bool(data.get("shift"))
    label = (data.get("topic_label") or "").strip() or current_label
    summary = (data.get("summary") or "").strip() or current_summary

    # If there is no current topic yet, force a shift so downstream
    # listeners initialize a fresh view.
    if not current_label:
        shift = True

    return ContextDecision(shift=shift, topic_label=label, summary=summary)
