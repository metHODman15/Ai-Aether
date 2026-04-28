"""Tests for backend.context — ContextManager and _parse."""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from backend.context import (
    ContextManager,
    ContextDecision,
    SENSITIVITY_LEVELS,
    _SENSITIVITY_GUIDANCE,
    _parse,
    _system_prompt,
)
from anthropic import APIError


def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_message(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [_make_text_block(text)]
    return msg


def _make_manager() -> tuple[ContextManager, MagicMock]:
    with patch("backend.context.Anthropic") as cls:
        instance = MagicMock()
        cls.return_value = instance
        mgr = ContextManager(api_key="test-key")
    return mgr, instance


class TestParse:
    def test_valid_shift_response(self):
        raw = json.dumps({"shift": True, "topic_label": "New Deal", "summary": "abc"})
        result = _parse(raw, "Old Topic", "old summary")
        assert result["shift"] is True
        assert result["topic_label"] == "New Deal"
        assert result["summary"] == "abc"

    def test_malformed_json_returns_no_shift(self):
        result = _parse("not json", "Existing Label", "existing summary")
        assert result["shift"] is False
        assert result["topic_label"] == "Existing Label"
        assert result["summary"] == "existing summary"

    def test_empty_label_forces_shift(self):
        raw = json.dumps({"shift": False, "topic_label": "First Topic", "summary": "s"})
        result = _parse(raw, "", "")
        assert result["shift"] is True

    def test_empty_label_forces_shift_even_when_claude_says_false(self):
        raw = json.dumps({"shift": False, "topic_label": "Whatever", "summary": "s"})
        result = _parse(raw, "", "no summary yet")
        assert result["shift"] is True

    def test_missing_json_braces_returns_no_shift(self):
        result = _parse("I think the topic is New Business", "Current", "summary")
        assert result["shift"] is False
        assert result["topic_label"] == "Current"


class TestSystemPrompt:
    def test_all_sensitivity_levels_produce_different_prompts(self):
        prompts = {level: _system_prompt(level) for level in SENSITIVITY_LEVELS}
        values = list(prompts.values())
        assert values[0] != values[1]
        assert values[1] != values[2]
        assert values[0] != values[2]

    def test_unknown_sensitivity_uses_balanced_guidance(self):
        balanced_prompt = _system_prompt("balanced")
        unknown_prompt = _system_prompt("nonexistent_level")
        balanced_guidance = _SENSITIVITY_GUIDANCE["balanced"]
        assert balanced_guidance in unknown_prompt
        assert balanced_guidance in balanced_prompt

    def test_conservative_prompt_mentions_reluctant(self):
        assert "reluctant" in _system_prompt("conservative").lower()

    def test_aggressive_prompt_mentions_lean(self):
        assert "lean" in _system_prompt("aggressive").lower()


class TestContextManagerEvaluate:
    def test_valid_shift_response(self):
        mgr, mock_client = _make_manager()
        payload = json.dumps({"shift": True, "topic_label": "Renewal", "summary": "Renewal opp"})
        mock_client.messages.create.return_value = _make_message(payload)
        result = mgr._evaluate_sync("Old Topic", "old summary", "We are renewing the contract.", "balanced")
        assert result["shift"] is True
        assert result["topic_label"] == "Renewal"

    async def test_empty_transcript_skips_api(self):
        mgr, mock_client = _make_manager()
        result = await mgr.evaluate("Existing", "summary", "   ")
        mock_client.messages.create.assert_not_called()
        assert result["shift"] is False

    def test_api_error_returns_no_shift(self, caplog):
        mgr, mock_client = _make_manager()
        mock_client.messages.create.side_effect = APIError(
            message="server error", request=MagicMock(), body=None
        )
        with caplog.at_level(logging.WARNING, logger="backend.context"):
            result = mgr._evaluate_sync("Label", "summary", "some chunk", "balanced")
        assert result["shift"] is False
        assert result["topic_label"] == "Label"

    def test_sensitivity_level_in_prompt(self):
        mgr, mock_client = _make_manager()
        payload = json.dumps({"shift": False, "topic_label": "X", "summary": "Y"})
        mock_client.messages.create.return_value = _make_message(payload)
        mgr._evaluate_sync("Existing", "summary", "transcript text", "aggressive")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "aggressive" in call_kwargs["system"]


class TestContextManagerSummariseDocument:
    async def test_empty_units_returns_no_data(self):
        mgr, mock_client = _make_manager()
        result = await mgr.summarise_document([])
        mock_client.messages.create.assert_not_called()
        assert "No data" in result

    def test_non_empty_units_calls_api(self):
        mgr, mock_client = _make_manager()
        mock_client.messages.create.return_value = _make_message("• Key deal: Acme $10k")
        units = [
            {
                "entities": {
                    "customer_name": "Acme",
                    "deal_amount": 10000,
                    "deal_stage": "Prospecting",
                },
                "crm": {},
            }
        ]
        result = mgr._summarise_document_sync(units)
        mock_client.messages.create.assert_called_once()
        assert "Acme" in mock_client.messages.create.call_args[1]["messages"][0]["content"]
        assert "Key deal" in result

    def test_units_with_no_entities_returns_no_data_bullet(self):
        mgr, mock_client = _make_manager()
        units = [{"entities": {}, "crm": {}}]
        result = mgr._summarise_document_sync(units)
        mock_client.messages.create.assert_not_called()
        assert "No entities" in result
