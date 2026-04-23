"""Extract CRM-relevant entities from transcript text using OpenAI.

Claude is reserved for context management (see backend/context.py),
so entity extraction runs against an OpenAI chat model in JSON mode.

The list of valid Opportunity stages is injected dynamically — the
:class:`SalesforceClient` discovers the picklist values for the
connected org at startup and exposes them via a callable provider so
orgs with custom stages get accurate guidance in the prompt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Callable, Sequence, TypedDict

from openai import OpenAI, OpenAIError

logger = logging.getLogger(__name__)


class Entities(TypedDict, total=False):
    customer_name: str | None
    contact_name: str | None
    deal_amount: float | None
    deal_stage: str | None
    keywords: list[str]


_DEFAULT_STAGES: tuple[str, ...] = (
    "Prospecting", "Qualification", "Needs Analysis", "Value Proposition",
    "Id. Decision Makers", "Perception Analysis", "Proposal/Price Quote",
    "Negotiation/Review", "Closed Won", "Closed Lost",
)


def _build_system_prompt(stages: Sequence[str]) -> str:
    stage_list = ", ".join(stages) if stages else ", ".join(_DEFAULT_STAGES)
    return f"""You are a CRM assistant that extracts business entities from
sales meeting transcripts. Identify information that maps to Salesforce records.

Return ONLY a single JSON object with these fields (use null for unknown):
{{
  "customer_name": string | null,   // Account / company name being discussed
  "contact_name": string | null,    // Person at the customer org
  "deal_amount": number | null,     // Numeric deal value in USD (no symbols)
  "deal_stage": string | null,      // One of: {stage_list}
  "keywords": string[]              // Up to 5 short search keywords
}}

If no relevant CRM info is present, return all-null fields and an empty keywords list."""


def _empty() -> Entities:
    return Entities(
        customer_name=None,
        contact_name=None,
        deal_amount=None,
        deal_stage=None,
        keywords=[],
    )


class EntityExtractor:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        stage_provider: Callable[[], Sequence[str]] | None = None,
    ):
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._stage_provider = stage_provider or (lambda: _DEFAULT_STAGES)

    async def extract(self, transcript: str) -> Entities:
        if not transcript.strip():
            return _empty()
        return await asyncio.to_thread(self._extract_sync, transcript)

    def _extract_sync(self, transcript: str) -> Entities:
        try:
            stages = self._stage_provider() or _DEFAULT_STAGES
        except Exception:
            stages = _DEFAULT_STAGES
        system_prompt = _build_system_prompt(stages)
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": transcript},
                ],
                response_format={"type": "json_object"},
                max_tokens=400,
                temperature=0.0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            return _parse_json(raw)
        except OpenAIError as exc:
            logger.warning("OpenAI entity extraction error: %s", exc)
            return _empty()


def _parse_json(raw: str) -> Entities:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return _empty()
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse entity JSON output: %s", exc)
        return _empty()

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
