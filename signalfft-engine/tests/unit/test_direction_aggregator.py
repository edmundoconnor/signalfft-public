"""Tests for engine.directional.aggregator — pure direction aggregation."""

from __future__ import annotations

import pytest

from engine.directional.aggregator import (
    DEFAULT_WEIGHTS,
    DirectionWeights,
    compute_direction_detailed,
    compute_direction_score,
)


# ===================================================================
# Pure function properties
# ===================================================================


class TestPureFunctionProperties:
    """Verify the function is deterministic with no side effects."""

    def test_deterministic_same_inputs_same_output(self):
        a = compute_direction_score(0.5, "bullish", 0.8)
        b = compute_direction_score(0.5, "bullish", 0.8)
        assert a == b

    def test_deterministic_across_many_calls(self):
        results = [compute_direction_score(0.3, "bearish", 0.6) for _ in range(100)]
        assert len(set(results)) == 1

    def test_no_side_effects_on_weights(self):
        w = DirectionWeights(lexicon_weight=0.5, claude_weight=0.5)
        compute_direction_score(0.5, "bullish", 0.8, weights=w)
        assert w.lexicon_weight == 0.5
        assert w.claude_weight == 0.5


# ===================================================================
# Claude available scenarios
# ===================================================================


class TestClaudeAvailable:
    """Scenarios where Claude direction and confidence are both provided."""

    def test_strong_bullish_agreement(self):
        """Lexicon positive + Claude bullish high confidence → strong positive."""
        score = compute_direction_score(0.8, "bullish", 0.9)
        assert score > 0.5
        assert score > 0  # bullish

    def test_strong_bearish_agreement(self):
        """Lexicon negative + Claude bearish high confidence → strong negative."""
        score = compute_direction_score(-0.8, "bearish", 0.9)
        assert score < -0.5
        assert score < 0  # bearish

    def test_agreement_amplifies(self):
        """When lexicon and Claude agree, signal is stronger than either alone."""
        score = compute_direction_score(0.6, "bullish", 0.8)
        # lexicon alone would be 0.6; with Claude agreement, should stay positive
        assert score > 0.3

    def test_disagreement_dampens(self):
        """Lexicon positive but Claude bearish → moderate/weak score."""
        score = compute_direction_score(0.6, "bearish", 0.8)
        # lexicon pushes positive, Claude pushes negative — result is weak
        assert abs(score) < 0.6  # weaker than either signal alone

    def test_claude_neutral_high_confidence_dampens_lexicon(self):
        """Claude says neutral with high confidence → dampens lexicon signal."""
        score = compute_direction_score(0.8, "neutral", 0.9)
        # Only lexicon contributes: 0.8 * 0.3 = 0.24 (claude_numeric = 0)
        assert score == pytest.approx(0.24)
        assert score < 0.8  # dampened compared to lexicon alone

    def test_claude_low_confidence_reduces_contribution(self):
        """Low confidence reduces Claude's effective weight."""
        high_conf = compute_direction_score(0.0, "bullish", 0.9)
        low_conf = compute_direction_score(0.0, "bullish", 0.1)
        assert high_conf > low_conf

    def test_exact_weighted_blend_bullish(self):
        """Verify exact math: 0.5 * 0.3 + (1.0 * 0.8) * 0.7 = 0.15 + 0.56 = 0.71."""
        score = compute_direction_score(0.5, "bullish", 0.8)
        assert score == pytest.approx(0.71)

    def test_exact_weighted_blend_bearish(self):
        """Verify exact math: -0.4 * 0.3 + (-1.0 * 0.6) * 0.7 = -0.12 + -0.42 = -0.54."""
        score = compute_direction_score(-0.4, "bearish", 0.6)
        assert score == pytest.approx(-0.54)

    def test_exact_weighted_blend_mixed(self):
        """Lexicon bullish, Claude bearish: 0.5 * 0.3 + (-1.0 * 0.5) * 0.7 = 0.15 - 0.35 = -0.2."""
        score = compute_direction_score(0.5, "bearish", 0.5)
        assert score == pytest.approx(-0.2)

    def test_zero_confidence_means_no_claude_contribution(self):
        """Zero confidence → Claude numeric is 0, only lexicon weighted portion."""
        score = compute_direction_score(0.6, "bullish", 0.0)
        assert score == pytest.approx(0.6 * 0.3)


# ===================================================================
# Claude unavailable scenarios (fallback to lexicon only)
# ===================================================================


