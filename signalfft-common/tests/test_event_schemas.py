"""Unit tests for SignalFFT event schemas (inter-service messaging)."""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from signalfft_common.events import (
    AttentionFieldUpdated,
    BaseEvent,
    EVENT_TYPE_REGISTRY,
    FeatureExtracted,
    FilingChainReady,
    FilingPairReady,
    FilingSectionsReady,
    NarrativeUpdated,
    RawEventCollected,
    SignalScored,
    TradeCandidateGenerated,
    WaveComputed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = "2026-02-15T12:00:00Z"
TRACE = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))
SOURCE = "test-service"


def _base_kwargs(**overrides) -> dict:
    """Return default kwargs shared by every event constructor."""
    defaults = {
        "timestamp": NOW,
        "source": SOURCE,
        "trace_id": TRACE,
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Fixtures -- one canonical instance per event type
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_event_collected() -> RawEventCollected:
    return RawEventCollected(
        **_base_kwargs(),
        payload={
            "event_id": "evt-001",
            "entity_id": "ent-001",
            "source": "SEC_EDGAR",
            "content_hash": "sha256:abc",
            "raw_artifact_s3": "s3://bucket/raw/evt-001.json",
        },
    )


@pytest.fixture
def feature_extracted() -> FeatureExtracted:
    return FeatureExtracted(
        **_base_kwargs(),
        payload={
            "feature_id": "feat-001",
            "event_id": "evt-001",
            "entity_id": "ent-001",
            "feature_type": "SENTIMENT",
        },
    )


@pytest.fixture
def signal_scored() -> SignalScored:
    return SignalScored(
        **_base_kwargs(),
        payload={
            "signal_id": "sig-001",
            "entity_id": "ent-001",
            "score": 0.87,
            "weight_version": "v1",
            "attention_field_version": "af-v1",
        },
    )


@pytest.fixture
def wave_computed() -> WaveComputed:
    return WaveComputed(
        **_base_kwargs(),
        payload={
            "wave_id": "wav-001",
            "entity_id": "ent-001",
            "strength": 0.92,
            "signal_count": 15,
        },
    )


@pytest.fixture
def narrative_updated() -> NarrativeUpdated:
    return NarrativeUpdated(
        **_base_kwargs(),
        payload={
            "narrative_id": "nar-001",
            "lifecycle_state": "EMERGING",
            "gravity_score": 0.75,
        },
    )


@pytest.fixture
def attention_field_updated() -> AttentionFieldUpdated:
    return AttentionFieldUpdated(
        **_base_kwargs(),
        payload={
            "field_id": "af-001",
            "version": "af-v1",
            "temperature": 0.5,
        },
    )


@pytest.fixture
def trade_candidate_generated() -> TradeCandidateGenerated:
    return TradeCandidateGenerated(
        **_base_kwargs(),
        payload={
            "candidate_id": "tc-001",
            "signal_id": "sig-001",
            "entity_id": "ent-001",
            "score": 0.87,
            "risk_status": "APPROVED",
        },
    )


@pytest.fixture
def filing_pair_ready() -> FilingPairReady:
    return FilingPairReady(
        **_base_kwargs(),
        payload={
            "entity_id": "AAPL",
            "form_type": "10-K",
            "current_filing_date": "2026-02-15",
            "prior_filing_date": "2025-02-15",
            "current_s3_prefix": "s3://bucket/filings/current/sections",
            "prior_s3_prefix": "s3://bucket/filings/prior/sections",
            "pair_id": "pair-001",
        },
    )


@pytest.fixture
def filing_chain_ready() -> FilingChainReady:
    return FilingChainReady(
        **_base_kwargs(),
        payload={
            "entity_id": "AAPL",
            "form_type": "10-K",
            "chain_length": 3,
            "latest_filing_date": "2026-02-15",
            "filing_dates": ["2024-02-15", "2025-02-15", "2026-02-15"],
            "chain_id": "chain-001",
        },
    )


# ---------------------------------------------------------------------------
# 1. Instantiation -- every event type can be created with valid data
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_raw_event_collected(self, raw_event_collected: RawEventCollected) -> None:
        assert raw_event_collected.event_type == "RAW_EVENT_COLLECTED"
        assert raw_event_collected.timestamp == NOW
        assert raw_event_collected.source == SOURCE
        assert raw_event_collected.trace_id == TRACE
        assert raw_event_collected.payload["event_id"] == "evt-001"

    def test_feature_extracted(self, feature_extracted: FeatureExtracted) -> None:
        assert feature_extracted.event_type == "FEATURE_EXTRACTED"
        assert feature_extracted.payload["feature_id"] == "feat-001"

    def test_signal_scored(self, signal_scored: SignalScored) -> None:
        assert signal_scored.event_type == "SIGNAL_SCORED"
        assert signal_scored.payload["score"] == 0.87

    def test_wave_computed(self, wave_computed: WaveComputed) -> None:
        assert wave_computed.event_type == "WAVE_COMPUTED"
        assert wave_computed.payload["strength"] == 0.92

    def test_narrative_updated(self, narrative_updated: NarrativeUpdated) -> None:
        assert narrative_updated.event_type == "NARRATIVE_UPDATED"
        assert narrative_updated.payload["gravity_score"] == 0.75

    def test_attention_field_updated(self, attention_field_updated: AttentionFieldUpdated) -> None:
        assert attention_field_updated.event_type == "ATTENTION_FIELD_UPDATED"
        assert attention_field_updated.payload["temperature"] == 0.5

    def test_trade_candidate_generated(self, trade_candidate_generated: TradeCandidateGenerated) -> None:
        assert trade_candidate_generated.event_type == "TRADE_CANDIDATE_GENERATED"
        assert trade_candidate_generated.payload["risk_status"] == "APPROVED"

    def test_filing_pair_ready(self, filing_pair_ready: FilingPairReady) -> None:
        assert filing_pair_ready.event_type == "FILING_PAIR_READY"
        assert filing_pair_ready.payload["entity_id"] == "AAPL"
        assert filing_pair_ready.payload["pair_id"] == "pair-001"

    def test_filing_chain_ready(self, filing_chain_ready: FilingChainReady) -> None:
        assert filing_chain_ready.event_type == "FILING_CHAIN_READY"
        assert filing_chain_ready.payload["chain_length"] == 3
        assert filing_chain_ready.payload["chain_id"] == "chain-001"


# ---------------------------------------------------------------------------
# 2. Round-trip: create -> to_sqs_message -> from_sqs_message -> verify
# ---------------------------------------------------------------------------


class TestSqsRoundTrip:
    def test_raw_event_collected_round_trip(self, raw_event_collected: RawEventCollected) -> None:
        msg = raw_event_collected.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, RawEventCollected)
        assert restored.event_type == raw_event_collected.event_type
        assert restored.payload == raw_event_collected.payload
        assert restored.trace_id == raw_event_collected.trace_id

    def test_feature_extracted_round_trip(self, feature_extracted: FeatureExtracted) -> None:
        msg = feature_extracted.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, FeatureExtracted)
        assert restored.payload == feature_extracted.payload

    def test_signal_scored_round_trip(self, signal_scored: SignalScored) -> None:
        msg = signal_scored.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, SignalScored)
        assert restored.payload == signal_scored.payload

    def test_wave_computed_round_trip(self, wave_computed: WaveComputed) -> None:
        msg = wave_computed.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, WaveComputed)
        assert restored.payload == wave_computed.payload

    def test_narrative_updated_round_trip(self, narrative_updated: NarrativeUpdated) -> None:
        msg = narrative_updated.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, NarrativeUpdated)
        assert restored.payload == narrative_updated.payload

    def test_attention_field_updated_round_trip(self, attention_field_updated: AttentionFieldUpdated) -> None:
        msg = attention_field_updated.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, AttentionFieldUpdated)
        assert restored.payload == attention_field_updated.payload

    def test_trade_candidate_generated_round_trip(self, trade_candidate_generated: TradeCandidateGenerated) -> None:
        msg = trade_candidate_generated.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, TradeCandidateGenerated)
        assert restored.payload == trade_candidate_generated.payload

    def test_filing_pair_ready_round_trip(self, filing_pair_ready: FilingPairReady) -> None:
        msg = filing_pair_ready.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, FilingPairReady)
        assert restored.payload == filing_pair_ready.payload

    def test_filing_chain_ready_round_trip(self, filing_chain_ready: FilingChainReady) -> None:
        msg = filing_chain_ready.to_sqs_message()
        restored = BaseEvent.from_sqs_message(msg)
        assert isinstance(restored, FilingChainReady)
        assert restored.payload == filing_chain_ready.payload


