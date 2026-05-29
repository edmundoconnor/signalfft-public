"""Pure feature extraction functions -- no I/O.

Extracts entity mentions, sentiment indicators, and temporal markers
from raw text content.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from signalfft_common.enums import FeatureType
from signalfft_common.models import Feature

from engine.directional.lexicon_scorer import score_polarity
from engine.feature_extraction.keyword_triage import triage_filing


def extract_features(
    event_id: str,
    entity_id: str,
    content: dict[str, Any],
    source: str = "",
) -> list[Feature]:
    """Extract all feature types from raw document content.

    Args:
        event_id: The event ID this content came from.
        entity_id: The entity this event is associated with.
        content: Raw document content dict (varies by source type).
        source: The collector source name (e.g. "EDGAR", "FINNHUB_NEWS", "REDDIT").

    Returns:
        List of Feature objects extracted from the content.
    """
    now = datetime.now(timezone.utc).isoformat()
    features: list[Feature] = []

    # Extract text from content dict
    text = _get_text(content)

    # Source type (enables cross-source scoring downstream)
    if source:
        features.append(Feature(
            feature_id=str(uuid.uuid4()),
            event_id=event_id,
            entity_id=entity_id,
            feature_type=FeatureType.SOURCE_TYPE,
            value={"source": source.lower()},
            created_at=now,
        ))

    # Entity mentions
    mentions = extract_entity_mentions(text, content)
    for mention in mentions:
        features.append(Feature(
            feature_id=str(uuid.uuid4()),
            event_id=event_id,
            entity_id=entity_id,
            feature_type=FeatureType.ENTITY_MENTION,
            value=mention,
            created_at=now,
        ))

    # Sentiment
    sentiment = extract_sentiment(text)
    lp = round(score_polarity(text), 4)
    if sentiment:
        sentiment["lexicon_polarity"] = lp
        features.append(Feature(
            feature_id=str(uuid.uuid4()),
            event_id=event_id,
            entity_id=entity_id,
            feature_type=FeatureType.SENTIMENT,
            value=sentiment,
            created_at=now,
        ))
    elif lp != 0.0:
        features.append(Feature(
            feature_id=str(uuid.uuid4()),
            event_id=event_id,
            entity_id=entity_id,
            feature_type=FeatureType.SENTIMENT,
            value={
                "polarity": 0.0,
                "magnitude": 0.0,
                "positive_terms": [],
                "negative_terms": [],
                "lexicon_polarity": lp,
            },
            created_at=now,
        ))

    # Temporal markers
    markers = extract_temporal_markers(text)
    for marker in markers:
        features.append(Feature(
            feature_id=str(uuid.uuid4()),
            event_id=event_id,
            entity_id=entity_id,
            feature_type=FeatureType.TEMPORAL_MARKER,
            value=marker,
            created_at=now,
        ))

    # Keyword triage
    triage = triage_filing(text)
    if triage.is_high_priority:
        features.append(Feature(
            feature_id=str(uuid.uuid4()),
            event_id=event_id,
            entity_id=entity_id,
            feature_type=FeatureType.TRIAGE,
            value={
                "priority_level": triage.priority_level,
                "matched_categories": triage.matched_categories,
                "matched_terms": triage.matched_terms,
                "category_count": triage.category_count,
            },
            created_at=now,
        ))

    return features


def _get_text(content: dict[str, Any]) -> str:
    """Extract text from content dict, trying common keys.

    Handles multiple content formats:
    - EDGAR: single "text" or "body" field with full filing text
    - Finnhub news: "headline" + "summary" fields
    - Reddit: "title" + "selftext" fields
    """
    # Single-field sources (e.g. EDGAR filings)
    for key in ("text", "body"):
        if key in content and isinstance(content[key], str) and content[key].strip():
            return content[key]

    # Multi-field sources: combine heading + body content
    parts = []
    for key in ("headline", "title"):
        val = content.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
            break
    for key in ("summary", "selftext", "description", "content"):
        val = content.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
            break
    if parts:
        return " ".join(parts)

    # Fallback: concatenate all string values
    all_parts = [str(v) for v in content.values() if isinstance(v, str)]
    return " ".join(all_parts)


def extract_entity_mentions(text: str, content: dict[str, Any]) -> list[dict]:
    """Extract entity mentions from text.

    Returns list of dicts with keys: name, mention_count.
    Uses simple NER-like heuristics (capitalized multi-word sequences).
    """
    if not text:
        return []

    # Find capitalized multi-word sequences (simple NER heuristic)
    # Matches sequences like "Apple Inc", "Goldman Sachs Group", "Federal Reserve"
    pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:Inc|Corp|Ltd|LLC|Co|Group|Bank|Fund)\.?)?)\b'
    matches = re.findall(pattern, text)

    # Also check for company names in content metadata
    company_name = content.get("company_name", "")
    if company_name and company_name not in matches:
        matches.append(company_name)

    # Deduplicate and count
    mention_counts: dict[str, int] = {}
    for match in matches:
        name = match.strip()
        if len(name) > 2:  # Filter very short matches
            mention_counts[name] = mention_counts.get(name, 0) + 1

    return [
        {"name": name, "mention_count": count}
        for name, count in mention_counts.items()
    ]


def extract_sentiment(text: str) -> dict | None:
    """Extract basic sentiment from text using keyword matching.

    Returns dict with keys: polarity (-1.0 to 1.0), magnitude (0.0 to 1.0),
    positive_terms, negative_terms. Returns None if no sentiment signal.
    """
    if not text:
        return None

    text_lower = text.lower()

    positive_terms = [
        "growth", "profit", "increase", "positive", "strong", "exceeded",
        "beat", "outperform", "upgrade", "bullish", "optimistic", "gain",
        "revenue growth", "record high", "above expectations",
    ]
    negative_terms = [
        "loss", "decline", "decrease", "negative", "weak", "missed",
        "below", "underperform", "downgrade", "bearish", "pessimistic",
        "risk", "lawsuit", "investigation", "default", "bankruptcy",
    ]

    found_positive = [t for t in positive_terms if t in text_lower]
    found_negative = [t for t in negative_terms if t in text_lower]

    total = len(found_positive) + len(found_negative)
    if total == 0:
        return None

    polarity = (len(found_positive) - len(found_negative)) / total
    magnitude = min(1.0, total / 10.0)

    return {
        "polarity": round(polarity, 3),
        "magnitude": round(magnitude, 3),
        "positive_terms": found_positive,
        "negative_terms": found_negative,
    }


def extract_temporal_markers(text: str) -> list[dict]:
    """Extract temporal references from text.

    Returns list of dicts with keys: marker_type, value, context.
    """
    if not text:
        return []

    markers: list[dict] = []

    # Date patterns (YYYY-MM-DD, MM/DD/YYYY, Month DD YYYY)
    date_patterns = [
        (r'\b(\d{4}-\d{2}-\d{2})\b', "ISO_DATE"),
        (r'\b(\d{1,2}/\d{1,2}/\d{4})\b', "US_DATE"),
        (r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b', "WRITTEN_DATE"),
    ]

    for pattern, marker_type in date_patterns:
        for match in re.finditer(pattern, text):
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            markers.append({
                "marker_type": marker_type,
                "value": match.group(1),
                "context": text[start:end].strip(),
            })

    # Relative time references
    relative_patterns = [
        (r'\b(next\s+(?:quarter|year|month|week))\b', "RELATIVE_FUTURE"),
        (r'\b(last\s+(?:quarter|year|month|week))\b', "RELATIVE_PAST"),
        (r'\b(fiscal\s+year\s+\d{4})\b', "FISCAL_YEAR"),
        (r'\b(Q[1-4]\s+\d{4})\b', "FISCAL_QUARTER"),
    ]

    for pattern, marker_type in relative_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            markers.append({
                "marker_type": marker_type,
                "value": match.group(1),
                "context": text[start:end].strip(),
            })

    return markers
