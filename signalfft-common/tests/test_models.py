"""Unit tests for SignalFFT domain models and enums."""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, FrozenInstanceError

import pytest

from signalfft_common.enums import (
    EdgeType,
    FeatureType,
    NarrativeState,
    RiskStatus,
    SignalType,
)
from signalfft_common.models import (
    AttentionField,
    Entity,
    Event,
    Feature,
    Narrative,
    Outcome,
    Signal,
    TradeCandidate,
    Wave,
    WeightConfig,
)


# ---------------------------------------------------------------------------
# Fixtures -- canonical instances for every model
# ---------------------------------------------------------------------------

NOW = "2026-02-15T12:00:00Z"


@pytest.fixture
def entity() -> Entity:
    return Entity(
        entity_id="ent-001",
        entity_type="COMPANY",
        name="Acme Corp",
        aliases={"ACME", "Acme"},
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def event() -> Event:
    return Event(
        event_id="evt-001",
        entity_id="ent-001",
        source="SEC_EDGAR",
        raw_artifact_s3="s3://bucket/filings/10k.pdf",
        event_type="SEC_FILING",
        content_hash="abc123",
        created_at=NOW,
    )


@pytest.fixture
def feature() -> Feature:
    return Feature(
        feature_id="feat-001",
        event_id="evt-001",
        entity_id="ent-001",
        feature_type=FeatureType.SENTIMENT,
        value={"polarity": 0.8, "magnitude": 0.9},
        created_at=NOW,
    )


@pytest.fixture
def signal() -> Signal:
    return Signal(
        signal_id="sig-001",
        entity_id="ent-001",
        score=0.87,
        components={
            "novelty": 0.9,
            "velocity": 0.7,
            "cross_source": 0.6,
            "semantic_impact": 0.8,
            "entity_sensitivity": 0.5,
            "historical_pattern": 0.4,
            "noise_penalty": 0.1,
        },
        weight_version="v1",
        attention_field_version="af-v1",
        created_at=NOW,
    )


@pytest.fixture
def weight_config() -> WeightConfig:
    return WeightConfig(wn=0.2, wv=0.15, wc=0.15, ws=0.2, we=0.1, wh=0.1, wp=0.1)


@pytest.fixture
def wave() -> Wave:
    return Wave(
        wave_id="wav-001",
        entity_id="ent-001",
        window_end=NOW,
        strength=0.92,
        components={
            "density": 0.8,
            "acceleration": 0.7,
            "coherence": 0.9,
            "spread": 0.6,
        },
        signal_count=15,
        ttl=1739620800,
    )


@pytest.fixture
def narrative() -> Narrative:
    return Narrative(
        narrative_id="nar-001",
        lifecycle_state=NarrativeState.EMERGING,
        gravity_score=0.75,
        entities={"ent-001", "ent-002"},
        claude_label="AI chip shortage narrative",
        transition_history=[
            {"from": "EMERGING", "to": "ACCELERATING", "at": NOW},
        ],
        created_at=NOW,
    )


@pytest.fixture
def attention_field() -> AttentionField:
    return AttentionField(
        field_id="af-001",
        timestamp=NOW,
        modifier_vector={"wn": 1.2, "wv": 0.9},
        temperature=0.5,
        narrative_field_strength=0.8,
        version="af-v1",
    )


@pytest.fixture
def trade_candidate() -> TradeCandidate:
    return TradeCandidate(
        candidate_id="tc-001",
        signal_id="sig-001",
        entity_id="ent-001",
        score=0.87,
        risk_status=RiskStatus.APPROVED,
        risk_rejection_reason=None,
        signal_model_version="sm-v1",
        attention_field_version="af-v1",
        opus_config_version="oc-v1",
        engine_container_sha="sha256:abc123",
        created_at=NOW,
    )


@pytest.fixture
def outcome() -> Outcome:
    return Outcome(
        outcome_id="out-001",
        signal_id="sig-001",
        candidate_id="tc-001",
        fill_price=0.65,
        latency_ms=42,
        slippage=0.002,
        created_at=NOW,
    )


# ---------------------------------------------------------------------------
# 1. Instantiation tests -- every model can be created with valid data
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_entity(self, entity: Entity) -> None:
        assert entity.entity_id == "ent-001"
        assert entity.entity_type == "COMPANY"
        assert entity.name == "Acme Corp"

    def test_event(self, event: Event) -> None:
        assert event.event_id == "evt-001"
        assert event.source == "SEC_EDGAR"

    def test_feature(self, feature: Feature) -> None:
        assert feature.feature_type is FeatureType.SENTIMENT
        assert feature.value == {"polarity": 0.8, "magnitude": 0.9}

    def test_signal(self, signal: Signal) -> None:
        assert signal.score == 0.87
        assert "novelty" in signal.components

    def test_weight_config(self, weight_config: WeightConfig) -> None:
        assert weight_config.wn == 0.2
        assert weight_config.wp == 0.1

    def test_wave(self, wave: Wave) -> None:
        assert wave.strength == 0.92
        assert wave.signal_count == 15

    def test_narrative(self, narrative: Narrative) -> None:
        assert narrative.lifecycle_state is NarrativeState.EMERGING
        assert "ent-001" in narrative.entities

    def test_attention_field(self, attention_field: AttentionField) -> None:
        assert attention_field.temperature == 0.5
        assert attention_field.version == "af-v1"

    def test_trade_candidate(self, trade_candidate: TradeCandidate) -> None:
        assert trade_candidate.risk_status is RiskStatus.APPROVED
        assert trade_candidate.risk_rejection_reason is None

    def test_outcome(self, outcome: Outcome) -> None:
        assert outcome.fill_price == 0.65
        assert outcome.latency_ms == 42


# ---------------------------------------------------------------------------
# 2. All fields are set correctly after instantiation
# ---------------------------------------------------------------------------


class TestFieldValues:
    def test_entity_all_fields(self, entity: Entity) -> None:
        assert entity.entity_id == "ent-001"
        assert entity.entity_type == "COMPANY"
        assert entity.name == "Acme Corp"
        assert entity.aliases == {"ACME", "Acme"}
        assert entity.created_at == NOW
        assert entity.updated_at == NOW

    def test_event_all_fields(self, event: Event) -> None:
        assert event.event_id == "evt-001"
        assert event.entity_id == "ent-001"
        assert event.source == "SEC_EDGAR"
        assert event.raw_artifact_s3 == "s3://bucket/filings/10k.pdf"
        assert event.event_type == "SEC_FILING"
        assert event.content_hash == "abc123"
        assert event.created_at == NOW

    def test_feature_all_fields(self, feature: Feature) -> None:
        assert feature.feature_id == "feat-001"
        assert feature.event_id == "evt-001"
        assert feature.entity_id == "ent-001"
        assert feature.feature_type is FeatureType.SENTIMENT
        assert feature.value == {"polarity": 0.8, "magnitude": 0.9}
        assert feature.created_at == NOW

    def test_signal_all_fields(self, signal: Signal) -> None:
        assert signal.signal_id == "sig-001"
        assert signal.entity_id == "ent-001"
        assert signal.score == 0.87
        assert len(signal.components) == 7
        assert signal.weight_version == "v1"
        assert signal.attention_field_version == "af-v1"
        assert signal.created_at == NOW

    def test_wave_all_fields(self, wave: Wave) -> None:
        assert wave.wave_id == "wav-001"
        assert wave.entity_id == "ent-001"
        assert wave.window_end == NOW
        assert wave.strength == 0.92
        assert len(wave.components) == 4
        assert wave.signal_count == 15
        assert wave.ttl == 1739620800

    def test_narrative_all_fields(self, narrative: Narrative) -> None:
        assert narrative.narrative_id == "nar-001"
        assert narrative.lifecycle_state is NarrativeState.EMERGING
        assert narrative.gravity_score == 0.75
        assert narrative.entities == {"ent-001", "ent-002"}
        assert narrative.claude_label == "AI chip shortage narrative"
        assert len(narrative.transition_history) == 1
        assert narrative.created_at == NOW

    def test_attention_field_all_fields(self, attention_field: AttentionField) -> None:
        assert attention_field.field_id == "af-001"
        assert attention_field.timestamp == NOW
        assert attention_field.modifier_vector == {"wn": 1.2, "wv": 0.9}
        assert attention_field.temperature == 0.5
        assert attention_field.narrative_field_strength == 0.8
        assert attention_field.version == "af-v1"

    def test_trade_candidate_all_fields(self, trade_candidate: TradeCandidate) -> None:
        assert trade_candidate.candidate_id == "tc-001"
        assert trade_candidate.signal_id == "sig-001"
        assert trade_candidate.entity_id == "ent-001"
        assert trade_candidate.score == 0.87
        assert trade_candidate.risk_status is RiskStatus.APPROVED
        assert trade_candidate.risk_rejection_reason is None
        assert trade_candidate.signal_model_version == "sm-v1"
        assert trade_candidate.attention_field_version == "af-v1"
        assert trade_candidate.opus_config_version == "oc-v1"
        assert trade_candidate.engine_container_sha == "sha256:abc123"
        assert trade_candidate.created_at == NOW

    def test_outcome_all_fields(self, outcome: Outcome) -> None:
        assert outcome.outcome_id == "out-001"
        assert outcome.signal_id == "sig-001"
        assert outcome.candidate_id == "tc-001"
        assert outcome.fill_price == 0.65
        assert outcome.latency_ms == 42
        assert outcome.slippage == 0.002
        assert outcome.created_at == NOW

    def test_outcome_defaults_none(self) -> None:
        o = Outcome(outcome_id="o", signal_id="s", candidate_id="c")
        assert o.fill_price is None
        assert o.latency_ms is None
        assert o.slippage is None

    def test_trade_candidate_rejection_default_none(self) -> None:
        tc = TradeCandidate(
            candidate_id="tc",
            signal_id="s",
            entity_id="e",
            score=0.5,
            risk_status=RiskStatus.REJECTED,
        )
        assert tc.risk_rejection_reason is None

    def test_entity_aliases_default_empty(self) -> None:
        e = Entity(entity_id="e", entity_type="PERSON", name="Jane")
        assert e.aliases == set()


# ---------------------------------------------------------------------------
# 3. Serialization: asdict round-trip
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_entity_asdict(self, entity: Entity) -> None:
        d = asdict(entity)
        assert d["entity_id"] == "ent-001"
        assert d["aliases"] == {"ACME", "Acme"}
        # Round-trip: reconstruct from dict
        restored = Entity(**d)
        assert restored.entity_id == entity.entity_id
        assert restored.aliases == entity.aliases

    def test_event_asdict(self, event: Event) -> None:
        d = asdict(event)
        assert d["event_id"] == "evt-001"
        restored = Event(**d)
        assert restored == event

    def test_feature_asdict(self, feature: Feature) -> None:
        d = asdict(feature)
        assert d["feature_type"] is FeatureType.SENTIMENT
        restored = Feature(**d)
        assert restored == feature

    def test_signal_asdict(self, signal: Signal) -> None:
        d = asdict(signal)
        assert d["score"] == 0.87
        restored = Signal(**d)
        assert restored == signal

    def test_weight_config_asdict(self, weight_config: WeightConfig) -> None:
        d = asdict(weight_config)
        assert d["wn"] == 0.2
        restored = WeightConfig(**d)
        assert restored == weight_config

    def test_wave_asdict(self, wave: Wave) -> None:
        d = asdict(wave)
        assert d["strength"] == 0.92
        restored = Wave(**d)
        assert restored == wave

    def test_narrative_asdict(self, narrative: Narrative) -> None:
        d = asdict(narrative)
        assert d["lifecycle_state"] is NarrativeState.EMERGING
        restored = Narrative(**d)
        assert restored.narrative_id == narrative.narrative_id
        assert restored.entities == narrative.entities

    def test_attention_field_asdict(self, attention_field: AttentionField) -> None:
        d = asdict(attention_field)
        assert d["temperature"] == 0.5
        restored = AttentionField(**d)
        assert restored == attention_field

    def test_trade_candidate_asdict(self, trade_candidate: TradeCandidate) -> None:
        d = asdict(trade_candidate)
        assert d["risk_status"] is RiskStatus.APPROVED
        restored = TradeCandidate(**d)
        assert restored == trade_candidate

    def test_outcome_asdict(self, outcome: Outcome) -> None:
        d = asdict(outcome)
        assert d["fill_price"] == 0.65
        restored = Outcome(**d)
        assert restored == outcome


# ---------------------------------------------------------------------------
# 4. WeightConfig is frozen
# ---------------------------------------------------------------------------


class TestWeightConfigFrozen:
    def test_cannot_assign(self, weight_config: WeightConfig) -> None:
        with pytest.raises(FrozenInstanceError):
            weight_config.wn = 999.0  # type: ignore[misc]

    def test_cannot_delete(self, weight_config: WeightConfig) -> None:
        with pytest.raises(FrozenInstanceError):
            del weight_config.wn  # type: ignore[misc]

    def test_is_hashable(self, weight_config: WeightConfig) -> None:
        # Frozen dataclasses are hashable
        assert isinstance(hash(weight_config), int)


# ---------------------------------------------------------------------------
# 5. Enum value verification
# ---------------------------------------------------------------------------


class TestEnums:
    def test_narrative_state_values(self) -> None:
        expected = {"EMERGING", "ACCELERATING", "DOMINANT", "SATURATED", "DECAYING"}
        actual = {member.value for member in NarrativeState}
        assert actual == expected

    def test_narrative_state_members(self) -> None:
        assert NarrativeState.EMERGING.value == "EMERGING"
        assert NarrativeState.ACCELERATING.value == "ACCELERATING"
        assert NarrativeState.DOMINANT.value == "DOMINANT"
        assert NarrativeState.SATURATED.value == "SATURATED"
        assert NarrativeState.DECAYING.value == "DECAYING"

    def test_risk_status_values(self) -> None:
        expected = {"APPROVED", "REJECTED"}
        actual = {member.value for member in RiskStatus}
        assert actual == expected

    def test_risk_status_members(self) -> None:
        assert RiskStatus.APPROVED.value == "APPROVED"
        assert RiskStatus.REJECTED.value == "REJECTED"

    def test_signal_type_values(self) -> None:
        expected = {"SEC_FILING", "NEWS_ARTICLE", "SOCIAL_POST", "ANALYST_REPORT"}
        actual = {member.value for member in SignalType}
        assert actual == expected

    def test_feature_type_values(self) -> None:
        expected = {"ENTITY_MENTION", "SENTIMENT", "TEMPORAL_MARKER", "SOURCE_TYPE", "TRIAGE"}
        actual = {member.value for member in FeatureType}
        assert actual == expected

    def test_edge_type_values(self) -> None:
        expected = {
            "ENTITY_HAS_EVENT",
            "SIGNAL_ASSOCIATED_WITH_OUTCOME",
            "SIGNAL_PART_OF_WAVE",
            "ENTITY_CAPTURED_BY_NARRATIVE",
        }
        actual = {member.value for member in EdgeType}
        assert actual == expected