class TestClaudeUnavailable:
    """When Claude result is None, fall back to raw lexicon polarity."""

    def test_none_direction_falls_back_to_lexicon(self):
        score = compute_direction_score(0.7, None, 0.8)
        assert score == pytest.approx(0.7)

    def test_none_confidence_falls_back_to_lexicon(self):
        score = compute_direction_score(0.7, "bullish", None)
        assert score == pytest.approx(0.7)

    def test_both_none_falls_back_to_lexicon(self):
        score = compute_direction_score(0.7, None, None)
        assert score == pytest.approx(0.7)

    def test_fallback_negative_lexicon(self):
        score = compute_direction_score(-0.5, None, None)
        assert score == pytest.approx(-0.5)

    def test_fallback_zero_lexicon(self):
        score = compute_direction_score(0.0, None, None)
        assert score == 0.0

    def test_fallback_no_dampening(self):
        """Fallback uses raw lexicon, NOT lexicon * weight."""
        score = compute_direction_score(0.9, None, None)
        assert score == pytest.approx(0.9)  # not 0.9 * 0.3


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    def test_all_zeros(self):
        score = compute_direction_score(0.0, "neutral", 0.0)
        assert score == 0.0

    def test_empty_inputs(self):
        score = compute_direction_score(0.0, None, None)
        assert score == 0.0

    def test_max_positive_clamped(self):
        """Lexicon +1.0, Claude bullish confidence 1.0 → clamped at 1.0."""
        score = compute_direction_score(1.0, "bullish", 1.0)
        # 1.0 * 0.3 + 1.0 * 0.7 = 1.0 — exactly 1.0
        assert score == pytest.approx(1.0)
        assert score <= 1.0

    def test_max_negative_clamped(self):
        """Lexicon -1.0, Claude bearish confidence 1.0 → clamped at -1.0."""
        score = compute_direction_score(-1.0, "bearish", 1.0)
        # -1.0 * 0.3 + -1.0 * 0.7 = -1.0
        assert score == pytest.approx(-1.0)
        assert score >= -1.0

    def test_clamp_prevents_overflow_positive(self):
        """Even with custom weights that could overflow, output is clamped."""
        heavy = DirectionWeights(lexicon_weight=1.0, claude_weight=1.0)
        score = compute_direction_score(1.0, "bullish", 1.0, weights=heavy)
        assert score == 1.0

    def test_clamp_prevents_overflow_negative(self):
        heavy = DirectionWeights(lexicon_weight=1.0, claude_weight=1.0)
        score = compute_direction_score(-1.0, "bearish", 1.0, weights=heavy)
        assert score == -1.0

    def test_unknown_direction_treated_as_neutral(self):
        """Unknown direction string maps to 0.0 via _DIRECTION_MAP.get default."""
        score = compute_direction_score(0.5, "sideways", 0.8)
        # 0.5 * 0.3 + 0.0 * 0.7 = 0.15
        assert score == pytest.approx(0.15)

    def test_neutral_claude_with_neutral_lexicon(self):
        score = compute_direction_score(0.0, "neutral", 1.0)
        assert score == 0.0


# ===================================================================
# Direction label tests
# ===================================================================


class TestDirectionLabel:
    """Verify direction_label derivation from score."""

    def test_positive_score_is_bullish(self):
        result = compute_direction_detailed(0.8, "bullish", 0.9)
        assert result["direction_label"] == "bullish"

    def test_negative_score_is_bearish(self):
        result = compute_direction_detailed(-0.8, "bearish", 0.9)
        assert result["direction_label"] == "bearish"

    def test_zero_score_is_neutral(self):
        result = compute_direction_detailed(0.0, "neutral", 0.0)
        assert result["direction_label"] == "neutral"

    def test_small_positive_in_dead_zone_is_neutral(self):
        """Score within ±0.05 dead zone → neutral."""
        result = compute_direction_detailed(0.1, "neutral", 0.0)
        # 0.1 * 0.3 + 0.0 = 0.03, which is < 0.05
        assert result["direction_score"] == pytest.approx(0.03)
        assert result["direction_label"] == "neutral"

    def test_small_negative_in_dead_zone_is_neutral(self):
        result = compute_direction_detailed(-0.1, "neutral", 0.0)
        # -0.1 * 0.3 = -0.03
        assert result["direction_score"] == pytest.approx(-0.03)
        assert result["direction_label"] == "neutral"

    def test_just_above_dead_zone_is_bullish(self):
        """Score just above 0.05 → bullish."""
        result = compute_direction_detailed(0.2, "neutral", 0.0)
        # 0.2 * 0.3 = 0.06 > 0.05
        assert result["direction_score"] == pytest.approx(0.06)
        assert result["direction_label"] == "bullish"

    def test_just_below_negative_dead_zone_is_bearish(self):
        result = compute_direction_detailed(-0.2, "neutral", 0.0)
        # -0.2 * 0.3 = -0.06 < -0.05
        assert result["direction_score"] == pytest.approx(-0.06)
        assert result["direction_label"] == "bearish"


# ===================================================================
# Custom weights
# ===================================================================


