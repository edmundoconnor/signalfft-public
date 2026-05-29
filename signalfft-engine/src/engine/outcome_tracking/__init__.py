"""Outcome tracking service — captures price snapshots when signals fire."""

from engine.outcome_tracking.price_snapshot import capture_price_snapshot
from engine.outcome_tracking.service import OutcomeTrackingService

__all__ = ["capture_price_snapshot", "OutcomeTrackingService"]
