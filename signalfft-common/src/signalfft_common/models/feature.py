"""Feature model -- extracted features from events."""

from __future__ import annotations

from dataclasses import dataclass, field

from signalfft_common.enums import FeatureType


@dataclass(slots=True)
class Feature:
    """A feature extracted from an event (entity mention, sentiment, etc.)."""

    feature_id: str
    event_id: str
    entity_id: str
    feature_type: FeatureType
    value: dict = field(default_factory=dict)
    created_at: str = ""  # ISO 8601