# ---------------------------------------------------------------------------
# 3. Payload validation -- missing required fields raise ValidationError
# ---------------------------------------------------------------------------


class TestPayloadValidation:
    def test_raw_event_collected_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            RawEventCollected(
                **_base_kwargs(),
                payload={"event_id": "evt-001", "entity_id": "ent-001"},
                # missing: source, content_hash, raw_artifact_s3
            )

    def test_feature_extracted_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            FeatureExtracted(
                **_base_kwargs(),
                payload={"feature_id": "feat-001"},
                # missing: event_id, entity_id, feature_type
            )

    def test_signal_scored_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            SignalScored(
                **_base_kwargs(),
                payload={"signal_id": "sig-001", "entity_id": "ent-001"},
                # missing: score, weight_version, attention_field_version
            )

    def test_wave_computed_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            WaveComputed(
                **_base_kwargs(),
                payload={"wave_id": "wav-001"},
                # missing: entity_id, strength, signal_count
            )

    def test_narrative_updated_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            NarrativeUpdated(
                **_base_kwargs(),
                payload={"narrative_id": "nar-001"},
                # missing: lifecycle_state, gravity_score
            )

    def test_attention_field_updated_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            AttentionFieldUpdated(
                **_base_kwargs(),
                payload={"field_id": "af-001"},
                # missing: version, temperature
            )

    def test_trade_candidate_generated_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            TradeCandidateGenerated(
                **_base_kwargs(),
                payload={"candidate_id": "tc-001"},
                # missing: signal_id, entity_id, score, risk_status
            )

    def test_filing_pair_ready_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            FilingPairReady(
                **_base_kwargs(),
                payload={"entity_id": "AAPL", "form_type": "10-K"},
                # missing: current_filing_date, prior_filing_date, current_s3_prefix, prior_s3_prefix, pair_id
            )

    def test_filing_chain_ready_missing_field(self) -> None:
        with pytest.raises(ValidationError, match="Missing required payload fields"):
            FilingChainReady(
                **_base_kwargs(),
                payload={"entity_id": "AAPL", "form_type": "10-K"},
                # missing: chain_length, latest_filing_date, filing_dates, chain_id
            )

    def test_empty_payload_raises(self) -> None:
        """Every concrete event type rejects an empty payload."""
        for event_cls in EVENT_TYPE_REGISTRY.values():
            with pytest.raises(ValidationError, match="Missing required payload fields"):
                event_cls(**_base_kwargs(), payload={})


