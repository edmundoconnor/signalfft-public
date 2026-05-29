"""AttentionField model -- dynamic weight modifiers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AttentionField:
    """A snapshot of the attention field that modulates signal weights."""

    field_id: str
    timestamp: str  # ISO 8601
    modifier_vector: dict = field(default_factory=dict)  # per-weight-dimension modifier
    temperature: float = 0.0
    narrative_field_strength: float = 0.0
    version: str = ""
