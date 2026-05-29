"""Tests for semantic delta scoring — config loading, shift type scoring,
severity scaling, direction multipliers, section weights, and aggregation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.ai_edges.semantic_delta.scoring import (
    DeltaScore,
    DeltaScoringConfig,
    clear_config_cache,
    load_config,
    score_delta,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear():
    clear_config_cache()
    yield
    clear_config_cache()


@pytest.fixture
def config() -> DeltaScoringConfig:
    return load_config()


def _shift(
    shift_type: str = "risk_escalation",
    severity: int = 3,
    direction: str = "bearish",
) -> dict:
    return {"shift_type": shift_type, "severity": severity, "direction": direction}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_loads_successfully(self, config):
        assert isinstance(config, DeltaScoringConfig)
        assert len(config.shift_type_base_scores) == 6
        assert len(config.direction_multipliers) == 3
        assert len(config.severity_weights) == 5

    def test_config_cached(self):
        c1 = load_config()
        c2 = load_config()
        assert c1 is c2

    def test_config_values(self, config):
        assert config.shift_type_base_scores["guidance_change"] == 0.9
        assert config.shift_type_base_scores["risk_escalation"] == 0.8
        assert config.direction_multipliers["bearish"] == -1.0
        assert config.severity_weights["5"] == 1.0
        assert config.section_weights["item_7"] == 1.0
        assert config.diminishing_factor == 0.3
        assert config.max_composite_score == 1.0

    def test_config_from_env_var(self, tmp_path):
        config_data = {
            "shift_type_base_scores": {"risk_escalation": 0.5},
            "direction_multipliers": {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0},
            "severity_weights": {"1": 0.2, "2": 0.4, "3": 0.6, "4": 0.8, "5": 1.0},
            "section_weights": {"item_7": 1.0, "default": 0.5},
            "aggregation": {"method": "max_plus_diminishing", "diminishing_factor": 0.3, "max_composite_score": 1.0},
        }
        cfg_path = tmp_path / "delta_scoring.json"
        cfg_path.write_text(json.dumps(config_data))

        os.environ["DELTA_SCORING_CONFIG_PATH"] = str(cfg_path)
        try:
            clear_config_cache()
            c = load_config()
            assert c.shift_type_base_scores["risk_escalation"] == 0.5
        finally:
            os.environ.pop("DELTA_SCORING_CONFIG_PATH", None)

    def test_config_env_var_directory(self, tmp_path):
        config_data = {
            "shift_type_base_scores": {"risk_escalation": 0.99},
            "direction_multipliers": {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0},
            "severity_weights": {"1": 0.2, "2": 0.4, "3": 0.6, "4": 0.8, "5": 1.0},
            "section_weights": {"default": 0.5},
            "aggregation": {"method": "max_plus_diminishing", "diminishing_factor": 0.3, "max_composite_score": 1.0},
        }
        cfg_path = tmp_path / "delta_scoring.json"
        cfg_path.write_text(json.dumps(config_data))

        os.environ["DELTA_SCORING_CONFIG_PATH"] = str(tmp_path)
        try:
            clear_config_cache()
            c = load_config()
            assert c.shift_type_base_scores["risk_escalation"] == 0.99
        finally:
            os.environ.pop("DELTA_SCORING_CONFIG_PATH", None)


# ---------------------------------------------------------------------------
# Shift type base scores
# ---------------------------------------------------------------------------

class TestShiftTypeBaseScores:
    def test_risk_escalation(self, config):
        result = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        assert result.composite_score > 0
        assert result.top_shift_type == "risk_escalation"

    def test_risk_removal(self, config):
        result = score_delta([_shift("risk_removal", 5, "bullish")], "item_7", config)
        assert result.composite_score > 0
        assert result.top_shift_type == "risk_removal"

    def test_tone_shift(self, config):
        result = score_delta([_shift("tone_shift", 5, "bearish")], "item_7", config)
        assert result.composite_score > 0
        assert result.top_shift_type == "tone_shift"

    def test_guidance_change(self, config):
        result = score_delta([_shift("guidance_change", 5, "bearish")], "item_7", config)
        assert result.composite_score > 0
        assert result.top_shift_type == "guidance_change"

    def test_disclosure_addition(self, config):
        result = score_delta([_shift("disclosure_addition", 5, "bearish")], "item_7", config)
        assert result.composite_score > 0

    def test_disclosure_removal(self, config):
        result = score_delta([_shift("disclosure_removal", 5, "bullish")], "item_7", config)
        assert result.composite_score > 0

    def test_guidance_change_highest_base(self, config):
        """guidance_change has highest base score (0.9)."""
        gc = score_delta([_shift("guidance_change", 5, "bearish")], "item_7", config)
        re = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        assert gc.composite_score > re.composite_score

    def test_invalid_shift_type_filtered(self, config):
        result = score_delta([_shift("made_up_type", 5, "bearish")], "item_7", config)
        assert result.shift_count == 0
        assert result.composite_score == 0.0


# ---------------------------------------------------------------------------
# Severity scaling
# ---------------------------------------------------------------------------

class TestSeverityScaling:
    def test_higher_severity_higher_score(self, config):
        low = score_delta([_shift("risk_escalation", 1, "bearish")], "item_7", config)
        high = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        assert high.composite_score > low.composite_score

    def test_severity_clamped_low(self, config):
        result = score_delta([_shift("risk_escalation", -1, "bearish")], "item_7", config)
        # Should clamp to 1
        sev1 = score_delta([_shift("risk_escalation", 1, "bearish")], "item_7", config)
        assert result.composite_score == sev1.composite_score

    def test_severity_clamped_high(self, config):
        result = score_delta([_shift("risk_escalation", 10, "bearish")], "item_7", config)
        sev5 = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        assert result.composite_score == sev5.composite_score

    def test_all_five_severity_levels(self, config):
        scores = []
        for sev in range(1, 6):
            result = score_delta([_shift("risk_escalation", sev, "bearish")], "item_7", config)
            scores.append(result.composite_score)
        # Monotonically increasing
        for i in range(len(scores) - 1):
            assert scores[i + 1] > scores[i]


# ---------------------------------------------------------------------------
# Direction multipliers
# ---------------------------------------------------------------------------

class TestDirectionMultipliers:
    def test_bearish_produces_score(self, config):
        result = score_delta([_shift("risk_escalation", 3, "bearish")], "item_7", config)
        assert result.composite_score > 0
        assert result.dominant_direction == "bearish"

    def test_bullish_produces_score(self, config):
        result = score_delta([_shift("risk_removal", 3, "bullish")], "item_7", config)
        assert result.composite_score > 0
        assert result.dominant_direction == "bullish"

    def test_neutral_produces_zero(self, config):
        result = score_delta([_shift("risk_escalation", 5, "neutral")], "item_7", config)
        assert result.composite_score == 0.0
        assert result.dominant_direction == "neutral"

    def test_invalid_direction_defaults_neutral(self, config):
        result = score_delta(
            [{"shift_type": "risk_escalation", "severity": 5, "direction": "sideways"}],
            "item_7", config,
        )
        assert result.composite_score == 0.0


# ---------------------------------------------------------------------------
# Section weights
# ---------------------------------------------------------------------------

class TestSectionWeights:
    def test_item_7_full_weight(self, config):
        result = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        assert result.composite_score > 0

    def test_item_1a_lower_weight(self, config):
        r7 = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        r1a = score_delta([_shift("risk_escalation", 5, "bearish")], "item_1a", config)
        assert r7.composite_score > r1a.composite_score

    def test_10q_sections(self, config):
        rp1 = score_delta([_shift("risk_escalation", 5, "bearish")], "part1_item2", config)
        rp2 = score_delta([_shift("risk_escalation", 5, "bearish")], "part2_item1a", config)
        assert rp1.composite_score > rp2.composite_score  # 1.0 vs 0.8

    def test_unknown_section_uses_default(self, config):
        result = score_delta([_shift("risk_escalation", 5, "bearish")], "item_99", config)
        default_result = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        assert result.composite_score < default_result.composite_score  # 0.5 vs 1.0


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_empty_shifts_returns_zero(self, config):
        result = score_delta([], "item_7", config)
        assert result.composite_score == 0.0
        assert result.dominant_direction == "neutral"
        assert result.shift_count == 0
        assert result.top_shift_type == ""

    def test_single_shift(self, config):
        result = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        assert result.shift_count == 1
        assert result.composite_score > 0

    def test_max_plus_diminishing(self, config):
        """Two shifts: second adds diminishing_factor^1 * its score."""
        one = score_delta([_shift("risk_escalation", 5, "bearish")], "item_7", config)
        two = score_delta([
            _shift("risk_escalation", 5, "bearish"),
            _shift("tone_shift", 5, "bearish"),
        ], "item_7", config)
        assert two.composite_score > one.composite_score
        # But not double
        assert two.composite_score < one.composite_score * 2

    def test_max_composite_capped(self, config):
        """Many high-severity shifts should still be capped at 1.0."""
        shifts = [_shift("guidance_change", 5, "bearish") for _ in range(20)]
        result = score_delta(shifts, "item_7", config)
        assert result.composite_score <= 1.0

    def test_dominant_direction_majority_vote(self, config):
        """Direction is determined by score-weighted majority."""
        shifts = [
            _shift("guidance_change", 5, "bearish"),
            _shift("risk_removal", 1, "bullish"),
        ]
        result = score_delta(shifts, "item_7", config)
        assert result.dominant_direction == "bearish"

    def test_dominant_direction_bullish_majority(self, config):
        shifts = [
            _shift("guidance_change", 5, "bullish"),
            _shift("risk_escalation", 1, "bearish"),
        ]
        result = score_delta(shifts, "item_7", config)
        assert result.dominant_direction == "bullish"

    def test_all_neutral_dominant_neutral(self, config):
        shifts = [
            _shift("risk_escalation", 3, "neutral"),
            _shift("tone_shift", 3, "neutral"),
        ]
        result = score_delta(shifts, "item_7", config)
        assert result.dominant_direction == "neutral"
