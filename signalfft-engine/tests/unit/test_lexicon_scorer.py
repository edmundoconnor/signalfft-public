"""Tests for the lexicon-based polarity scorer."""

from __future__ import annotations

from engine.directional.lexicon_scorer import (
    NEGATIVE_TERMS,
    POSITIVE_TERMS,
    score_polarity,
    score_polarity_detailed,
)


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self):
        """Identical input must always produce identical output."""
        text = "The company reported growth and raised guidance."
        scores = [score_polarity(text) for _ in range(100)]
        assert all(s == scores[0] for s in scores)

    def test_no_side_effects(self):
        """Calling the function should not change global state."""
        pos_before = frozenset(POSITIVE_TERMS)
        neg_before = frozenset(NEGATIVE_TERMS)
        score_polarity("growth decline loss beat")
        assert POSITIVE_TERMS == pos_before
        assert NEGATIVE_TERMS == neg_before


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_empty_string(self):
        assert score_polarity("") == 0.0

    def test_whitespace_only(self):
        assert score_polarity("   \n\t  ") == 0.0

    def test_no_financial_terms(self):
        """Text with no matching terms should return 0.0."""
        assert score_polarity("The quick brown fox jumps over the lazy dog.") == 0.0

    def test_all_positive(self):
        """All positive terms should produce a score approaching +1.0."""
        text = " ".join(sorted(t for t in POSITIVE_TERMS if " " not in t))
        score = score_polarity(text)
        assert score > 0.8

    def test_all_negative(self):
        """All negative terms should produce a score approaching -1.0."""
        text = " ".join(sorted(t for t in NEGATIVE_TERMS if " " not in t))
        score = score_polarity(text)
        assert score < -0.8

    def test_equal_positive_negative(self):
        """Equal counts of positive and negative terms should return 0.0."""
        text = "growth decline"
        assert score_polarity(text) == 0.0

    def test_single_positive(self):
        """1 positive, 0 negative -> 1/(1+0+1) = 0.5."""
        assert score_polarity("growth") == 0.5

    def test_single_negative(self):
        """0 positive, 1 negative -> -1/(0+1+1) = -0.5."""
        assert score_polarity("decline") == -0.5


# ---------------------------------------------------------------------------
# Phrase matching tests
# ---------------------------------------------------------------------------


class TestPhraseMatching:
    def test_multiword_positive_match(self):
        """Multi-word phrase 'record revenue' should match as a single term."""
        result = score_polarity_detailed("The company achieved record revenue this quarter.")
        assert "record revenue" in result["positive_matches"]
        assert result["positive_count"] == 1

    def test_multiword_negative_match(self):
        """Multi-word phrase 'going concern' should match as a single term."""
        result = score_polarity_detailed("The auditor flagged a going concern warning.")
        assert "going concern" in result["negative_matches"]
        assert result["negative_count"] == 1

    def test_going_alone_not_negative(self):
        """The word 'going' alone should not trigger a negative match."""
        result = score_polarity_detailed("We are going to the store.")
        assert result["negative_count"] == 0

    def test_word_boundary_approved(self):
        """'approved' should match but 'unapproved' and 'disapproved' should not."""
        assert score_polarity("The drug was approved.") > 0.0
        assert score_polarity("The drug was unapproved.") == 0.0
        assert score_polarity("The plan was disapproved.") == 0.0

    def test_word_boundary_growth(self):
        """'growth' should not match inside 'degrowth' or 'outgrowth'."""
        result = score_polarity_detailed("growth")
        assert result["positive_count"] == 1
        # These should NOT match 'growth' due to word boundaries
        result2 = score_polarity_detailed("degrowth")
        assert result2["positive_count"] == 0

    def test_case_insensitive(self):
        """Matching should be case insensitive."""
        assert score_polarity("GROWTH") == score_polarity("growth")
        assert score_polarity("Growth") == score_polarity("growth")
        assert score_polarity("GOING CONCERN") == score_polarity("going concern")

    def test_punctuation_adjacent(self):
        """Terms adjacent to punctuation should still match."""
        result = score_polarity_detailed("growth, decline. loss!")
        assert result["positive_count"] == 1
        assert result["negative_count"] == 2

    def test_debt_default_vs_default(self):
        """'debt default' should match as a single phrase; standalone 'default' also matches."""
        result = score_polarity_detailed("The company is in debt default.")
        assert "debt default" in result["negative_matches"]

        result2 = score_polarity_detailed("The loan is in default.")
        assert "default" in result2["negative_matches"]


# ---------------------------------------------------------------------------
# Real-world scenario tests
# ---------------------------------------------------------------------------


