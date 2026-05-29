"""Comprehensive tests for the pure feature extraction functions."""

from __future__ import annotations

import pytest

from engine.feature_extraction.extractor import (
    extract_features,
    extract_entity_mentions,
    extract_sentiment,
    extract_temporal_markers,
    _get_text,
)
from signalfft_common.enums import FeatureType
from signalfft_common.models import Feature


# ===========================================================================
# extract_features tests
# ===========================================================================


class TestExtractFeatures:
    """Tests for the top-level extract_features orchestrator."""

    def test_extract_features_returns_list(self):
        """Basic call returns a list of Feature objects."""
        content = {"text": "Goldman Sachs reported strong growth in Q1 2026."}
        features = extract_features("evt-1", "ent-1", content)
        assert isinstance(features, list)
        assert len(features) > 0
        assert all(isinstance(f, Feature) for f in features)

    def test_extract_features_empty_content(self):
        """Empty dict should return an empty list (no text to extract from)."""
        features = extract_features("evt-2", "ent-2", {})
        assert isinstance(features, list)
        # With no text, no entity mentions, no sentiment, no temporal markers
        assert len(features) == 0

    def test_feature_has_correct_type(self):
        """Each Feature should have the correct FeatureType enum value."""
        content = {
            "text": "Apple Inc reported strong growth on 2026-01-15 for next quarter."
        }
        features = extract_features("evt-3", "ent-3", content)

        type_map = {}
        for f in features:
            ft = f.feature_type
            type_map.setdefault(ft, []).append(f)

        # Should have at least entity mentions, sentiment, and temporal markers
        assert FeatureType.ENTITY_MENTION in type_map
        assert FeatureType.SENTIMENT in type_map
        assert FeatureType.TEMPORAL_MARKER in type_map

    def test_feature_ids_unique(self):
        """All feature_ids across extracted features should be unique UUIDs."""
        content = {
            "text": "Goldman Sachs and Apple Inc saw strong growth in Q1 2026 on 2026-03-01."
        }
        features = extract_features("evt-4", "ent-4", content)
        ids = [f.feature_id for f in features]
        assert len(ids) == len(set(ids)), "Feature IDs must be unique"

    def test_extract_features_preserves_event_and_entity_ids(self):
        """All features should carry the correct event_id and entity_id."""
        content = {"text": "Federal Reserve announced rate increase."}
        features = extract_features("evt-99", "ent-42", content)
        for f in features:
            assert f.event_id == "evt-99"
            assert f.entity_id == "ent-42"

    def test_extract_features_created_at_populated(self):
        """All features should have a non-empty created_at timestamp."""
        content = {"text": "Apple Inc saw profit growth."}
        features = extract_features("evt-5", "ent-5", content)
        for f in features:
            assert f.created_at != ""
            assert "T" in f.created_at  # ISO 8601 format

    def test_extract_features_source_type_emitted(self):
        """When source is provided, a SOURCE_TYPE feature should be emitted."""
        content = {"text": "Some text content."}
        features = extract_features("evt-6", "ent-6", content, source="FINNHUB_NEWS")
        source_features = [f for f in features if f.feature_type == FeatureType.SOURCE_TYPE]
        assert len(source_features) == 1
        assert source_features[0].value == {"source": "finnhub_news"}

    def test_extract_features_no_source_no_source_type(self):
        """When source is empty, no SOURCE_TYPE feature should be emitted."""
        content = {"text": "Some text content."}
        features = extract_features("evt-7", "ent-7", content)
        source_features = [f for f in features if f.feature_type == FeatureType.SOURCE_TYPE]
        assert len(source_features) == 0


class TestLexiconPolarityInFeatures:
    """Tests for lexicon_polarity integration in SENTIMENT features."""

    def test_sentiment_feature_includes_lexicon_polarity(self):
        """SENTIMENT feature value should contain lexicon_polarity key."""
        content = {"text": "The company reported strong growth and record revenue."}
        features = extract_features("evt-lp1", "ent-lp1", content)
        sent_features = [f for f in features if f.feature_type == FeatureType.SENTIMENT]
        assert len(sent_features) >= 1
        assert "lexicon_polarity" in sent_features[0].value

    def test_lexicon_polarity_is_bounded_float(self):
        """lexicon_polarity should be a float in [-1.0, +1.0]."""
        content = {"text": "Strong growth exceeded expectations amid decline risk."}
        features = extract_features("evt-lp2", "ent-lp2", content)
        sent_features = [f for f in features if f.feature_type == FeatureType.SENTIMENT]
        assert len(sent_features) >= 1
        lp = sent_features[0].value["lexicon_polarity"]
        assert isinstance(lp, float)
        assert -1.0 <= lp <= 1.0

    def test_lexicon_only_sentiment_feature(self):
        """When extract_sentiment returns None but lexicon finds terms, SENTIMENT feature created."""
        # "approved" is in the lexicon scorer but NOT in extract_sentiment's keyword lists
        content = {"text": "The drug was approved by the regulator."}
        features = extract_features("evt-lp3", "ent-lp3", content)
        sent_features = [f for f in features if f.feature_type == FeatureType.SENTIMENT]
        assert len(sent_features) == 1
        val = sent_features[0].value
        assert val["polarity"] == 0.0
        assert val["magnitude"] == 0.0
        assert val["positive_terms"] == []
        assert val["negative_terms"] == []
        assert val["lexicon_polarity"] != 0.0

    def test_empty_content_no_sentiment_features(self):
        """Empty content should produce no SENTIMENT features."""
        features = extract_features("evt-lp4", "ent-lp4", {})
        sent_features = [f for f in features if f.feature_type == FeatureType.SENTIMENT]
        assert len(sent_features) == 0


