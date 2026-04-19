"""Extract CRM-relevant entities from transcript text using Claude."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TypedDict

from anthropic import Anthropic, APIError

logger = logging.getLogger(__name__)


class Entities(TypedDict, total=False):
    customer_name: str | None
    contact_name: str | None
    deal_amount: float | None
    deal_stage: str | None
    keywords: list[str]


SYSTEM_PROMPT = """You are a CRM assistant that extracts business entities from
sales meeting transcripts. Identify information that maps to Salesforce records.

Return ONLY a single JSON object with these fields (use null for unknown):
{
  "customer_name": string | null,   // Account / company name being discussed
  "contact_name": string | null,    // Person at the customer org
  "deal_amount": number | null,     // Numeric deal value in USD (no symbols)
  "deal_stage": string | null,      // One of: Prospecting, Qualification,
                                    //   Needs Analysis, Value Proposition,
                                    //   Id. Decision Makers, Perception Analysis,
                                    //   Proposal/Price Quote, Negotiation/Review,
                                    //   Closed Won, Closed Lost
  "keywords": string[]              // Up to 5 short search keywords
}

If no relevant CRM info is present, return all-null fields and an empty keywords list.
Never include explanations, markdown, or extra text — only the JSON object."""


class EntityExtractor:
    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-latest"):
        self._client = Anthropic(api_key=api_key)
        self._model = model

    async def extract(self, transcript: str) -> Entities:
        if not transcript.strip():
            return Entities(
                customer_name=None,
                contact_name=None,
                deal_amount=None,
                deal_stage=None,
                keywords=[],
            )
        return await asyncio.to_thread(self._extract_sync, transcript)

    def _extract_sync(self, transcript: str) -> Entities:
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=400,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": transcript}],
            )
            raw = "".join(
                block.text for block in msg.content if getattr(block, "type", "") == "text"
            ).strip()
            return _parse_json(raw)
        except APIError as exc:
            logger.warning("Claude API error: %s", exc)
            return Entities(
                customer_name=None,
                contact_name=None,
                deal_amount=None,
                deal_stage=None,
                keywords=[],
            )


def _parse_json(raw: str) -> Entities:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return Entities(
            customer_name=None, contact_name=None, deal_amount=None,
            deal_stage=None, keywords=[],
        )
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse Claude JSON output: %s", exc)
        return Entities(
            customer_name=None, contact_name=None, deal_amount=None,
            deal_stage=None, keywords=[],
        )

    amount = data.get("deal_amount")
    if isinstance(amount, str):
        cleaned = re.sub(r"[^0-9.]", "", amount)
        amount = float(cleaned) if cleaned else None

    return Entities(
        customer_name=_clean_str(data.get("customer_name")),
        contact_name=_clean_str(data.get("contact_name")),
        deal_amount=float(amount) if isinstance(amount, (int, float)) else None,
        deal_stage=_clean_str(data.get("deal_stage")),
        keywords=[k for k in (data.get("keywords") or []) if isinstance(k, str)][:5],
    )


def _clean_str(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None
