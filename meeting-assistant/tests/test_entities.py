"""Tests for backend.entities — EntityExtractor and _parse_json."""
from __future__ import annotations

import json
import types
from unittest.mock import MagicMock, patch

import pytest

from backend.entities import EntityExtractor, _parse_json, _empty, Entities
from openai import OpenAIError


def _make_response(content: str) -> MagicMock:
    """Construct a minimal openai ChatCompletion mock."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_extractor() -> tuple[EntityExtractor, MagicMock]:
    with patch("backend.entities.OpenAI") as cls:
        instance = MagicMock()
        cls.return_value = instance
        extractor = EntityExtractor(api_key="test-key")
    return extractor, instance


class TestParseJson:
    def test_valid_json_returns_correct_entities(self):
        raw = json.dumps({
            "customer_name": "Acme Corp",
            "contact_name": "Jane Doe",
            "deal_amount": 5000,
            "deal_stage": "Prospecting",
            "keywords": ["cloud", "renewal"],
        })
        result = _parse_json(raw)
        assert result["customer_name"] == "Acme Corp"
        assert result["contact_name"] == "Jane Doe"
        assert result["deal_amount"] == 5000.0
        assert result["deal_stage"] == "Prospecting"
        assert result["keywords"] == ["cloud", "renewal"]

    def test_markdown_fence_still_parsed(self):
        inner = json.dumps({
            "customer_name": "WidgetCo",
            "contact_name": None,
            "deal_amount": None,
            "deal_stage": None,
            "keywords": [],
        })
        raw = f"```json\n{inner}\n```"
        result = _parse_json(raw)
        assert result["customer_name"] == "WidgetCo"

    def test_garbage_returns_empty_entities(self):
        result = _parse_json("this is not JSON at all")
        assert result["customer_name"] is None
        assert result["keywords"] == []

    def test_deal_amount_string_with_dollar_sign(self):
        raw = json.dumps({
            "customer_name": None,
            "contact_name": None,
            "deal_amount": "$1,500",
            "deal_stage": None,
            "keywords": [],
        })
        result = _parse_json(raw)
        assert result["deal_amount"] == 1500.0

    def test_deal_amount_raw_number(self):
        raw = json.dumps({
            "customer_name": None,
            "contact_name": None,
            "deal_amount": 25000,
            "deal_stage": None,
            "keywords": [],
        })
        result = _parse_json(raw)
        assert isinstance(result["deal_amount"], float)
        assert result["deal_amount"] == 25000.0

    def test_keywords_truncated_to_five(self):
        raw = json.dumps({
            "customer_name": None,
            "contact_name": None,
            "deal_amount": None,
            "deal_stage": None,
            "keywords": ["a", "b", "c", "d", "e", "f", "g"],
        })
        result = _parse_json(raw)
        assert len(result["keywords"]) == 5


class TestEntityExtractor:
    def test_valid_response_returns_entities(self):
        extractor, mock_client = _make_extractor()
        payload = json.dumps({
            "customer_name": "Globex",
            "contact_name": "Hank Scorpio",
            "deal_amount": 999,
            "deal_stage": "Closed Won",
            "keywords": ["nuclear"],
        })
        mock_client.chat.completions.create.return_value = _make_response(payload)
        result = extractor._extract_sync("Globex renewal discussion")
        assert result["customer_name"] == "Globex"

    def test_openai_error_returns_empty_entities(self, caplog):
        extractor, mock_client = _make_extractor()
        mock_client.chat.completions.create.side_effect = OpenAIError("rate limit")
        import logging
        with caplog.at_level(logging.WARNING, logger="backend.entities"):
            result = extractor._extract_sync("some transcript")
        assert result["customer_name"] is None
        assert result["keywords"] == []
        assert any("OpenAI" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_empty_transcript_skips_api(self):
        extractor, mock_client = _make_extractor()
        result = await extractor.extract("")
        mock_client.chat.completions.create.assert_not_called()
        assert result["customer_name"] is None
        assert result["keywords"] == []

    @pytest.mark.asyncio
    async def test_extract_async_returns_entities(self):
        extractor, mock_client = _make_extractor()
        payload = json.dumps({
            "customer_name": "Initech",
            "contact_name": None,
            "deal_amount": None,
            "deal_stage": None,
            "keywords": ["tps", "reports"],
        })
        mock_client.chat.completions.create.return_value = _make_response(payload)
        result = await extractor.extract("Initech TPS report discussion")
        assert result["customer_name"] == "Initech"
        assert "tps" in result["keywords"]