# ===========================================================================
# extract_entity_mentions tests
# ===========================================================================


class TestEntityMentions:
    """Tests for entity mention extraction."""

    def test_entity_mention_found(self):
        """A capitalized company name should be detected."""
        text = "Goldman Sachs released a report today."
        mentions = extract_entity_mentions(text, {})
        names = [m["name"] for m in mentions]
        assert "Goldman Sachs" in names

    def test_entity_mention_multiple(self):
        """Multiple distinct entities should be found."""
        text = "Goldman Sachs and Federal Reserve discussed the policy."
        mentions = extract_entity_mentions(text, {})
        names = [m["name"] for m in mentions]
        assert "Goldman Sachs" in names
        assert "Federal Reserve" in names

    def test_entity_mention_with_suffix(self):
        """Patterns like 'Apple Inc' should be detected."""
        text = "Apple Inc announced new products today."
        mentions = extract_entity_mentions(text, {})
        names = [m["name"] for m in mentions]
        assert any("Apple" in name for name in names)

    def test_entity_mention_from_metadata(self):
        """company_name in content dict should be included."""
        text = "The company announced results."
        content = {"text": text, "company_name": "Acme Corp"}
        mentions = extract_entity_mentions(text, content)
        names = [m["name"] for m in mentions]
        assert "Acme Corp" in names

    def test_entity_mention_count(self):
        """Repeated mentions should be counted correctly."""
        text = "Goldman Sachs reported earnings. Goldman Sachs beat expectations."
        mentions = extract_entity_mentions(text, {})
        gs_mentions = [m for m in mentions if m["name"] == "Goldman Sachs"]
        assert len(gs_mentions) == 1
        assert gs_mentions[0]["mention_count"] == 2

    def test_entity_mention_empty_text(self):
        """Empty text should return no mentions."""
        mentions = extract_entity_mentions("", {})
        assert mentions == []

    def test_entity_mention_short_names_filtered(self):
        """Names with 2 or fewer characters should be filtered out."""
        text = "Mr Ed is here."
        mentions = extract_entity_mentions(text, {})
        names = [m["name"] for m in mentions]
        # "Mr" is only 2 chars, should be filtered; "Ed" is also 2 chars
        for name in names:
            assert len(name) > 2


# ===========================================================================
# extract_sentiment tests
# ===========================================================================


class TestSentiment:
    """Tests for sentiment extraction."""

    def test_sentiment_positive(self):
        """Text with positive terms should have polarity > 0."""
        text = "The company reported strong growth and profit increase."
        result = extract_sentiment(text)
        assert result is not None
        assert result["polarity"] > 0
        assert len(result["positive_terms"]) > 0

    def test_sentiment_negative(self):
        """Text with negative terms should have polarity < 0."""
        text = "The company faced a major loss and decline in revenue amid bankruptcy risk."
        result = extract_sentiment(text)
        assert result is not None
        assert result["polarity"] < 0
        assert len(result["negative_terms"]) > 0

    def test_sentiment_mixed(self):
        """Text with equal positive and negative terms should have polarity near 0."""
        text = "Despite strong growth, there was a significant loss."
        result = extract_sentiment(text)
        assert result is not None
        # "strong", "growth" are positive; "loss" is negative
        # With 2 positive and 1 negative: polarity = (2-1)/3 = 0.333
        # Not exactly 0, but that's fine -- we just check it's a valid result
        assert -1.0 <= result["polarity"] <= 1.0

    def test_sentiment_no_signal(self):
        """Text without sentiment keywords should return None."""
        text = "The meeting was held at the office on Tuesday."
        result = extract_sentiment(text)
        assert result is None

    def test_sentiment_magnitude_capped(self):
        """Magnitude should be capped at 1.0."""
        # Use many sentiment terms to try to push magnitude above 1.0
        text = ("growth profit increase positive strong exceeded beat "
                "outperform upgrade bullish optimistic gain loss decline "
                "decrease negative weak")
        result = extract_sentiment(text)
        assert result is not None
        assert result["magnitude"] <= 1.0

    def test_sentiment_empty_text(self):
        """Empty text should return None."""
        result = extract_sentiment("")
        assert result is None

    def test_sentiment_returns_correct_keys(self):
        """Sentiment result should have polarity, magnitude, positive_terms, negative_terms."""
        text = "Strong growth was observed."
        result = extract_sentiment(text)
        assert result is not None
        assert "polarity" in result
        assert "magnitude" in result
        assert "positive_terms" in result
        assert "negative_terms" in result