class TestRealWorldScenarios:
    def test_bullish_excerpt(self):
        """Bullish filing excerpt should produce a positive score."""
        text = (
            "The company beat analyst expectations for the third consecutive quarter. "
            "Revenue growth accelerated to 25% year-over-year driven by strong demand "
            "for our cloud platform. Management raised full-year guidance and announced "
            "a new share buyback program. Margin expansion continued with cost savings "
            "from operational efficiency gains."
        )
        score = score_polarity(text)
        assert score > 0.5

    def test_bearish_excerpt(self):
        """Bearish filing excerpt should produce a negative score."""
        text = (
            "The company recorded a significant impairment charge of $2.3 billion "
            "related to the restructuring of its consumer division. Ongoing litigation "
            "with regulatory bodies and a recent downgrade by credit agencies have "
            "raised going concern warnings. The investigation into accounting practices "
            "revealed a material weakness in internal controls, and guidance was reduced "
            "for the remainder of the fiscal year."
        )
        score = score_polarity(text)
        assert score < -0.5

    def test_mixed_excerpt(self):
        """Mixed filing should produce a score reflecting the balance."""
        text = (
            "Revenue growth was strong at 15% year-over-year, and the company "
            "exceeded expectations on earnings. However, management disclosed "
            "a restructuring plan that will result in a significant writedown. "
            "Litigation costs also contributed to margin compression."
        )
        result = score_polarity_detailed(text)
        assert result["positive_count"] > 0
        assert result["negative_count"] > 0
        # The score should reflect the balance, not be extreme
        assert -0.8 < result["polarity_score"] < 0.8


# ---------------------------------------------------------------------------
# Score range tests
# ---------------------------------------------------------------------------


class TestScoreRange:
    def test_always_in_range(self):
        """Output must always be in [-1.0, +1.0] regardless of input."""
        test_cases = [
            "",
            "growth",
            "decline",
            "growth " * 1000,
            "decline " * 1000,
            "growth decline " * 500,
            " ".join(sorted(POSITIVE_TERMS)),
            " ".join(sorted(NEGATIVE_TERMS)),
            " ".join(sorted(POSITIVE_TERMS | NEGATIVE_TERMS)),
        ]
        for text in test_cases:
            score = score_polarity(text)
            assert -1.0 <= score <= 1.0, f"Score {score} out of range for: {text[:50]}..."

    def test_more_keywords_stronger_signal(self):
        """More keyword hits should produce a stronger (more extreme) score."""
        score_1 = score_polarity("growth")
        score_3 = score_polarity("growth beat exceeded")
        assert score_3 > score_1

    def test_formula_correctness(self):
        """Verify the formula: (pos - neg) / (pos + neg + 1)."""
        # 2 positive, 1 negative -> (2-1)/(2+1+1) = 0.25
        result = score_polarity_detailed("growth beat decline")
        assert result["positive_count"] == 2
        assert result["negative_count"] == 1
        assert result["polarity_score"] == 0.25


# ---------------------------------------------------------------------------
# Detailed function tests
# ---------------------------------------------------------------------------


class TestDetailed:
    def test_returns_all_keys(self):
        """Detailed result should have all expected keys."""
        result = score_polarity_detailed("growth")
        expected_keys = {
            "polarity_score", "positive_count", "negative_count",
            "positive_matches", "negative_matches", "total_words",
        }
        assert set(result.keys()) == expected_keys

    def test_correct_match_lists(self):
        """Match lists should contain the actual terms found."""
        result = score_polarity_detailed("We saw growth and strong demand but also a decline.")
        assert "growth" in result["positive_matches"]
        assert "strong demand" in result["positive_matches"]
        assert "decline" in result["negative_matches"]

    def test_counts_are_accurate(self):
        """Counts should match the length of match lists."""
        result = score_polarity_detailed("growth beat exceeded decline loss")
        assert result["positive_count"] == len(result["positive_matches"])
        assert result["negative_count"] == len(result["negative_matches"])
        assert result["positive_count"] == 3
        assert result["negative_count"] == 2

    def test_total_words(self):
        """total_words should reflect whitespace-split word count."""
        result = score_polarity_detailed("one two three four five")
        assert result["total_words"] == 5

    def test_empty_returns_zeros(self):
        """Empty input should return zeroed-out result."""
        result = score_polarity_detailed("")
        assert result["polarity_score"] == 0.0
        assert result["positive_count"] == 0
        assert result["negative_count"] == 0
        assert result["positive_matches"] == []
        assert result["negative_matches"] == []
        assert result["total_words"] == 0

    def test_matches_are_lowercase(self):
        """Returned matches should be lowercased."""
        result = score_polarity_detailed("GROWTH DECLINE")
        assert result["positive_matches"] == ["growth"]
        assert result["negative_matches"] == ["decline"]

    def test_duplicate_matches_counted(self):
        """Multiple occurrences of the same term should each be counted."""
        result = score_polarity_detailed("growth and more growth and even more growth")
        assert result["positive_count"] == 3
        assert result["positive_matches"].count("growth") == 3

    def test_score_consistent_with_simple(self):
        """Detailed polarity_score should match simple score_polarity output."""
        text = "growth beat decline restructuring strong demand"
        simple = score_polarity(text)
        detailed = score_polarity_detailed(text)
        assert simple == detailed["polarity_score"]
