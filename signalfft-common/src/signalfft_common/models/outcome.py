"""Outcome model -- post-execution fill and latency records."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Outcome:
    """Execution outcome for a trade candidate."""

    outcome_id: str
    signal_id: str
    candidate_id: str
    fill_price: float | None = field(default=None)
    latency_ms: int | None = field(default=None)
    slippage: float | None = field(default=None)
    created_at: str = ""  # ISO 8601
