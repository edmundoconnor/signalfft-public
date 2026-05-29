"""Signal and WeightConfig models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Signal:
    """A computed signal score for an entity at a point in time.

    Components keys: novelty, velocity, cross_source, semantic_impact,
    entity_sensitivity, historical_pattern, noise_penalty.
    """

    signal_id: str
    entity_id: str
    score: float
    event_id: str = ""
    components: dict = field(default_factory=dict)
    weight_version: str = ""
    attention_field_version: str = ""
    created_at: str = ""  # ISO 8601
    direction_score: float = 0.0


@dataclass(frozen=True, slots=True)
class WeightConfig:
    """Immutable weight vector for signal scoring."""

    wn: float  # Novelty
    wv: float  # Velocity
    wc: float  # CrossSourceCorrelation
    ws: float  # SemanticImpact
    we: float  # EntitySensitivity
    wh: float  # HistoricalPattern
    wp: float  # NoisePenalty
