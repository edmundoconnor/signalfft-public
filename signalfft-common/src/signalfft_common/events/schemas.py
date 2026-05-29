"""Event schemas for inter-service messaging via SQS and EventBridge."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, field_validator


class BaseEvent(BaseModel):
    """Base class for all SignalFFT inter-service events."""

    event_type: str
    timestamp: str  # ISO 8601
    source: str  # service that emitted the event
    trace_id: str  # UUID for distributed tracing
    payload: dict[str, Any]

    def to_sqs_message(self) -> str:
        """JSON serialized string for SQS."""
        return self.model_dump_json()

    @classmethod
    def from_sqs_message(cls, body: str) -> BaseEvent:
        """Deserialize and validate, return correct subclass based on event_type."""
        data = json.loads(body)
        event_type = data.get("event_type")
        subclass = EVENT_TYPE_REGISTRY.get(event_type)
        if subclass is None:
            raise ValueError(f"Unknown event_type: {event_type}")
        return subclass.model_validate(data)

    def to_eventbridge_entry(self, bus_name: str) -> dict:
        """Formatted for EventBridge PutEvents."""
        return {
            "Source": f"signalfft.{self.source}",
            "DetailType": self.event_type,
            "Detail": self.model_dump_json(),
            "EventBusName": bus_name,
        }


class RawEventCollected(BaseEvent):
    """Emitted when a raw event is ingested from an external source."""

    event_type: Literal["RAW_EVENT_COLLECTED"] = "RAW_EVENT_COLLECTED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"event_id", "entity_id", "source", "content_hash", "raw_artifact_s3"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class FeatureExtracted(BaseEvent):
    """Emitted when a feature is extracted from a raw event."""

    event_type: Literal["FEATURE_EXTRACTED"] = "FEATURE_EXTRACTED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"feature_id", "event_id", "entity_id", "feature_type"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class SignalScored(BaseEvent):
    """Emitted when a signal score is computed for an entity."""

    event_type: Literal["SIGNAL_SCORED"] = "SIGNAL_SCORED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"signal_id", "entity_id", "score", "weight_version", "attention_field_version"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class WaveComputed(BaseEvent):
    """Emitted when a wave is computed from aggregated signals."""

    event_type: Literal["WAVE_COMPUTED"] = "WAVE_COMPUTED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"wave_id", "entity_id", "strength", "signal_count"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class NarrativeUpdated(BaseEvent):
    """Emitted when a narrative lifecycle state changes."""

    event_type: Literal["NARRATIVE_UPDATED"] = "NARRATIVE_UPDATED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"narrative_id", "lifecycle_state", "gravity_score"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class AttentionFieldUpdated(BaseEvent):
    """Emitted when the attention field is recalculated."""

    event_type: Literal["ATTENTION_FIELD_UPDATED"] = "ATTENTION_FIELD_UPDATED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"field_id", "version", "temperature"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class TradeCandidateGenerated(BaseEvent):
    """Emitted when a trade candidate passes risk checks."""

    event_type: Literal["TRADE_CANDIDATE_GENERATED"] = "TRADE_CANDIDATE_GENERATED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"candidate_id", "signal_id", "entity_id", "score", "risk_status"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class FilingDocumentRequested(BaseEvent):
    """Emitted when a filing document needs to be fetched from SEC."""

    event_type: Literal["FILING_DOCUMENT_REQUESTED"] = "FILING_DOCUMENT_REQUESTED"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"event_id", "entity_id", "filing_url", "form_type", "filing_date", "cik"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class FilingDocumentReady(BaseEvent):
    """Emitted when a filing document has been fetched and stored in S3."""

    event_type: Literal["FILING_DOCUMENT_READY"] = "FILING_DOCUMENT_READY"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {"event_id", "entity_id", "filing_s3_uri", "form_type", "filing_date", "cik"}
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class FilingSectionsReady(BaseEvent):
    """Emitted when filing sections have been extracted and stored in S3."""

    event_type: Literal["FILING_SECTIONS_READY"] = "FILING_SECTIONS_READY"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {
            "event_id", "entity_id", "cik", "form_type", "filing_date",
            "sections_available", "section_s3_prefix", "total_text_length",
        }
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class FilingPairReady(BaseEvent):
    """Emitted when a filing pair (current vs prior same form type) is indexed."""

    event_type: Literal["FILING_PAIR_READY"] = "FILING_PAIR_READY"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {
            "entity_id", "form_type", "current_filing_date", "prior_filing_date",
            "current_s3_prefix", "prior_s3_prefix", "pair_id",
        }
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class FilingChainReady(BaseEvent):
    """Emitted when a filing chain (chronological history per entity/form) is built."""

    event_type: Literal["FILING_CHAIN_READY"] = "FILING_CHAIN_READY"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {
            "entity_id", "form_type", "chain_length", "latest_filing_date",
            "filing_dates", "chain_id",
        }
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class HighPriorityFiling(BaseEvent):
    """Emitted when keyword triage flags a filing as high priority."""

    event_type: Literal["HIGH_PRIORITY_FILING"] = "HIGH_PRIORITY_FILING"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {
            "event_id", "entity_id", "priority_level",
            "matched_categories", "matched_terms",
        }
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class TriageComplete(BaseEvent):
    """Emitted when Edge 1 quiet filing triage completes for a filing."""

    event_type: Literal["TRIAGE_COMPLETE"] = "TRIAGE_COMPLETE"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {
            "entity_id", "event_id", "materiality_score",
            "attention_likelihood", "direction", "is_quiet_filing",
            "boost_multiplier", "suggested_urgency",
        }
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


class DeltaAnalysisComplete(BaseEvent):
    """Emitted when Edge 2 semantic delta analysis completes for a filing pair."""

    event_type: Literal["DELTA_ANALYSIS_COMPLETE"] = "DELTA_ANALYSIS_COMPLETE"

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, v: dict) -> dict:
        required = {
            "entity_id", "pair_id", "current_filing_date", "prior_filing_date",
            "form_type", "sections_analyzed", "shift_count", "composite_score",
            "dominant_direction",
        }
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required payload fields: {missing}")
        return v


EVENT_TYPE_REGISTRY: dict[str, type[BaseEvent]] = {
    "RAW_EVENT_COLLECTED": RawEventCollected,
    "FEATURE_EXTRACTED": FeatureExtracted,
    "SIGNAL_SCORED": SignalScored,
    "WAVE_COMPUTED": WaveComputed,
    "NARRATIVE_UPDATED": NarrativeUpdated,
    "ATTENTION_FIELD_UPDATED": AttentionFieldUpdated,
    "TRADE_CANDIDATE_GENERATED": TradeCandidateGenerated,
    "FILING_DOCUMENT_REQUESTED": FilingDocumentRequested,
    "FILING_DOCUMENT_READY": FilingDocumentReady,
    "FILING_SECTIONS_READY": FilingSectionsReady,
    "FILING_PAIR_READY": FilingPairReady,
    "FILING_CHAIN_READY": FilingChainReady,
    "HIGH_PRIORITY_FILING": HighPriorityFiling,
    "TRIAGE_COMPLETE": TriageComplete,
    "DELTA_ANALYSIS_COMPLETE": DeltaAnalysisComplete,
}
