import os
import logging
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)


class GraphWriter:
    """Writes edges to the graph_edges DynamoDB table (adjacency-list pattern)."""

    def __init__(self, table_name: str = None, region: str = "us-east-1"):
        table_name = table_name or os.environ.get(
            "GRAPH_EDGES_TABLE", "prod-signalfft-graph-edges"
        )
        dynamo = boto3.resource("dynamodb", region_name=region)
        self._table = dynamo.Table(table_name)

    def upsert_edge(
        self,
        source_id: str,
        source_type: str,
        target_id: str,
        target_type: str,
        edge_type: str,
        metadata: dict | None = None,
    ) -> None:
        metadata = metadata or {}
        now = datetime.now(timezone.utc).isoformat()

        # Forward edge
        self._table.put_item(Item={
            "PK": f"NODE#{source_id}",
            "SK": f"EDGE#{edge_type}#{target_id}",
            "edge_type": edge_type,
            "target_type": target_type,
            "metadata": metadata,
            "created_at": now,
        })
        logger.debug("Forward edge: %s -[%s]-> %s", source_id, edge_type, target_id)

        # Reverse edge (for GSI-based lookups)
        self._table.put_item(Item={
            "PK": f"NODE#{target_id}",
            "SK": f"EDGE#{edge_type}#{source_id}",
            "edge_type": edge_type,
            "target_type": source_type,
            "metadata": metadata,
            "created_at": now,
        })
        logger.debug("Reverse edge: %s -[%s]-> %s", target_id, edge_type, source_id)

    def link_entity_event(self, entity_id: str, event_id: str, metadata: dict | None = None) -> None:
        self.upsert_edge(entity_id, "ENTITY", event_id, "EVENT", "ENTITY_HAS_EVENT", metadata)

    def link_entity_signal(self, entity_id: str, signal_id: str, metadata: dict | None = None) -> None:
        self.upsert_edge(entity_id, "ENTITY", signal_id, "SIGNAL", "ENTITY_HAS_SIGNAL", metadata)

    def link_signal_outcome(self, signal_id: str, outcome_id: str, metadata: dict | None = None) -> None:
        self.upsert_edge(signal_id, "SIGNAL", outcome_id, "OUTCOME", "SIGNAL_ASSOCIATED_WITH_OUTCOME", metadata)

    def link_signal_wave(self, signal_id: str, wave_id: str, metadata: dict | None = None) -> None:
        self.upsert_edge(signal_id, "SIGNAL", wave_id, "WAVE", "SIGNAL_PART_OF_WAVE", metadata)

    def link_entity_narrative(self, entity_id: str, narrative_id: str, metadata: dict | None = None) -> None:
        self.upsert_edge(entity_id, "ENTITY", narrative_id, "NARRATIVE", "ENTITY_CAPTURED_BY_NARRATIVE", metadata)

    def on_signal_created(self, signal_id: str, entity_id: str, event_id: str, score: float = None) -> None:
        meta = {"score": str(score)} if score is not None else None
        self.link_entity_event(entity_id, event_id, meta)
        self.link_entity_signal(entity_id, signal_id, meta)
        self.upsert_edge(signal_id, "SIGNAL", event_id, "EVENT", "SIGNAL_FROM_EVENT", meta)
        logger.info("Graph edges created for signal %s / entity %s", signal_id, entity_id)

    def on_wave_created(self, wave_id: str, entity_id: str, signal_ids: list[str]) -> None:
        for sid in signal_ids:
            self.link_signal_wave(sid, wave_id)
        logger.info("Wave %s linked to %d signals", wave_id, len(signal_ids))

    def on_narrative_updated(self, narrative_id: str, entity_ids: list[str]) -> None:
        for eid in entity_ids:
            self.link_entity_narrative(eid, narrative_id)
        logger.info("Narrative %s linked to %d entities", narrative_id, len(entity_ids))
