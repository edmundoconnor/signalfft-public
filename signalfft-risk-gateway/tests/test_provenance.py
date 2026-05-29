"""Tests for provenance stamping."""
import os

from risk_gateway.provenance import stamp_provenance


def test_stamp_provenance_defaults():
    candidate = {"candidate_id": "C1", "score": 0.10}
    result = stamp_provenance(candidate)
    assert result is candidate  # mutates in place
    assert result["signal_model_version"] == "unknown"
    assert result["attention_field_version"] == "unknown"
    assert result["opus_config_version"] == "unknown"
    assert result["engine_container_sha"] == "unknown"


def test_stamp_provenance_from_env(monkeypatch):
    monkeypatch.setenv("SIGNAL_MODEL_VERSION", "v2.1")
    monkeypatch.setenv("OPUS_CONFIG_VERSION", "cfg-42")
    monkeypatch.setenv("ENGINE_CONTAINER_SHA", "sha256:abc123")

    candidate = {"candidate_id": "C2", "score": 0.20}
    result = stamp_provenance(candidate)
    assert result["signal_model_version"] == "v2.1"
    assert result["opus_config_version"] == "cfg-42"
    assert result["engine_container_sha"] == "sha256:abc123"


def test_stamp_provenance_preserves_attention_field_version():
    candidate = {
        "candidate_id": "C3",
        "score": 0.15,
        "attention_field_version": "v3",
    }
    result = stamp_provenance(candidate)
    assert result["attention_field_version"] == "v3"
