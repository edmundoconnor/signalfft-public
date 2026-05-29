"""Wave model -- short-window signal density bursts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Wave:
    """A detected wave (burst of signal density) for an entity.

    Components keys: density, acceleration, coherence, spread.
    """

    wave_id: str
    entity_id: str
    window_end: str  # ISO 8601
    strength: float
    components: dict = field(default_factory=dict)
    signal_count: int = 0
    ttl: int = 0  # epoch seconds
