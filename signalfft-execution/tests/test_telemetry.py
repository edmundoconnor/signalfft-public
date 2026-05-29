"""Tests for the telemetry recorder."""

import uuid

from execution.telemetry import TelemetryRecorder


def _make_fill_result():
    return {
        "order_id": str(uuid.uuid4()),
        "fill_price": 99.85,
        "slippage": -0.15,
        "latency_ms": 50,
        "direction": "BUY",
        "quantity": 100,
        "status": "FILLED",
    }


def test_record_fill_returns_outcome():
    recorder = TelemetryRecorder(table_name="")
    outcome = recorder.record_fill(
        candidate_id="tc-001",
        signal_id="sig-001",
        entity_id="ent-001",
        fill_result=_make_fill_result(),
    )

    assert outcome["candidate_id"] == "tc-001"
    assert outcome["signal_id"] == "sig-001"
    assert outcome["entity_id"] == "ent-001"
    assert outcome["fill_price"] == 99.85
    assert outcome["slippage"] == -0.15
    assert outcome["latency_ms"] == 50
    assert outcome["direction"] == "BUY"
    assert outcome["quantity"] == 100
    assert outcome["status"] == "FILLED"
    assert "outcome_id" in outcome
    assert "created_at" in outcome


def test_outcome_has_uuid():
    recorder = TelemetryRecorder(table_name="")
    outcome = recorder.record_fill(
        candidate_id="tc-002",
        signal_id="sig-002",
        entity_id="ent-002",
        fill_result=_make_fill_result(),
    )
    parsed = uuid.UUID(outcome["outcome_id"])
    assert str(parsed) == outcome["outcome_id"]


def test_log_only_mode():
    """TelemetryRecorder with no table name still returns outcome without error."""
    recorder = TelemetryRecorder(table_name="")
    outcome = recorder.record_fill(
        candidate_id="tc-003",
        signal_id="sig-003",
        entity_id="ent-003",
        fill_result=_make_fill_result(),
    )
    assert outcome["status"] == "FILLED"
    assert outcome["entity_id"] == "ent-003"
