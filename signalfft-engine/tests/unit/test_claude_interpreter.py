"""Tests for Claude directional interpretation module.

Mocks the Anthropic API client — does NOT make real API calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import textwrap
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import engine.directional.claude_interpreter as ci
from engine.directional.claude_interpreter import (
    DirectionAssessment,
    _compute_prompt_version,
    _log_cost,
    _parse_response,
    _validate_response,
    build_prompt,
    interpret_direction,
    store_assessment,
    truncate_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PROMPT_YAML = textwrap.dedent("""\
    version: "1.0"
    model: "claude-sonnet-4-20250514"
    max_input_chars: 32000

    system: |
      You are a financial analyst specializing in SEC filing analysis.

    user_template: |
      Entity: {entity_id}
      Form Type: {form_type}
      Section: {section_name}
      Filing Date: {filing_date}
      Text: {text}
""")

SAMPLE_CONTEXT = {
    "form_type": "10-K",
    "section_name": "item_7",
    "filing_date": "2026-02-15",
}

VALID_CLAUDE_JSON = json.dumps({
    "direction": "bullish",
    "confidence": 0.85,
    "reasoning": "Revenue grew 20% year-over-year with strong enterprise adoption.",
    "key_directional_factors": [
        "revenue growth acceleration",
        "enterprise adoption increasing",
        "margin expansion",
    ],
})


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _mock_response(text: str = VALID_CLAUDE_JSON, input_tokens: int = 1500, output_tokens: int = 80):
    """Create a mock Anthropic API response."""
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_prompt_cache():
    """Reset the module-level prompt cache between tests."""
    ci._prompt_cache = None
    ci._prompt_version_cache = None
    yield
    ci._prompt_cache = None
    ci._prompt_version_cache = None


@pytest.fixture
def prompt_yaml(tmp_path):
    """Write a test prompt YAML and set PROMPT_TEMPLATE_PATH."""
    yaml_path = tmp_path / "directional_interpretation.yaml"
    yaml_path.write_text(SAMPLE_PROMPT_YAML)
    os.environ["PROMPT_TEMPLATE_PATH"] = str(yaml_path)
    yield yaml_path
    os.environ.pop("PROMPT_TEMPLATE_PATH", None)


@pytest.fixture
def api_key_env():
    """Set a fake ANTHROPIC_API_KEY."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
    yield
    os.environ.pop("ANTHROPIC_API_KEY", None)


@pytest.fixture
def aws_env():
    """Set up mocked AWS with a DynamoDB events table."""
    with mock_aws():
        region = "us-east-1"
        os.environ["AWS_REGION"] = region
        os.environ["ENVIRONMENT"] = "test"
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"

        table_name = "test-signalfft-events"
        dynamodb = boto3.client("dynamodb", region_name=region)
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "region": region,
            "table_name": table_name,
        }


# ===========================================================================
# Prompt construction
# ===========================================================================


class TestPromptConstruction:
    """Tests for prompt template loading and construction."""

    def test_loads_yaml_template(self, prompt_yaml):
        """Prompt template should be loaded from YAML file."""
        system, user = build_prompt("Some text", "AAPL", SAMPLE_CONTEXT)
        assert "financial analyst" in system
        assert "AAPL" in user

    def test_system_prompt_from_yaml(self, prompt_yaml):
        """System prompt should come from the YAML system field."""
        system, _ = build_prompt("text", "AAPL", SAMPLE_CONTEXT)
        assert "SEC filing analysis" in system

    def test_user_prompt_contains_entity_and_context(self, prompt_yaml):
        """User prompt should interpolate entity_id and context fields."""
        _, user = build_prompt("Sample filing text", "MSFT", SAMPLE_CONTEXT)
        assert "MSFT" in user
        assert "10-K" in user
        assert "item_7" in user
        assert "2026-02-15" in user
        assert "Sample filing text" in user

    def test_user_prompt_defaults_for_missing_context(self, prompt_yaml):
        """Missing context fields should default to 'unknown'."""
        _, user = build_prompt("text", "AAPL", {})
        assert "unknown" in user

    def test_prompt_version_is_sha256_prefix(self, prompt_yaml):
        """Prompt version should be first 12 chars of SHA-256 hash."""
        build_prompt("text", "AAPL", SAMPLE_CONTEXT)
        version = ci._get_prompt_version()
        expected = hashlib.sha256(SAMPLE_PROMPT_YAML.encode("utf-8")).hexdigest()[:12]
        assert version == expected
        assert len(version) == 12

    def test_prompt_version_changes_with_content(self, tmp_path):
        """Different prompt content should produce a different version hash."""
        v1 = _compute_prompt_version("version 1 content")
        v2 = _compute_prompt_version("version 2 content")
        assert v1 != v2

    def test_prompt_cached_after_first_load(self, prompt_yaml):
        """Second call should use cached template, not re-read file."""
        build_prompt("text1", "AAPL", SAMPLE_CONTEXT)
        # Delete the file — cache should still work
        prompt_yaml.unlink()
        system, _ = build_prompt("text2", "AAPL", SAMPLE_CONTEXT)
        assert "financial analyst" in system


