"""SignalFFT enumerations."""

from enum import Enum


class NarrativeState(Enum):
    """Lifecycle states for a narrative arc."""

    EMERGING = "EMERGING"
    ACCELERATING = "ACCELERATING"
    DOMINANT = "DOMINANT"
    SATURATED = "SATURATED"
    DECAYING = "DECAYING"


class SignalType(Enum):
    """Classification of inbound signal sources."""

    SEC_FILING = "SEC_FILING"
    NEWS_ARTICLE = "NEWS_ARTICLE"
    SOCIAL_POST = "SOCIAL_POST"
    ANALYST_REPORT = "ANALYST_REPORT"


class FeatureType(Enum):
    """Types of features extracted from events."""

    ENTITY_MENTION = "ENTITY_MENTION"
    SENTIMENT = "SENTIMENT"
    TEMPORAL_MARKER = "TEMPORAL_MARKER"
    SOURCE_TYPE = "SOURCE_TYPE"
    TRIAGE = "TRIAGE"


class EdgeType(Enum):
    """Relationship types in the knowledge graph."""

    ENTITY_HAS_EVENT = "ENTITY_HAS_EVENT"
    SIGNAL_ASSOCIATED_WITH_OUTCOME = "SIGNAL_ASSOCIATED_WITH_OUTCOME"
    SIGNAL_PART_OF_WAVE = "SIGNAL_PART_OF_WAVE"
    ENTITY_CAPTURED_BY_NARRATIVE = "ENTITY_CAPTURED_BY_NARRATIVE"


class RiskStatus(Enum):
    """Risk gate decision for a trade candidate."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