# ---------------------------------------------------------------------------
# 4. to_eventbridge_entry produces correct structure
# ---------------------------------------------------------------------------


class TestEventBridgeEntry:
    def test_eventbridge_entry_structure(self, raw_event_collected: RawEventCollected) -> None:
        entry = raw_event_collected.to_eventbridge_entry("my-bus")
        assert entry["Source"] == f"signalfft.{SOURCE}"
        assert entry["DetailType"] == "RAW_EVENT_COLLECTED"
        assert entry["EventBusName"] == "my-bus"
        # Detail is a JSON string that can be deserialized
        detail = json.loads(entry["Detail"])
        assert detail["event_type"] == "RAW_EVENT_COLLECTED"
        assert detail["payload"]["event_id"] == "evt-001"

    def test_eventbridge_entry_all_keys_present(self, signal_scored: SignalScored) -> None:
        entry = signal_scored.to_eventbridge_entry("signals-bus")
        assert set(entry.keys()) == {"Source", "DetailType", "Detail", "EventBusName"}

    def test_eventbridge_entry_detail_is_valid_json(self, wave_computed: WaveComputed) -> None:
        entry = wave_computed.to_eventbridge_entry("waves-bus")
        parsed = json.loads(entry["Detail"])
        assert parsed["event_type"] == "WAVE_COMPUTED"
        assert parsed["trace_id"] == TRACE


