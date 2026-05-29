"""Tests for quiet filing triage — prompt construction, response parsing,
validation, boost computation, and Claude API integration."""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.ai_edges.quiet_filing_triage.triage import (
    TriageAssessment,
    build_prompt,
    call_triage,
    clear_prompt_cache,
    compute_boost,
    get_prompt_version,
    log_cost,
    parse_response,
    truncate_text,
    validate_response,
    _load_prompt_template,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


VALID_CLAUDE_JSON = json.dumps({
    "materiality_score": 8,
    "attention_likelihood": "low",
    "direction": "bearish",
    "reasoning": "CEO resignation disclosed in Friday after-hours 8-K.",
    "key_material_items": ["CEO resignation effective March 1"],
    "suggested_urgency": "act",
})


def _mock_response(text: str = VALID_CLAUDE_JSON, input_tokens: int = 5000, output_tokens: int = 200):
    """Build a mock Anthropic API response."""
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the prompt cache before each test."""
    clear_prompt_cache()
    yield
    clear_prompt_cache()


SAMPLE_CONTEXT = {
    "is_after_hours": True,
    "is_friday": True,
    "is_holiday_adjacent": False,
    "is_amended": False,
    "has_press_release": False,
    "filing_time_context": "Filing was filed after market hours, on a Friday.",
}


# ---------------------------------------------------------------------------
# truncate_text
# ---------------------------------------------------------------------------

class TestTruncateText:
    def test_short_text_unchanged(self):
        text = "Hello world"
        assert truncate_text(text, 100) == text

    def test_exact_limit_unchanged(self):
        text = "x" * 100
        assert truncate_text(text, 100) == text

    def test_long_text_truncated(self):
        text = "A" * 100 + "B" * 100
        result = truncate_text(text, 100)
        assert len(result) <= 100 + len("\n\n[... middle section truncated for length ...]\n\n")
        assert result.startswith("A")
        assert result.endswith("B")
        assert "truncated" in result

    def test_preserves_start_and_end(self):
        text = "START" + "x" * 50000 + "END__"
        result = truncate_text(text, 1000)
        assert result.startswith("START")
        assert result.endswith("END__")


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_clean_json(self):
        data = parse_response(VALID_CLAUDE_JSON)
        assert data["materiality_score"] == 8
        assert data["direction"] == "bearish"

    def test_json_with_markdown_fences(self):
        wrapped = f"```json\n{VALID_CLAUDE_JSON}\n```"
        data = parse_response(wrapped)
        assert data["materiality_score"] == 8

    def test_json_with_bare_fences(self):
        wrapped = f"```\n{VALID_CLAUDE_JSON}\n```"
        data = parse_response(wrapped)
        assert data["materiality_score"] == 8

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_response("not json")


# ---------------------------------------------------------------------------
# validate_response
# ---------------------------------------------------------------------------

class TestValidateResponse:
    def test_valid_data_passes(self):
        data = json.loads(VALID_CLAUDE_JSON)
        v = validate_response(data)
        assert v["materiality_score"] == 8
        assert v["attention_likelihood"] == "low"
        assert v["direction"] == "bearish"

    def test_materiality_clamped_high(self):
        v = validate_response({"materiality_score": 15})
        assert v["materiality_score"] == 10

    def test_materiality_clamped_low(self):
        v = validate_response({"materiality_score": -5})
        assert v["materiality_score"] == 1

    def test_materiality_string_coerced(self):
        v = validate_response({"materiality_score": "7"})
        assert v["materiality_score"] == 7

    def test_materiality_non_numeric_defaults(self):
        v = validate_response({"materiality_score": "abc"})
        assert v["materiality_score"] == 1

    def test_invalid_attention_defaults_medium(self):
        v = validate_response({"attention_likelihood": "unknown"})
        assert v["attention_likelihood"] == "medium"

    def test_invalid_direction_defaults_neutral(self):
        v = validate_response({"direction": "sideways"})
        assert v["direction"] == "neutral"

    def test_invalid_urgency_defaults_monitor(self):
        v = validate_response({"suggested_urgency": "panic"})
        assert v["suggested_urgency"] == "monitor"

    def test_key_items_truncated_to_five(self):
        items = [f"item-{i}" for i in range(10)]
        v = validate_response({"key_material_items": items})
        assert len(v["key_material_items"]) == 5

    def test_key_items_non_list_defaults_empty(self):
        v = validate_response({"key_material_items": "not a list"})
        assert v["key_material_items"] == []

    def test_missing_fields_use_defaults(self):
        v = validate_response({})
        assert v["materiality_score"] == 1
        assert v["attention_likelihood"] == "medium"
        assert v["direction"] == "neutral"
        assert v["reasoning"] == ""
        assert v["key_material_items"] == []
        assert v["suggested_urgency"] == "monitor"


# ---------------------------------------------------------------------------
# compute_boost
# ---------------------------------------------------------------------------

class TestComputeBoost:
    def test_quiet_filing_high_materiality_low_attention(self):
        is_quiet, mult = compute_boost(8, "low", 1.5)
        assert is_quiet is True
        assert mult == 1.5

    def test_exactly_threshold_materiality(self):
        is_quiet, mult = compute_boost(7, "low", 1.5)
        assert is_quiet is True

    def test_below_threshold_materiality(self):
        is_quiet, mult = compute_boost(6, "low", 1.5)
        assert is_quiet is False
        assert mult == 1.0

    def test_medium_attention_no_boost(self):
        is_quiet, mult = compute_boost(9, "medium", 1.5)
        assert is_quiet is False
        assert mult == 1.0

    def test_high_attention_no_boost(self):
        is_quiet, mult = compute_boost(10, "high", 1.5)
        assert is_quiet is False
        assert mult == 1.0

    def test_custom_boost_multiplier(self):
        is_quiet, mult = compute_boost(9, "low", 2.0)
        assert is_quiet is True
        assert mult == 2.0


# ---------------------------------------------------------------------------
# log_cost
# ---------------------------------------------------------------------------

class TestLogCost:
    def test_cost_calculation(self):
        # 5000 input tokens * $3/M = $0.015
        # 200 output tokens * $15/M = $0.003
        cost = log_cost("AAPL", "10-K", 5000, 200, "claude-sonnet-4-20250514")
        assert abs(cost - 0.018) < 0.0001

    def test_zero_tokens(self):
        cost = log_cost("AAPL", "10-K", 0, 0, "test-model")
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Prompt loading and versioning
# ---------------------------------------------------------------------------

class TestPromptLoading:
    def test_prompt_version_is_stable(self):
        v1 = get_prompt_version()
        v2 = get_prompt_version()
        assert v1 == v2
        assert len(v1) == 12  # SHA-256 first 12 chars

    def test_prompt_template_has_required_keys(self):
        template = _load_prompt_template()
        assert "system" in template
        assert "user_template" in template


class TestBuildPrompt:
    def test_returns_system_and_user(self):
        system, user = build_prompt(
            "Some filing text", "AAPL", "10-K", "2026-01-15", SAMPLE_CONTEXT,
        )
        assert len(system) > 0
        assert "AAPL" in user
        assert "10-K" in user
        assert "2026-01-15" in user
        assert "Some filing text" in user

    def test_includes_timing_context(self):
        _, user = build_prompt(
            "text", "AAPL", "10-K", "2026-01-15", SAMPLE_CONTEXT,
        )
        assert "after market hours" in user
        assert "True" in user  # is_after_hours=True

    def test_includes_tier1_keywords(self):
        keywords = [{"term": "merger"}, {"term": "acquisition"}]
        _, user = build_prompt(
            "text", "AAPL", "8-K", "2026-01-15", SAMPLE_CONTEXT, keywords,
        )
        assert "merger" in user
        assert "acquisition" in user

    def test_no_tier1_keywords(self):
        _, user = build_prompt(
            "text", "AAPL", "8-K", "2026-01-15", SAMPLE_CONTEXT, None,
        )
        assert "None" in user

    def test_long_text_truncated(self):
        long_text = "A" * 100_000
        _, user = build_prompt(
            long_text, "AAPL", "10-K", "2026-01-15", SAMPLE_CONTEXT,
        )
        assert "truncated" in user


# ---------------------------------------------------------------------------
# call_triage (async, mock Anthropic)
# ---------------------------------------------------------------------------

class TestCallTriage:
    def test_successful_triage(self):
        mock_resp = _mock_response()

        with patch("engine.ai_edges.quiet_filing_triage.triage.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                result = _run(call_triage(
                    filing_text="Some filing text about CEO resignation.",
                    entity_id="AAPL",
                    form_type="8-K",
                    filing_date="2026-02-25",
                    context=SAMPLE_CONTEXT,
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert isinstance(result, TriageAssessment)
        assert result.materiality_score == 8
        assert result.attention_likelihood == "low"
        assert result.direction == "bearish"
        assert result.is_quiet_filing is True
        assert result.boost_multiplier == 1.5
        assert result.entity_id == "AAPL"
        assert result.form_type == "8-K"
        assert result.input_tokens == 5000
        assert result.output_tokens == 200
        assert result.estimated_cost > 0

    def test_no_api_key_returns_default(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        result = _run(call_triage(
            filing_text="text",
            entity_id="AAPL",
            form_type="10-K",
            filing_date="2026-02-25",
            context=SAMPLE_CONTEXT,
        ))
        assert result.materiality_score == 1
        assert result.attention_likelihood == "medium"
        assert result.is_quiet_filing is False

    def test_malformed_json_returns_default(self):
        mock_resp = _mock_response(text="not json at all")

        with patch("engine.ai_edges.quiet_filing_triage.triage.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                result = _run(call_triage(
                    filing_text="text",
                    entity_id="AAPL",
                    form_type="10-K",
                    filing_date="2026-02-25",
                    context=SAMPLE_CONTEXT,
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert result.materiality_score == 1
        assert result.is_quiet_filing is False

    def test_api_error_retries_then_default(self):
        with patch("engine.ai_edges.quiet_filing_triage.triage.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                side_effect=Exception("API overloaded"),
            )

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                result = _run(call_triage(
                    filing_text="text",
                    entity_id="AAPL",
                    form_type="10-K",
                    filing_date="2026-02-25",
                    context=SAMPLE_CONTEXT,
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert result.materiality_score == 1
        assert result.is_quiet_filing is False
        assert mock_client.messages.create.call_count == 3  # 3 retries

    def test_api_succeeds_after_retry(self):
        mock_resp = _mock_response()

        with patch("engine.ai_edges.quiet_filing_triage.triage.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                side_effect=[
                    Exception("temporary"),
                    mock_resp,
                ],
            )

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                result = _run(call_triage(
                    filing_text="text",
                    entity_id="AAPL",
                    form_type="8-K",
                    filing_date="2026-02-25",
                    context=SAMPLE_CONTEXT,
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert result.materiality_score == 8
        assert mock_client.messages.create.call_count == 2

    def test_non_quiet_filing(self):
        """Low materiality → not a quiet filing."""
        low_score_json = json.dumps({
            "materiality_score": 3,
            "attention_likelihood": "low",
            "direction": "neutral",
            "reasoning": "Routine 10-K boilerplate.",
            "key_material_items": [],
            "suggested_urgency": "monitor",
        })
        mock_resp = _mock_response(text=low_score_json)

        with patch("engine.ai_edges.quiet_filing_triage.triage.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                result = _run(call_triage(
                    filing_text="text",
                    entity_id="AAPL",
                    form_type="10-K",
                    filing_date="2026-02-25",
                    context=SAMPLE_CONTEXT,
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert result.materiality_score == 3
        assert result.is_quiet_filing is False
        assert result.boost_multiplier == 1.0

    def test_custom_boost_multiplier(self):
        mock_resp = _mock_response()

        with patch("engine.ai_edges.quiet_filing_triage.triage.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                result = _run(call_triage(
                    filing_text="text",
                    entity_id="AAPL",
                    form_type="8-K",
                    filing_date="2026-02-25",
                    context=SAMPLE_CONTEXT,
                    boost_multiplier=2.0,
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert result.boost_multiplier == 2.0  # quiet filing + custom multiplier