# ===========================================================================
# Text truncation
# ===========================================================================


class TestTextTruncation:
    """Tests for the text truncation logic."""

    def test_short_text_unchanged(self):
        """Text under the limit should be returned as-is."""
        text = "Short text"
        assert truncate_text(text) == text

    def test_at_limit_unchanged(self):
        """Text exactly at the limit should not be truncated."""
        text = "x" * ci._MAX_INPUT_CHARS
        assert truncate_text(text) == text

    def test_over_limit_truncated(self):
        """Text over the limit should be truncated to max_chars or less."""
        text = "x" * 40_000
        result = truncate_text(text, max_chars=1000)
        assert len(result) <= 1000

    def test_truncation_marker_present(self):
        """Truncated text should contain the truncation marker."""
        text = "x" * 40_000
        result = truncate_text(text, max_chars=1000)
        assert "[... middle section truncated for length ...]" in result

    def test_beginning_preserved(self):
        """First characters of original text should be in result."""
        text = "BEGINNING_MARKER" + "x" * 40_000 + "END_MARKER"
        result = truncate_text(text, max_chars=1000)
        assert result.startswith("BEGINNING_MARKER")

    def test_end_preserved(self):
        """Last characters of original text should be in result."""
        text = "BEGINNING_MARKER" + "x" * 40_000 + "END_MARKER"
        result = truncate_text(text, max_chars=1000)
        assert result.endswith("END_MARKER")

    def test_custom_max_chars(self):
        """Custom max_chars parameter should be respected."""
        text = "a" * 500
        result = truncate_text(text, max_chars=200)
        assert len(result) <= 200
        assert "[... middle section truncated for length ...]" in result


# ===========================================================================
# Response parsing
# ===========================================================================


