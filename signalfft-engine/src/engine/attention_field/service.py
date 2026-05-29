"""Attention field ECS Fargate service.

Computes the attention field — a real-time view of where signal
density is highest across entities.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

logger = logging.getLogger(__name__)


class AttentionFieldService:
    """Periodically scans signals and updates the attention field."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self._signals_table = self._dynamo.Table(
            os.environ.get("SIGNALS_TABLE", f"{self._env}-signalfft-signals")
        )
        self._attention_table = self._dynamo.Table(
            os.environ.get("ATTENTION_FIELD_TABLE", f"{self._env}-signalfft-attention-field")
        )
        self._scan_interval = int(os.environ.get("SCAN_INTERVAL_SECONDS", "30"))
        self._running = True

    def run(self) -> None:
        logger.info("Attention field service starting")
        while self._running:
            try:
                self._update_attention_field()
            except Exception:
                logger.exception("Error updating attention field")
            time.sleep(self._scan_interval)

    def stop(self) -> None:
        self._running = False

    def _update_attention_field(self) -> None:
        """Scan signals and compute attention density per entity."""
        response = self._signals_table.scan(Limit=200)
        items = response.get("Items", [])

        entity_scores: dict[str, list[float]] = {}
        for item in items:
            eid = item.get("entity_id", "")
            score = float(item.get("score", 0))
            if eid:
                entity_scores.setdefault(eid, []).append(score)

        now = datetime.now(timezone.utc).isoformat()

        for entity_id, scores in entity_scores.items():
            density = sum(scores)
            avg_score = density / len(scores) if scores else 0.0

            self._attention_table.put_item(
                Item={
                    "PK": f"ENTITY#{entity_id}",
                    "SK": f"FIELD#{now}",
                    "entity_id": entity_id,
                    "density": Decimal(str(round(density, 6))),
                    "avg_score": Decimal(str(round(avg_score, 6))),
                    "signal_count": len(scores),
                    "updated_at": now,
                }
            )

        logger.info("Attention field updated for %d entities", len(entity_scores))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = AttentionFieldService()
    service.run()