# ---------------------------------------------------------------------------
# 5. from_sqs_message with invalid JSON raises error
# ---------------------------------------------------------------------------


class TestInvalidJson:
    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            BaseEvent.from_sqs_message("not valid json {{{")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            BaseEvent.from_sqs_message("")


# ---------------------------------------------------------------------------
# 6. from_sqs_message with unknown event_type raises ValueError
# ---------------------------------------------------------------------------


class TestUnknownEventType:
    def test_unknown_event_type_raises(self) -> None:
        data = json.dumps({
            "event_type": "TOTALLY_UNKNOWN",
            "timestamp": NOW,
            "source": SOURCE,
            "trace_id": TRACE,
            "payload": {},
        })
        with pytest.raises(ValueError, match="Unknown event_type: TOTALLY_UNKNOWN"):
            BaseEvent.from_sqs_message(data)

    def test_missing_event_type_raises(self) -> None:
        data = json.dumps({
            "timestamp": NOW,
            "source": SOURCE,
            "trace_id": TRACE,
            "payload": {},
        })
        with pytest.raises(ValueError, match="Unknown event_type: None"):
            BaseEvent.from_sqs_message(data)


# ---------------------------------------------------------------------------
# 7. Event type registry is complete and correct
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registry_has_all_event_types(self) -> None:
        expected_keys = {
            "RAW_EVENT_COLLECTED",
            "FEATURE_EXTRACTED",
            "SIGNAL_SCORED",
            "WAVE_COMPUTED",
            "NARRATIVE_UPDATED",
            "ATTENTION_FIELD_UPDATED",
            "TRADE_CANDIDATE_GENERATED",
            "FILING_DOCUMENT_REQUESTED",
            "FILING_DOCUMENT_READY",
            "FILING_SECTIONS_READY",
            "FILING_PAIR_READY",
            "FILING_CHAIN_READY",
            "HIGH_PRIORITY_FILING",
            "TRIAGE_COMPLETE",
            "DELTA_ANALYSIS_COMPLETE",
        }
        assert set(EVENT_TYPE_REGISTRY.keys()) == expected_keys

    def test_registry_values_are_base_event_subclasses(self) -> None:
        for name, cls in EVENT_TYPE_REGISTRY.items():
            assert issubclass(cls, BaseEvent), f"{name} is not a BaseEvent subclass"

    def test_registry_round_trip_all_types(self) -> None:
        """Every registered type can round-trip through SQS serialization."""
        payloads = {
            "RAW_EVENT_COLLECTED": {
                "event_id": "e1", "entity_id": "en1", "source": "S",
                "content_hash": "h", "raw_artifact_s3": "s3://b/k",
            },
            "FEATURE_EXTRACTED": {
                "feature_id": "f1", "event_id": "e1", "entity_id": "en1",
                "feature_type": "SENTIMENT",
            },
            "SIGNAL_SCORED": {
                "signal_id": "s1", "entity_id": "en1", "score": 0.5,
                "weight_version": "v1", "attention_field_version": "af-v1",
            },
            "WAVE_COMPUTED": {
                "wave_id": "w1", "entity_id": "en1", "strength": 0.8,
                "signal_count": 10,
            },
            "NARRATIVE_UPDATED": {
                "narrative_id": "n1", "lifecycle_state": "EMERGING",
                "gravity_score": 0.6,
            },
            "ATTENTION_FIELD_UPDATED": {
                "field_id": "af1", "version": "v2", "temperature": 0.3,
            },
            "TRADE_CANDIDATE_GENERATED": {
                "candidate_id": "tc1", "signal_id": "s1", "entity_id": "en1",
                "score": 0.9, "risk_status": "APPROVED",
            },
            "FILING_DOCUMENT_REQUESTED": {
                "event_id": "e1", "entity_id": "en1",
                "filing_url": "https://sec.gov/filing", "form_type": "10-K",
                "filing_date": "2026-02-14", "cik": "1234567",
            },
            "FILING_DOCUMENT_READY": {
                "event_id": "e1", "entity_id": "en1",
                "filing_s3_uri": "s3://bucket/filings/key", "form_type": "10-K",
                "filing_date": "2026-02-14", "cik": "1234567",
            },
            "FILING_SECTIONS_READY": {
                "event_id": "e1", "entity_id": "en1", "cik": "1234567",
                "form_type": "10-K", "filing_date": "2026-02-14",
                "sections_available": ["item_1", "item_1a"],
                "section_s3_prefix": "s3://bucket/filings/1234567/10-K/2026-02-14/sections",
                "total_text_length": 50000,
            },
            "FILING_PAIR_READY": {
                "entity_id": "AAPL", "form_type": "10-K",
                "current_filing_date": "2026-02-15",
                "prior_filing_date": "2025-02-15",
                "current_s3_prefix": "s3://bucket/current",
                "prior_s3_prefix": "s3://bucket/prior",
                "pair_id": "p1",
            },
            "FILING_CHAIN_READY": {
                "entity_id": "AAPL", "form_type": "10-K",
                "chain_length": 2,
                "latest_filing_date": "2026-02-15",
                "filing_dates": ["2025-02-15", "2026-02-15"],
                "chain_id": "c1",
            },
            "HIGH_PRIORITY_FILING": {
                "event_id": "e1", "entity_id": "AAPL",
                "priority_level": "HIGH",
                "matched_categories": ["executive_changes"],
                "matched_terms": [{"term": "resigned", "category": "executive_changes", "position": 10}],
            },
            "TRIAGE_COMPLETE": {
                "entity_id": "AAPL", "event_id": "e1",
                "materiality_score": 8, "attention_likelihood": "low",
                "direction": "bearish", "is_quiet_filing": True,
                "boost_multiplier": 1.5, "suggested_urgency": "act",
            },
            "DELTA_ANALYSIS_COMPLETE": {
                "entity_id": "AAPL", "pair_id": "p1",
                "current_filing_date": "2026-03-01",
                "prior_filing_date": "2025-03-01",
                "form_type": "10-K", "sections_analyzed": ["item_7", "item_1a"],
                "shift_count": 4, "composite_score": 0.72,
                "dominant_direction": "bearish",
            },
        }
        for event_type, payload in payloads.items():
            cls = EVENT_TYPE_REGISTRY[event_type]
            event = cls(**_base_kwargs(), payload=payload)
            msg = event.to_sqs_message()
            restored = BaseEvent.from_sqs_message(msg)
            assert type(restored) is cls
            assert restored.event_type == event_type
            assert restored.payload == payload


