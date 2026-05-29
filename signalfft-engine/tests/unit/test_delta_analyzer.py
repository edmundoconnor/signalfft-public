"""Tests for semantic delta analyzer — prompt construction, Claude API mocking,
response parsing, truncation, validation, retry, and no-API-key fallback."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.ai_edges.semantic_delta.analyzer import (
    SemanticShift,
    analyze_delta,
    build_prompt,
    clear_prompt_cache,
    get_prompt_version,
    log_cost,
    parse_response,
    truncate_text,
    validate_shifts,
    _load_prompt_template,
    VALID_SHIFT_TYPES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


VALID_CLAUDE_JSON = json.dumps({
    "shifts": [
        {
            "shift_type": "risk_escalation",
            "description": "New cybersecurity risk disclosure added.",
            "severity": 4,
            "direction": "bearish",
            "evidence": {
                "previous_excerpt": "No cybersecurity disclosure.",
                "current_excerpt": "The company experienced a data breach...",
            },
        },
        {
            "shift_type": "guidance_change",
            "description": "Revenue guidance raised from $5B to $5.5B.",
            "severity": 3,
            "direction": "bullish",
            "evidence": {
                "previous_excerpt": "Revenue expected at $5B.",
                "current_excerpt": "Revenue expected at $5.5B.",
            },
        },
    ]
})


def _mock_response(text: str = VALID_CLAUDE_JSON, input_tokens: int = 8000, output_tokens: int = 500):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    return response


@pytest.fixture(autouse=True)
def _clear():
    clear_prompt_cache()
    yield
    clear_prompt_cache()


# ---------------------------------------------------------------------------
# truncate_text
# ---------------------------------------------------------------------------

class TestTruncateText:
    def test_short_text_unchanged(self):
        assert truncate_text("Hello", 100) == "Hello"

    def test_exact_limit_unchanged(self):
        text = "x" * 24000
        assert truncate_text(text) == text

    def test_long_text_truncated(self):
        text = "A" * 30000
        result = truncate_text(text)
        assert len(result) < 30000
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
        assert len(data["shifts"]) == 2

    def test_json_with_markdown_fences(self):
        wrapped = f"```json\n{VALID_CLAUDE_JSON}\n```"
        data = parse_response(wrapped)
        assert len(data["shifts"]) == 2

    def test_json_with_bare_fences(self):
        wrapped = f"```\n{VALID_CLAUDE_JSON}\n```"
        data = parse_response(wrapped)
        assert len(data["shifts"]) == 2

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_response("not json at all")

    def test_empty_shifts(self):
        data = parse_response('{"shifts": []}')
        assert data["shifts"] == []


# ---------------------------------------------------------------------------
# validate_shifts
# ---------------------------------------------------------------------------

class TestValidateShifts:
    def test_valid_shifts(self):
        data = json.loads(VALID_CLAUDE_JSON)
        shifts = validate_shifts(data)
        assert len(shifts) == 2
        assert all(isinstance(s, SemanticShift) for s in shifts)
        assert shifts[0].shift_type == "risk_escalation"
        assert shifts[0].severity == 4
        assert shifts[0].direction == "bearish"

    def test_invalid_shift_type_filtered(self):
        data = {"shifts": [{"shift_type": "made_up", "severity": 3, "direction": "bearish"}]}
        shifts = validate_shifts(data)
        assert len(shifts) == 0

    def test_severity_clamped_low(self):
        data = {"shifts": [{"shift_type": "risk_escalation", "severity": -5, "direction": "bearish"}]}
        shifts = validate_shifts(data)
        assert shifts[0].severity == 1

    def test_severity_clamped_high(self):
        data = {"shifts": [{"shift_type": "risk_escalation", "severity": 99, "direction": "bearish"}]}
        shifts = validate_shifts(data)
        assert shifts[0].severity == 5

    def test_severity_string_coerced(self):
        data = {"shifts": [{"shift_type": "risk_escalation", "severity": "3", "direction": "bearish"}]}
        shifts = validate_shifts(data)
        assert shifts[0].severity == 3

    def test_severity_non_numeric_defaults(self):
        data = {"shifts": [{"shift_type": "risk_escalation", "severity": "abc", "direction": "bearish"}]}
        shifts = validate_shifts(data)
        assert shifts[0].severity == 1

    def test_invalid_direction_defaults_neutral(self):
        data = {"shifts": [{"shift_type": "risk_escalation", "severity": 3, "direction": "sideways"}]}
        shifts = validate_shifts(data)
        assert shifts[0].direction == "neutral"

    def test_missing_fields_use_defaults(self):
        data = {"shifts": [{"shift_type": "risk_escalation"}]}
        shifts = validate_shifts(data)
        assert shifts[0].severity == 1
        assert shifts[0].direction == "neutral"
        assert shifts[0].description == ""

    def test_max_20_shifts(self):
        data = {"shifts": [
            {"shift_type": "risk_escalation", "severity": 3, "direction": "bearish"}
            for _ in range(30)
        ]}
        shifts = validate_shifts(data)
        assert len(shifts) == 20

    def test_non_list_shifts_returns_empty(self):
        shifts = validate_shifts({"shifts": "not a list"})
        assert shifts == []

    def test_missing_shifts_key_returns_empty(self):
        shifts = validate_shifts({})
        assert shifts == []

    def test_non_dict_shift_entry_skipped(self):
        data = {"shifts": ["not a dict", {"shift_type": "risk_escalation", "severity": 3, "direction": "bearish"}]}
        shifts = validate_shifts(data)
        assert len(shifts) == 1

    def test_evidence_preserved(self):
        data = json.loads(VALID_CLAUDE_JSON)
        shifts = validate_shifts(data)
        assert "previous_excerpt" in shifts[0].evidence
        assert "current_excerpt" in shifts[0].evidence

    def test_evidence_non_dict_defaults_empty(self):
        data = {"shifts": [{"shift_type": "risk_escalation", "severity": 3, "direction": "bearish", "evidence": "not dict"}]}
        shifts = validate_shifts(data)
        assert shifts[0].evidence == {"previous_excerpt": "", "current_excerpt": ""}

    def test_all_valid_shift_types(self):
        for st in VALID_SHIFT_TYPES:
            data = {"shifts": [{"shift_type": st, "severity": 3, "direction": "bearish"}]}
            shifts = validate_shifts(data)
            assert len(shifts) == 1
            assert shifts[0].shift_type == st


# ---------------------------------------------------------------------------
# Prompt loading and versioning
# ---------------------------------------------------------------------------

class TestPromptLoading:
    def test_prompt_version_is_stable(self):
        v1 = get_prompt_version()
        v2 = get_prompt_version()
        assert v1 == v2
        assert len(v1) == 12

    def test_prompt_template_has_required_keys(self):
        template = _load_prompt_template()
        assert "system" in template
        assert "user_template" in template


class TestBuildPrompt:
    def test_returns_system_and_user(self):
        system, user = build_prompt(
            "Current text", "Previous text", "AAPL",
            "10-K", "item_7", "2026-03-01", "2025-03-01",
        )
        assert len(system) > 0
        assert "AAPL" in user
        assert "10-K" in user
        assert "item_7" in user
        assert "2026-03-01" in user
        assert "2025-03-01" in user
        assert "Current text" in user
        assert "Previous text" in user

    def test_long_text_truncated_in_prompt(self):
        long_text = "A" * 50000
        _, user = build_prompt(
            long_text, "short", "AAPL",
            "10-K", "item_7", "2026-03-01", "2025-03-01",
        )
        assert "truncated" in user


# ---------------------------------------------------------------------------
# log_cost
# ---------------------------------------------------------------------------

class TestLogCost:
    def test_cost_calculation(self):
        cost = log_cost("AAPL", "item_7", 8000, 500, "claude-sonnet-4-20250514")
        # 8000 * 3/M = $0.024; 500 * 15/M = $0.0075; total = $0.0315
        assert abs(cost - 0.0315) < 0.0001

    def test_zero_tokens(self):
        cost = log_cost("AAPL", "item_7", 0, 0, "test-model")
        assert cost == 0.0


# ---------------------------------------------------------------------------
# analyze_delta (async, mock Anthropic)
# ---------------------------------------------------------------------------

class TestAnalyzeDelta:
    def test_successful_analysis(self):
        mock_resp = _mock_response()

        with patch("engine.ai_edges.semantic_delta.analyzer.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                shifts = _run(analyze_delta(
                    current_text="Current filing text about data breach...",
                    previous_text="Previous filing text without data breach...",
                    entity_id="AAPL",
                    form_type="10-K",
                    section_name="item_7",
                    current_date="2026-03-01",
                    previous_date="2025-03-01",
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert len(shifts) == 2
        assert shifts[0].shift_type == "risk_escalation"
        assert shifts[1].shift_type == "guidance_change"

    def test_no_api_key_returns_empty(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        shifts = _run(analyze_delta(
            current_text="text", previous_text="text",
            entity_id="AAPL", form_type="10-K", section_name="item_7",
            current_date="2026-03-01", previous_date="2025-03-01",
        ))
        assert shifts == []

    def test_malformed_json_returns_empty(self):
        mock_resp = _mock_response(text="not json at all")

        with patch("engine.ai_edges.semantic_delta.analyzer.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                shifts = _run(analyze_delta(
                    current_text="text", previous_text="text",
                    entity_id="AAPL", form_type="10-K", section_name="item_7",
                    current_date="2026-03-01", previous_date="2025-03-01",
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert shifts == []

    def test_api_error_retries_then_empty(self):
        with patch("engine.ai_edges.semantic_delta.analyzer.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                side_effect=Exception("API overloaded"),
            )

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                shifts = _run(analyze_delta(
                    current_text="text", previous_text="text",
                    entity_id="AAPL", form_type="10-K", section_name="item_7",
                    current_date="2026-03-01", previous_date="2025-03-01",
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert shifts == []
        assert mock_client.messages.create.call_count == 3

    def test_api_succeeds_after_retry(self):
        mock_resp = _mock_response()

        with patch("engine.ai_edges.semantic_delta.analyzer.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                side_effect=[Exception("temporary"), mock_resp],
            )

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                shifts = _run(analyze_delta(
                    current_text="text", previous_text="text",
                    entity_id="AAPL", form_type="10-K", section_name="item_7",
                    current_date="2026-03-01", previous_date="2025-03-01",
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert len(shifts) == 2
        assert mock_client.messages.create.call_count == 2

    def test_empty_shifts_response(self):
        mock_resp = _mock_response(text='{"shifts": []}')

        with patch("engine.ai_edges.semantic_delta.analyzer.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                shifts = _run(analyze_delta(
                    current_text="text", previous_text="text",
                    entity_id="AAPL", form_type="10-K", section_name="item_7",
                    current_date="2026-03-01", previous_date="2025-03-01",
                ))
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        assert shifts == []
