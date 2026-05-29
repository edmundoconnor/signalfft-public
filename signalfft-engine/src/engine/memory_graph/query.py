import os
import logging
from collections import deque

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)

MAX_NODES = 500


class GraphQuery:
    """Query the Memory Graph for neighborhood traversal and pattern analysis."""

    def __init__(self, table_name: str = None, region: str = "us-east-1"):
        table_name = table_name or os.environ.get(
            "GRAPH_EDGES_TABLE", "prod-signalfft-graph-edges"
        )
        dynamo = boto3.resource("dynamodb", region_name=region)
        self._table = dynamo.Table(table_name)

    def _parse_edges(self, items: list[dict]) -> list[dict]:
        """Parse DynamoDB items into edge dicts, extracting target_id from SK."""
        results = []
        for item in items:
            sk = item.get("SK", "")
            parts = sk.split("#", 2)
            if len(parts) < 3:
                continue
            target_id = parts[2]
            results.append({
                "target_id": target_id,
                "target_type": item.get("target_type", ""),
                "edge_type": item.get("edge_type", ""),
                "metadata": item.get("metadata", {}),
                "created_at": item.get("created_at", ""),
            })
        return results

    def get_neighbors(self, node_id: str, edge_type: str | None = None) -> list[dict]:
        pk = f"NODE#{node_id}"
        sk_prefix = f"EDGE#{edge_type}#" if edge_type else "EDGE#"
        response = self._table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix),
        )
        return self._parse_edges(response.get("Items", []))

    def get_reverse_neighbors(self, node_id: str, edge_type: str | None = None) -> list[dict]:
        pk = f"NODE#{node_id}"
        sk_prefix = f"EDGE#{edge_type}#" if edge_type else "EDGE#"
        response = self._table.query(
            IndexName="reverse-lookup",
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix),
        )
        return self._parse_edges(response.get("Items", []))

    def k_hop_neighborhood(self, node_id: str, k: int = 2, edge_types: list[str] | None = None) -> dict:
        nodes: dict[str, dict] = {node_id: {"depth": 0}}
        edges: list[dict] = []
        visited: set[str] = {node_id}
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])

        while queue:
            current, depth = queue.popleft()
            if depth >= k:
                continue

            neighbors = self.get_neighbors(current)
            for neighbor in neighbors:
                tid = neighbor["target_id"]
                etype = neighbor["edge_type"]

                if edge_types and etype not in edge_types:
                    continue

                edges.append({
                    "source": current,
                    "target": tid,
                    "edge_type": etype,
                    "metadata": neighbor["metadata"],
                })

                if tid not in visited:
                    if len(visited) >= MAX_NODES:
                        logger.warning(
                            "k-hop traversal hit %d node limit at depth %d",
                            MAX_NODES, depth + 1,
                        )
                        return {"root": node_id, "nodes": nodes, "edges": edges}
                    visited.add(tid)
                    nodes[tid] = {"depth": depth + 1}
                    queue.append((tid, depth + 1))

        return {"root": node_id, "nodes": nodes, "edges": edges}

    def get_entity_signals(self, entity_id: str, limit: int = 50) -> list[dict]:
        neighbors = self.get_neighbors(entity_id, edge_type="ENTITY_HAS_SIGNAL")
        neighbors.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return neighbors[:limit]

    def get_entity_outcomes(self, entity_id: str) -> list[dict]:
        signals = self.get_entity_signals(entity_id, limit=50)
        outcomes: list[dict] = []
        for sig in signals:
            sig_neighbors = self.get_neighbors(
                sig["target_id"], edge_type="SIGNAL_ASSOCIATED_WITH_OUTCOME"
            )
            outcomes.extend(sig_neighbors)
        return outcomes

    def get_entity_pattern_score(self, entity_id: str) -> float:
        signals = self.get_entity_signals(entity_id)
        if not signals:
            return 0.0

        meaningful = 0
        for sig in signals:
            meta = sig.get("metadata", {})
            try:
                score_val = float(meta.get("score", 0))
            except (TypeError, ValueError):
                score_val = 0.0
            if score_val > 0.1:
                meaningful += 1

        signal_density = meaningful / len(signals)

        outcomes = self.get_entity_outcomes(entity_id)
        if outcomes:
            outcome_factor = min(len(outcomes) / len(signals), 1.0)
        else:
            outcome_factor = 0.0

        pattern_score = (signal_density * 0.6) + (outcome_factor * 0.4)
        return max(0.0, min(1.0, pattern_score))