# ---------------------------------------------------------------------------
# 8. Literal event_type enforcement
# ---------------------------------------------------------------------------


class TestLiteralEventType:
    def test_wrong_event_type_literal_rejected(self) -> None:
        """Providing wrong event_type string for a concrete class is rejected."""
        with pytest.raises(ValidationError):
            RawEventCollected(
                event_type="WRONG_TYPE",
                timestamp=NOW,
                source=SOURCE,
                trace_id=TRACE,
                payload={
                    "event_id": "e", "entity_id": "en", "source": "S",
                    "content_hash": "h", "raw_artifact_s3": "s3://b/k",
                },
            )

    def test_default_event_type_is_set(self) -> None:
        """Concrete classes auto-set event_type when not explicitly provided."""
        event = FeatureExtracted(
            **_base_kwargs(),
            payload={
                "feature_id": "f1", "event_id": "e1",
                "entity_id": "en1", "feature_type": "SENTIMENT",
            },
        )
        assert event.event_type == "FEATURE_EXTRACTED"


# ---------------------------------------------------------------------------
# 9. Extra payload fields are allowed
# ---------------------------------------------------------------------------


class TestExtraPayloadFields:
    def test_extra_fields_preserved(self) -> None:
        """Payload may contain additional fields beyond the required set."""
        event = RawEventCollected(
            **_base_kwargs(),
            payload={
                "event_id": "e1",
                "entity_id": "en1",
                "source": "S",
                "content_hash": "h",
                "raw_artifact_s3": "s3://b/k",
                "extra_field": "extra_value",
                "another": 42,
            },
        )
        assert event.payload["extra_field"] == "extra_value"
        assert event.payload["another"] == 42
