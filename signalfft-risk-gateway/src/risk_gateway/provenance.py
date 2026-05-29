"""
Stamps trade candidates with version provenance for auditability.
Every candidate must record exactly which versions of each component produced it.
"""
import os


def stamp_provenance(candidate: dict) -> dict:
    """
    Add provenance fields to candidate dict (mutates and returns).
    """
    candidate["signal_model_version"] = os.environ.get("SIGNAL_MODEL_VERSION", "unknown")
    candidate["attention_field_version"] = candidate.get("attention_field_version", "unknown")
    candidate["opus_config_version"] = os.environ.get("OPUS_CONFIG_VERSION", "unknown")
    candidate["engine_container_sha"] = os.environ.get("ENGINE_CONTAINER_SHA", "unknown")
    return candidate
