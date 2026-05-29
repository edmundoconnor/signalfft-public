"""TradeCandidate model -- signals that pass the threshold for trading."""

from __future__ import annotations

from dataclasses import dataclass, field

from signalfft_common.enums import RiskStatus


@dataclass(slots=True)
class TradeCandidate:
    """A trade candidate produced by the signal engine."""

    candidate_id: str
    signal_id: str
    entity_id: str
    score: float
    risk_status: RiskStatus
    risk_rejection_reason: str | None = field(default=None)
    signal_model_version: str = ""
    attention_field_version: str = ""
    opus_config_version: str = ""
    engine_container_sha: str = ""
    created_at: str = ""  # ISO 8601
    direction: str = ""
    direction_score: float = 0.0