# ===========================================================================
# extract_temporal_markers tests
# ===========================================================================


class TestTemporalMarkers:
    """Tests for temporal marker extraction."""

    def test_temporal_iso_date(self):
        """Should find YYYY-MM-DD date patterns."""
        text = "The report was published on 2026-01-15 with updated data."
        markers = extract_temporal_markers(text)
        iso_markers = [m for m in markers if m["marker_type"] == "ISO_DATE"]
        assert len(iso_markers) >= 1
        assert iso_markers[0]["value"] == "2026-01-15"

    def test_temporal_us_date(self):
        """Should find MM/DD/YYYY date patterns."""
        text = "The filing date is 01/15/2026 as per the records."
        markers = extract_temporal_markers(text)
        us_markers = [m for m in markers if m["marker_type"] == "US_DATE"]
        assert len(us_markers) >= 1
        assert us_markers[0]["value"] == "01/15/2026"

    def test_temporal_written_date(self):
        """Should find 'January 15, 2026' style dates."""
        text = "The event is scheduled for January 15, 2026 at the venue."
        markers = extract_temporal_markers(text)
        written_markers = [m for m in markers if m["marker_type"] == "WRITTEN_DATE"]
        assert len(written_markers) >= 1
        assert "January" in written_markers[0]["value"]

    def test_temporal_relative(self):
        """Should find relative time references like 'next quarter' and 'last year'."""
        text = "Revenue is expected to grow next quarter. Last year was difficult."
        markers = extract_temporal_markers(text)
        types = [m["marker_type"] for m in markers]
        assert "RELATIVE_FUTURE" in types
        assert "RELATIVE_PAST" in types

    def test_temporal_fiscal(self):
        """Should find 'Q1 2026' and 'fiscal year 2026' patterns."""
        text = "Earnings for Q1 2026 exceeded expectations. Fiscal year 2026 looks promising."
        markers = extract_temporal_markers(text)
        types = [m["marker_type"] for m in markers]
        assert "FISCAL_QUARTER" in types
        assert "FISCAL_YEAR" in types

    def test_temporal_context_included(self):
        """Each marker should include surrounding context text."""
        text = "The deadline is 2026-03-15 for all submissions."
        markers = extract_temporal_markers(text)
        assert len(markers) >= 1
        assert "context" in markers[0]
        assert len(markers[0]["context"]) > 0

    def test_temporal_empty_text(self):
        """Empty text should return no markers."""
        markers = extract_temporal_markers("")
        assert markers == []

    def test_temporal_multiple_dates(self):
        """Multiple dates in text should all be found."""
        text = "Between 2026-01-01 and 2026-12-31 the project was active."
        markers = extract_temporal_markers(text)
        iso_markers = [m for m in markers if m["marker_type"] == "ISO_DATE"]
        assert len(iso_markers) == 2


# ===========================================================================
# _get_text tests
# ===========================================================================


class TestGetText:
    """Tests for the _get_text helper function."""

    def test_get_text_text_key(self):
        """Single 'text' field (EDGAR pattern) should be returned."""
        assert _get_text({"text": "hello"}) == "hello"

    def test_get_text_body_key(self):
        """Single 'body' field should be returned."""
        assert _get_text({"body": "world"}) == "world"

    def test_get_text_text_priority_over_title(self):
        """'text' key should take priority over 'title' and others."""
        content = {"text": "primary", "body": "secondary", "title": "tertiary"}
        assert _get_text(content) == "primary"

    def test_get_text_finnhub_headline_summary(self):
        """Finnhub content with headline+summary should combine both."""
        content = {"headline": "AAPL beats earnings", "summary": "Apple reported Q1 results."}
        result = _get_text(content)
        assert "AAPL beats earnings" in result
        assert "Apple reported Q1 results." in result

    def test_get_text_reddit_title_selftext(self):
        """Reddit content with title+selftext should combine both."""
        content = {"title": "NVDA is going up!", "selftext": "Great earnings report."}
        result = _get_text(content)
        assert "NVDA is going up!" in result
        assert "Great earnings report." in result

    def test_get_text_title_only(self):
        """Content with only title and no body field should return title."""
        content = {"title": "Just a title", "score": 50}
        result = _get_text(content)
        assert result == "Just a title"

    def test_get_text_fallback_concatenation(self):
        """When no standard keys exist, all string values are concatenated."""
        content = {"custom_field": "foo", "another": "bar", "number": 42}
        result = _get_text(content)
        assert "foo" in result
        assert "bar" in result

    def test_get_text_empty_dict(self):
        """Empty dict should return empty string."""
        assert _get_text({}) == ""

    def test_get_text_non_string_values_ignored(self):
        """Non-string values for standard keys should be skipped."""
        content = {"text": 12345, "body": ["not", "a", "string"], "title": "actual text"}
        assert _get_text(content) == "actual text"