class TestCustomWeights:
    """Overriding weights changes output proportionally."""

    def test_equal_weights(self):
        """50/50 blend."""
        w = DirectionWeights(lexicon_weight=0.5, claude_weight=0.5)
        score = compute_direction_score(0.6, "bullish", 0.8, weights=w)
        # 0.6 * 0.5 + (1.0 * 0.8) * 0.5 = 0.3 + 0.4 = 0.7
        assert score == pytest.approx(0.7)

    def test_lexicon_only_weights(self):
        """All weight on lexicon."""
        w = DirectionWeights(lexicon_weight=1.0, claude_weight=0.0)
        score = compute_direction_score(0.6, "bullish", 0.8, weights=w)
        assert score == pytest.approx(0.6)

    def test_claude_only_weights(self):
        """All weight on Claude."""
        w = DirectionWeights(lexicon_weight=0.0, claude_weight=1.0)
        score = compute_direction_score(0.6, "bullish", 0.8, weights=w)
        # 0.0 + (1.0 * 0.8) * 1.0 = 0.8
        assert score == pytest.approx(0.8)

    def test_custom_weights_change_output(self):
        """Different weights produce different results."""
        default_score = compute_direction_score(0.5, "bullish", 0.7)
        custom_score = compute_direction_score(
            0.5, "bullish", 0.7, weights=DirectionWeights(0.5, 0.5)
        )
        assert default_score != custom_score


# ===================================================================
# Detailed output
# ===================================================================


class TestDetailedOutput:
    """Tests for compute_direction_detailed."""

    def test_all_keys_present(self):
        result = compute_direction_detailed(0.5, "bullish", 0.8)
        expected_keys = {
            "direction_score",
            "direction_label",
            "lexicon_polarity",
            "lexicon_contribution",
            "claude_direction",
            "claude_confidence",
            "claude_numeric",
            "claude_contribution",
            "weights_used",
            "claude_available",
        }
        assert set(result.keys()) == expected_keys

    def test_contributions_sum_to_score_when_claude_available(self):
        result = compute_direction_detailed(0.5, "bullish", 0.8)
        expected = result["lexicon_contribution"] + result["claude_contribution"]
        assert result["direction_score"] == pytest.approx(expected)

    def test_claude_available_flag_true(self):
        result = compute_direction_detailed(0.5, "bullish", 0.8)
        assert result["claude_available"] is True

    def test_claude_available_flag_false_none_direction(self):
        result = compute_direction_detailed(0.5, None, 0.8)
        assert result["claude_available"] is False

    def test_claude_available_flag_false_none_confidence(self):
        result = compute_direction_detailed(0.5, "bullish", None)
        assert result["claude_available"] is False

    def test_weights_used_matches_defaults(self):
        result = compute_direction_detailed(0.5, "bullish", 0.8)
        assert result["weights_used"] == {"lexicon": 0.3, "claude": 0.7}

    def test_weights_used_matches_custom(self):
        w = DirectionWeights(lexicon_weight=0.4, claude_weight=0.6)
        result = compute_direction_detailed(0.5, "bullish", 0.8, weights=w)
        assert result["weights_used"] == {"lexicon": 0.4, "claude": 0.6}

    def test_claude_numeric_bullish(self):
        result = compute_direction_detailed(0.0, "bullish", 0.7)
        assert result["claude_numeric"] == pytest.approx(0.7)

    def test_claude_numeric_bearish(self):
        result = compute_direction_detailed(0.0, "bearish", 0.7)
        assert result["claude_numeric"] == pytest.approx(-0.7)

    def test_claude_numeric_neutral(self):
        result = compute_direction_detailed(0.0, "neutral", 0.7)
        assert result["claude_numeric"] == pytest.approx(0.0)

    def test_claude_numeric_when_unavailable(self):
        result = compute_direction_detailed(0.5, None, None)
        assert result["claude_numeric"] == 0.0

    def test_lexicon_contribution_when_claude_available(self):
        result = compute_direction_detailed(0.5, "bullish", 0.8)
        assert result["lexicon_contribution"] == pytest.approx(0.5 * 0.3)

    def test_lexicon_contribution_when_claude_unavailable(self):
        """Fallback: lexicon contribution equals raw lexicon polarity."""
        result = compute_direction_detailed(0.5, None, None)
        assert result["lexicon_contribution"] == pytest.approx(0.5)

    def test_claude_contribution_when_unavailable(self):
        result = compute_direction_detailed(0.5, None, None)
        assert result["claude_contribution"] == 0.0

    def test_passthrough_fields(self):
        result = compute_direction_detailed(0.5, "bearish", 0.6)
        assert result["lexicon_polarity"] == 0.5
        assert result["claude_direction"] == "bearish"
        assert result["claude_confidence"] == 0.6


# ===================================================================
# Default weights validation
# ===================================================================


class TestDefaultWeights:
    """Verify default weight constants."""

    def test_default_weights_sum_to_one(self):
        assert DEFAULT_WEIGHTS.lexicon_weight + DEFAULT_WEIGHTS.claude_weight == pytest.approx(1.0)

    def test_default_lexicon_weight(self):
        assert DEFAULT_WEIGHTS.lexicon_weight == 0.3

    def test_default_claude_weight(self):
        assert DEFAULT_WEIGHTS.claude_weight == 0.7

    def test_weights_are_frozen(self):
        with pytest.raises(AttributeError):
            DEFAULT_WEIGHTS.lexicon_weight = 0.5  # type: ignore[misc]
