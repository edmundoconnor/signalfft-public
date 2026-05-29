"""Narrative gravity ECS Fargate service.

Aggregates signals and waves into narrative arcs with gravity scores.
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


class NarrativeGravityService:
    """Computes narrative gravity by aggregating entity signals over time."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self._signals_table = self._dynamo.Table(
            os.environ.get("SIGNALS_TABLE", f"{self._env}-signalfft-signals")
        )
        self._narratives_table = self._dynamo.Table(
            os.environ.get("NARRATIVES_TABLE", f"{self._env}-signalfft-narratives")
        )
        self._entities_table = self._dynamo.Table(
            os.environ.get("ENTITIES_TABLE", f"{self._env}-signalfft-entities")
        )
        self._scan_interval = int(os.environ.get("SCAN_INTERVAL_SECONDS", "30"))
        self._running = True

        graph_table = os.environ.get("GRAPH_EDGES_TABLE")
        if graph_table:
            from engine.memory_graph.writer import GraphWriter
            self._graph = GraphWriter(table_name=graph_table, region=self._region)
        else:
            logger.debug("Graph writes disabled — GRAPH_EDGES_TABLE not set")
            self._graph = None

    def run(self) -> None:
        logger.info("Narrative gravity service starting")
        while self._running:
            try:
                self._compute_narratives()
            except Exception:
                logger.exception("Error computing narratives")
            time.sleep(self._scan_interval)

    def stop(self) -> None:
        self._running = False

    def _compute_narratives(self) -> None:
        """Scan recent signals and compute narrative gravity scores."""
        response = self._signals_table.scan(Limit=100)
        items = response.get("Items", [])

        # Group signals by entity
        entity_signals: dict[str, list] = {}
        for item in items:
            eid = item.get("entity_id", "")
            if eid:
                entity_signals.setdefault(eid, []).append(item)

        now = datetime.now(timezone.utc).isoformat()

        for entity_id, signals in entity_signals.items():
            if len(signals) < 2:
                continue

            scores = [float(s.get("score", 0)) for s in signals]
            gravity = sum(scores) / len(scores)

            narrative_id = str(uuid.uuid4())
            self._narratives_table.put_item(
                Item={
                    "PK": f"ENTITY#{entity_id}",
                    "SK": f"NARRATIVE#{now}#{narrative_id}",
                    "narrative_id": narrative_id,
                    "entity_id": entity_id,
                    "gravity_score": Decimal(str(round(gravity, 6))),
                    "signal_count": len(signals),
                    "lifecycle_state": "active",
                    "created_at": now,
                }
            )

            # Write graph edges
            if self._graph:
                try:
                    self._graph.on_narrative_updated(
                        narrative_id=narrative_id,
                        entity_ids=[entity_id],
                    )
                except Exception:
                    logger.exception("Graph write failed for narrative %s", narrative_id)

            logger.info(
                "Narrative %s for %s: gravity=%.4f signals=%d",
                narrative_id, entity_id, gravity, len(signals),
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = NarrativeGravityService()
    service.run()