class TestResponseParsing:
    """Tests for Claude response parsing and validation."""

    def test_valid_json_parsed(self):
        """Well-formed JSON should be parsed correctly."""
        result = _parse_response(VALID_CLAUDE_JSON)
        assert result["direction"] == "bullish"
        assert result["confidence"] == 0.85

    def test_json_with_markdown_fences(self):
        """JSON wrapped in markdown code fences should be parsed."""
        wrapped = f"```json\n{VALID_CLAUDE_JSON}\n```"
        result = _parse_response(wrapped)
        assert result["direction"] == "bullish"

    def test_json_with_bare_fences(self):
        """JSON wrapped in bare ``` fences should be parsed."""
        wrapped = f"```\n{VALID_CLAUDE_JSON}\n```"
        result = _parse_response(wrapped)
        assert result["direction"] == "bullish"

    def test_malformed_json_raises(self):
        """Malformed JSON should raise JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            _parse_response("this is not json at all")

    def test_valid_direction_values(self):
        """All three valid direction values should be preserved."""
        for direction in ("bullish", "bearish", "neutral"):
            data = {"direction": direction, "confidence": 0.5}
            result = _validate_response(data)
            assert result["direction"] == direction

    def test_invalid_direction_defaults_to_neutral(self):
        """Unknown direction value should default to 'neutral'."""
        data = {"direction": "sideways", "confidence": 0.5}
        result = _validate_response(data)
        assert result["direction"] == "neutral"

    def test_missing_direction_defaults_to_neutral(self):
        """Missing direction key should default to 'neutral'."""
        result = _validate_response({"confidence": 0.5})
        assert result["direction"] == "neutral"

    def test_confidence_clamped_high(self):
        """Confidence above 1.0 should be clamped to 1.0."""
        result = _validate_response({"direction": "bullish", "confidence": 1.5})
        assert result["confidence"] == 1.0

    def test_confidence_clamped_low(self):
        """Confidence below 0.0 should be clamped to 0.0."""
        result = _validate_response({"direction": "bullish", "confidence": -0.5})
        assert result["confidence"] == 0.0

    def test_non_numeric_confidence_defaults_to_zero(self):
        """Non-numeric confidence should default to 0.0."""
        result = _validate_response({"direction": "bullish", "confidence": "high"})
        assert result["confidence"] == 0.0

    def test_factors_capped_at_five(self):
        """Key directional factors list should be capped at 5."""
        data = {
            "direction": "bullish",
            "confidence": 0.8,
            "key_directional_factors": ["a", "b", "c", "d", "e", "f", "g"],
        }
        result = _validate_response(data)
        assert len(result["key_directional_factors"]) == 5

    def test_non_list_factors_defaults_to_empty(self):
        """Non-list key_directional_factors should default to empty list."""
        data = {"direction": "neutral", "key_directional_factors": "not a list"}
        result = _validate_response(data)
        assert result["key_directional_factors"] == []

    def test_reasoning_cast_to_string(self):
        """Reasoning should be cast to string."""
        data = {"direction": "neutral", "reasoning": 42}
        result = _validate_response(data)
        assert result["reasoning"] == "42"


# ===========================================================================
# interpret_direction (main function)
# ===========================================================================


class TestInterpretDirection:
    """Tests for the main async interpret_direction function."""

    def test_successful_interpretation(self, prompt_yaml, api_key_env):
        """Successful API call should return a DirectionAssessment."""
        mock_response = _mock_response()

        with patch("engine.directional.claude_interpreter.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            result = _run(interpret_direction(
                "Revenue grew 20% year-over-year.",
                "AAPL",
                SAMPLE_CONTEXT,
            ))

        assert isinstance(result, DirectionAssessment)
        assert result.direction == "bullish"
        assert result.confidence == 0.85
        assert result.entity_id == "AAPL"
        assert result.section_name == "item_7"
        assert result.filing_date == "2026-02-15"
        assert len(result.key_directional_factors) == 3
        assert result.prompt_version == ci._get_prompt_version()

    def test_missing_api_key_returns_neutral(self, prompt_yaml):
        """Missing ANTHROPIC_API_KEY should return neutral with confidence 0.0."""
        os.environ.pop("ANTHROPIC_API_KEY", None)

        result = _run(interpret_direction("text", "AAPL", SAMPLE_CONTEXT))

        assert result.direction == "neutral"
        assert result.confidence == 0.0
        assert "unavailable" in result.reasoning.lower()

    def test_malformed_json_returns_neutral(self, prompt_yaml, api_key_env):
        """Malformed JSON from Claude should return neutral assessment."""
        mock_response = _mock_response(text="This is not valid JSON {{{")

        with patch("engine.directional.claude_interpreter.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            result = _run(interpret_direction("text", "AAPL", SAMPLE_CONTEXT))

        assert result.direction == "neutral"
        assert result.confidence == 0.0

    def test_retry_on_api_error(self, prompt_yaml, api_key_env):
        """API errors should trigger retries with eventual success."""
        mock_response = _mock_response()

        with patch("engine.directional.claude_interpreter.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            # Fail twice, succeed on third attempt
            mock_client.messages.create = AsyncMock(
                side_effect=[
                    Exception("API overloaded"),
                    Exception("API overloaded"),
                    mock_response,
                ]
            )

            with patch("engine.directional.claude_interpreter.asyncio.sleep", new_callable=AsyncMock):
                result = _run(interpret_direction("text", "AAPL", SAMPLE_CONTEXT))

        assert result.direction == "bullish"
        assert mock_client.messages.create.call_count == 3

    def test_all_retries_exhausted_returns_neutral(self, prompt_yaml, api_key_env):
        """All retries exhausted should return neutral assessment."""
        with patch("engine.directional.claude_interpreter.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client

            mock_client.messages.create = AsyncMock(
                side_effect=Exception("API permanently down")
            )

            with patch("engine.directional.claude_interpreter.asyncio.sleep", new_callable=AsyncMock):
                result = _run(interpret_direction("text", "AAPL", SAMPLE_CONTEXT))

        assert result.direction == "neutral"
        assert result.confidence == 0.0
        assert mock_client.messages.create.call_count == 3

    def test_cost_logged(self, prompt_yaml, api_key_env, caplog):
        """API call should log cost with token counts."""
        mock_response = _mock_response(input_tokens=2000, output_tokens=100)

        with patch("engine.directional.claude_interpreter.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            with caplog.at_level(logging.INFO, logger="engine.directional.claude_interpreter"):
                _run(interpret_direction("text", "AAPL", SAMPLE_CONTEXT))

        cost_logs = [r for r in caplog.records if "Claude API cost" in r.message]
        assert len(cost_logs) == 1
        assert "input_tokens=2000" in cost_logs[0].message
        assert "output_tokens=100" in cost_logs[0].message

    def test_model_from_env_var(self, prompt_yaml, api_key_env):
        """CLAUDE_MODEL_ID env var should override the default model."""
        os.environ["CLAUDE_MODEL_ID"] = "claude-haiku-4-5-20251001"
        mock_response = _mock_response()

        try:
            with patch("engine.directional.claude_interpreter.anthropic") as mock_anthropic:
                mock_client = MagicMock()
                mock_anthropic.AsyncAnthropic.return_value = mock_client
                mock_client.messages.create = AsyncMock(return_value=mock_response)

                result = _run(interpret_direction("text", "AAPL", SAMPLE_CONTEXT))

            assert result.claude_model_version == "claude-haiku-4-5-20251001"
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
        finally:
            os.environ.pop("CLAUDE_MODEL_ID", None)

    def test_bearish_response_parsed(self, prompt_yaml, api_key_env):
        """Bearish direction from Claude should be correctly parsed."""
        bearish_json = json.dumps({
            "direction": "bearish",
            "confidence": 0.72,
            "reasoning": "New material weakness disclosed in internal controls.",
            "key_directional_factors": ["material weakness", "internal control deficiency"],
        })
        mock_response = _mock_response(text=bearish_json)

        with patch("engine.directional.claude_interpreter.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            result = _run(interpret_direction("text", "AAPL", SAMPLE_CONTEXT))

        assert result.direction == "bearish"
        assert result.confidence == 0.72
        assert "material weakness" in result.key_directional_factors


# ===========================================================================
# DynamoDB storage
# ===========================================================================


class TestStoreAssessment:
    """Tests for DynamoDB storage."""

    def test_correct_pk_sk(self, aws_env):
        """Assessment should be stored with correct PK and SK."""
        assessment = DirectionAssessment(
            direction="bullish",
            confidence=0.85,
            reasoning="Strong growth indicators.",
            key_directional_factors=["revenue growth"],
            claude_model_version="claude-sonnet-4-20250514",
            prompt_version="abc123def456",
            entity_id="AAPL",
            section_name="item_7",
            filing_date="2026-02-15",
            created_at="2026-02-15T12:00:00+00:00",
        )

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        store_assessment(assessment, table)

        response = table.get_item(
            Key={
                "PK": "ENTITY#AAPL",
                "SK": "DIRECTION#item_7#2026-02-15",
            }
        )
        item = response["Item"]
        assert item["PK"] == "ENTITY#AAPL"
        assert item["SK"] == "DIRECTION#item_7#2026-02-15"

    def test_all_fields_stored(self, aws_env):
        """All assessment fields should be persisted in DynamoDB."""
        assessment = DirectionAssessment(
            direction="bearish",
            confidence=0.65,
            reasoning="Regulatory concerns.",
            key_directional_factors=["regulatory action", "compliance risk"],
            claude_model_version="claude-sonnet-4-20250514",
            prompt_version="abc123def456",
            entity_id="MSFT",
            section_name="item_1a",
            filing_date="2026-03-01",
            created_at="2026-03-01T08:00:00+00:00",
        )

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        store_assessment(assessment, table)

        response = table.get_item(
            Key={
                "PK": "ENTITY#MSFT",
                "SK": "DIRECTION#item_1a#2026-03-01",
            }
        )
        item = response["Item"]
        assert item["direction"] == "bearish"
        assert item["reasoning"] == "Regulatory concerns."
        assert item["key_directional_factors"] == ["regulatory action", "compliance risk"]
        assert item["claude_model_version"] == "claude-sonnet-4-20250514"
        assert item["prompt_version"] == "abc123def456"
        assert item["entity_id"] == "MSFT"
        assert item["section_name"] == "item_1a"
        assert item["filing_date"] == "2026-03-01"
        assert item["created_at"] == "2026-03-01T08:00:00+00:00"
        assert item["source"] == "claude_interpreter"

    def test_confidence_stored_as_decimal(self, aws_env):
        """Confidence should be stored as Decimal for DynamoDB compatibility."""
        assessment = DirectionAssessment(
            direction="neutral",
            confidence=0.42,
            reasoning="Mixed signals.",
            key_directional_factors=[],
            claude_model_version="claude-sonnet-4-20250514",
            prompt_version="abc123def456",
            entity_id="GOOG",
            section_name="item_1",
            filing_date="2026-01-15",
            created_at="2026-01-15T10:00:00+00:00",
        )

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        store_assessment(assessment, table)

        response = table.get_item(
            Key={
                "PK": "ENTITY#GOOG",
                "SK": "DIRECTION#item_1#2026-01-15",
            }
        )
        item = response["Item"]
        assert isinstance(item["confidence"], Decimal)
        assert item["confidence"] == Decimal("0.42")


# ===========================================================================
# Cost calculation
# ===========================================================================


class TestCostCalculation:
    """Tests for cost tracking."""

    def test_cost_calculation_sonnet(self, caplog):
        """Cost should be calculated with Sonnet pricing ($3/M input, $15/M output)."""
        with caplog.at_level(logging.INFO, logger="engine.directional.claude_interpreter"):
            _log_cost("AAPL", "item_7", 1_000_000, 100_000, "claude-sonnet-4-20250514")

        cost_logs = [r for r in caplog.records if "Claude API cost" in r.message]
        assert len(cost_logs) == 1
        # 1M input * $3/M = $3.00, 100K output * $15/M = $1.50, total = $4.50
        assert "$4.500000" in cost_logs[0].message

    def test_cost_logged_with_entity(self, caplog):
        """Cost log should include entity_id and section_name."""
        with caplog.at_level(logging.INFO, logger="engine.directional.claude_interpreter"):
            _log_cost("BSX", "item_1a", 500, 50, "claude-sonnet-4-20250514")

        cost_logs = [r for r in caplog.records if "Claude API cost" in r.message]
        assert "entity=BSX" in cost_logs[0].message
        assert "section=item_1a" in cost_logs[0].message

    def test_small_cost_precision(self, caplog):
        """Small token counts should still produce precise cost estimates."""
        with caplog.at_level(logging.INFO, logger="engine.directional.claude_interpreter"):
            _log_cost("AAPL", "item_7", 1500, 80, "claude-sonnet-4-20250514")

        cost_logs = [r for r in caplog.records if "Claude API cost" in r.message]
        assert len(cost_logs) == 1
        # 1500 * $3/M = $0.0045, 80 * $15/M = $0.0012, total = $0.0057
        assert "$0.005700" in cost_logs[0].message


# ===========================================================================
# DirectionAssessment model
# ===========================================================================


class TestDirectionAssessmentModel:
    """Tests for the DirectionAssessment dataclass."""

    def test_dataclass_creation(self):
        """DirectionAssessment should be creatable with all fields."""
        assessment = DirectionAssessment(
            direction="bullish",
            confidence=0.9,
            reasoning="Strong fundamentals.",
            key_directional_factors=["revenue growth"],
            claude_model_version="claude-sonnet-4-20250514",
            prompt_version="abc123",
            entity_id="AAPL",
            section_name="item_7",
            filing_date="2026-02-15",
            created_at="2026-02-15T00:00:00+00:00",
        )
        assert assessment.direction == "bullish"
        assert assessment.confidence == 0.9

    def test_uses_slots(self):
        """DirectionAssessment should use __slots__ for memory efficiency."""
        assert hasattr(DirectionAssessment, "__slots__")

    def test_factors_is_list(self):
        """key_directional_factors should be a list."""
        assessment = DirectionAssessment(
            direction="neutral",
            confidence=0.5,
            reasoning="Mixed.",
            key_directional_factors=["a", "b"],
            claude_model_version="model",
            prompt_version="v1",
            entity_id="TEST",
            section_name="item_1",
            filing_date="2026-01-01",
            created_at="2026-01-01T00:00:00+00:00",
        )
        assert isinstance(assessment.key_directional_factors, list)
        assert len(assessment.key_directional_factors) == 2


# ===========================================================================
# Prompt version hashing
# ===========================================================================


class TestPromptVersioning:
    """Tests for prompt template versioning."""

    def test_same_content_same_hash(self):
        """Identical content should produce identical hashes."""
        content = "test prompt content"
        assert _compute_prompt_version(content) == _compute_prompt_version(content)

    def test_different_content_different_hash(self):
        """Different content should produce different hashes."""
        v1 = _compute_prompt_version("version 1")
        v2 = _compute_prompt_version("version 2")
        assert v1 != v2

    def test_hash_length_is_12(self):
        """Prompt version hash should be 12 characters."""
        version = _compute_prompt_version("any content")
        assert len(version) == 12

    def test_hash_is_hex(self):
        """Prompt version hash should be valid hex."""
        version = _compute_prompt_version("any content")
        int(version, 16)  # Should not raise
