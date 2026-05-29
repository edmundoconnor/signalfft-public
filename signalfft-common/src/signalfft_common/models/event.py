"""Event model -- raw inbound artifacts ingested by SignalFFT."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Event:
    """An ingested raw artifact (filing, article, post, etc.)."""

    event_id: str
    entity_id: str
    source: str
    raw_artifact_s3: str
    event_type: str
    content_hash: str
    created_at: str  # ISO 8601
