"""Signal scoring ECS Fargate service.

Polls features SQS queue, computes signal scores, writes to DynamoDB,
emits SignalScored events.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

import boto3

from signalfft_common.dynamo.keys import build_signals_pk, build_signals_sk
from signalfft_common.events import BaseEvent
from signalfft_common.models import Signal

from signalfft_common.entity import EntityResolver

from engine.directional.aggregator import compute_direction_score
from engine.signal_scoring.scorer import compute_signal_score

logger = logging.getLogger(__name__)


class SignalScoringService:
    """Long-running service that scores features into signals."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("FEATURES_QUEUE_URL", "")
        self.output_queue_url = os.environ.get("SIGNALS_QUEUE_URL", "")
        self._risk_input_queue_url = os.environ.get("RISK_INPUT_QUEUE_URL", "")
        self._outcome_tracking_queue_url = os.environ.get("OUTCOME_TRACKING_QUEUE_URL", "")
        self._signals_table = self._dynamo.Table(
            os.environ.get("SIGNALS_TABLE", f"{self._env}-signalfft-signals")
        )
        self._features_table = self._dynamo.Table(
            os.environ.get("FEATURES_TABLE", f"{self._env}-signalfft-features")
        )
        self._events_table = self._dynamo.Table(
            os.environ.get("EVENTS_TABLE", f"{self._env}-signalfft-events")
        )
        self._poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
        self._running = True
        self._resolver = EntityResolver()

        graph_table = os.environ.get("GRAPH_EDGES_TABLE")
        if graph_table:
            from engine.memory_graph.writer import GraphWriter
            from engine.memory_graph.query import GraphQuery
            self._graph = GraphWriter(table_name=graph_table, region=self._region)
            self._graph_query = GraphQuery(table_name=graph_table, region=self._region)
            logger.info("HistoricalPattern enabled via graph table: %s", graph_table)
        else:
            logger.info("HistoricalPattern disabled — GRAPH_EDGES_TABLE not set")
            self._graph = None
            self._graph_query = None

    def run(self) -> None:
        logger.info("Signal scoring service starting")
        while self._running:
            try:
                messages = self._poll_messages()
                for msg in messages:
                    self.process_message(msg)
            except Exception:
                logger.exception("Error in poll cycle")
            time.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False

    def _poll_messages(self) -> list[dict]:
        response = self._sqs.receive_message(
            QueueUrl=self.input_queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
        )
        return response.get("Messages", [])

    def process_message(self, message: dict) -> None:
        receipt_handle = message["ReceiptHandle"]
        try:
            event = BaseEvent.from_sqs_message(message["Body"])
            payload = event.payload

            feature_id = payload["feature_id"]
            event_id = payload["event_id"]
            entity_id = payload["entity_id"]

            # Build components from feature data
            components, lexicon_polarity = self._build_components(event_id, entity_id)

            # Compute signal score
            score = compute_signal_score(components)

            # Compute direction score
            direction_score = compute_direction_score(lexicon_polarity, None, None)

            # Create signal record
            signal_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            signal = Signal(
                signal_id=signal_id,
                entity_id=entity_id,
                score=score,
                components=components,
                weight_version="default",
                attention_field_version="v1",
                created_at=now,
                direction_score=direction_score,
            )

            # Store signal
            self._store_signal(signal)

            # Emit signal event
            self._emit_signal_event(signal)

            # Write graph edges
            if self._graph:
                try:
                    self._graph.on_signal_created(
                        signal_id=signal.signal_id,
                        entity_id=signal.entity_id,
                        event_id=event_id,
                        score=signal.score,
                    )
                except Exception:
                    logger.exception("Graph write failed for signal %s", signal.signal_id)

            # Ack message
            self._sqs.delete_message(
                QueueUrl=self.input_queue_url,
                ReceiptHandle=receipt_handle,
            )

            logger.info("Scored signal %s: %.4f", signal_id, score)

        except Exception:
            logger.exception("Failed to process message %s", message.get("MessageId"))

    def _build_components(self, event_id: str, entity_id: str = "") -> tuple[dict[str, float], float]:
        """Fetch features for event and map to scorer component format.

        Maps raw feature types to the scoring model's expected components:
          ENTITY_MENTION  -> entity_sensitivity (from mention_count)
          SENTIMENT       -> semantic_impact (from polarity + magnitude)
          TEMPORAL_MARKER -> novelty (presence of temporal markers)
          Memory Graph    -> historical_pattern (from entity graph history)
          Events table    -> velocity (recent event count for entity)
          Events table    -> cross_source (distinct sources for entity)

        Returns:
            Tuple of (components dict, lexicon_polarity float).
        """
        response = self._features_table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"EVENT#{event_id}"},
        )
        items = response.get("Items", [])

        entity_sensitivity = 0.0
        semantic_impact = 0.0
        novelty = 0.0
        mention_count = 0
        lexicon_polarity = 0.0

        for item in items:
            ft = item.get("feature_type", "")
            val = item.get("value", {})
            if not isinstance(val, dict):
                continue

            if ft == "ENTITY_MENTION":
                mc = float(val.get("mention_count", 0))
                mention_count += int(mc)
                entity_sensitivity = min(1.0, entity_sensitivity + mc * 0.3)

            elif ft == "SENTIMENT":
                polarity = float(val.get("polarity", 0.0))
                magnitude = float(val.get("magnitude", 0.0))
                semantic_impact = min(1.0, abs(polarity) * magnitude + semantic_impact)
                lexicon_polarity = float(val.get("lexicon_polarity", 0.0))

            elif ft == "TEMPORAL_MARKER":
                novelty = min(1.0, novelty + 0.25)

        if novelty == 0.0 and len(items) > 0:
            novelty = 0.1

        historical_pattern = 0.0
        if self._graph_query and entity_id:
            try:
                historical_pattern = self._graph_query.get_entity_pattern_score(entity_id)
                logger.debug("HistoricalPattern for %s: %.4f", entity_id, historical_pattern)
            except Exception as e:
                logger.warning("Graph query failed for %s, using 0.0: %s", entity_id, e)
                historical_pattern = 0.0

        velocity = self._compute_velocity(entity_id) if entity_id else 0.0
        cross_source = self._compute_cross_source(entity_id) if entity_id else 0.0

        return {
            "novelty": round(novelty, 4),
            "velocity": round(velocity, 4),
            "cross_source": round(cross_source, 4),
            "semantic_impact": round(semantic_impact, 4),
            "entity_sensitivity": round(entity_sensitivity, 4),
            "historical_pattern": round(historical_pattern, 4),
            "noise_penalty": 0.0,
        }, lexicon_polarity

    def _compute_velocity(self, entity_id: str) -> float:
        """Compute event velocity for an entity.

        Counts events for this entity in the last hour. Also checks
        CIK-based partition keys for old EDGAR events.
        1 event = 0.0, 2 = 0.25, 3 = 0.5, 5+ = 1.0.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        normalized = self._resolver.normalize(entity_id)
        entity_ids_to_check = [normalized]
        if normalized != entity_id:
            entity_ids_to_check.append(entity_id)
        cik = self._resolver.ticker_to_cik(normalized)
        if cik:
            entity_ids_to_check.append(f"CIK_{cik}")
            entity_ids_to_check.append(cik)

        count = 0
        for eid in entity_ids_to_check:
            try:
                response = self._events_table.query(
                    KeyConditionExpression="PK = :pk AND SK > :sk",
                    ExpressionAttributeValues={
                        ":pk": f"ENTITY#{eid}",
                        ":sk": f"EVENT#{cutoff}",
                    },
                    Select="COUNT",
                )
                count += response.get("Count", 0)
            except Exception:
                logger.warning("Velocity query failed for %s, skipping", eid)

        if count <= 1:
            return 0.0
        return min(1.0, (count - 1) * 0.25)

    def _compute_cross_source(self, entity_id: str) -> float:
        """Compute cross-source correlation for an entity.

        Counts distinct event sources for this entity in the last 2 hours.
        Also checks CIK-based partition keys to match old EDGAR events
        that haven't been re-collected with ticker-based IDs yet.
        1 source = 0.0, 2 sources = 0.5, 3+ sources = 1.0.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        # Build list of entity ID forms to check (ticker + any CIK aliases)
        normalized = self._resolver.normalize(entity_id)
        entity_ids_to_check = [normalized]
        if normalized != entity_id:
            entity_ids_to_check.append(entity_id)
        cik = self._resolver.ticker_to_cik(normalized)
        if cik:
            entity_ids_to_check.append(f"CIK_{cik}")
            entity_ids_to_check.append(cik)

        sources: set[str] = set()
        for eid in entity_ids_to_check:
            try:
                response = self._events_table.query(
                    KeyConditionExpression="PK = :pk AND SK > :sk",
                    ExpressionAttributeValues={
                        ":pk": f"ENTITY#{eid}",
                        ":sk": f"EVENT#{cutoff}",
                    },
                    ProjectionExpression="#src",
                    ExpressionAttributeNames={"#src": "source"},
                )
                for item in response.get("Items", []):
                    src = item.get("source", "")
                    if src:
                        sources.add(src)
            except Exception:
                logger.warning("Cross-source query failed for %s, skipping", eid)

        n = len(sources)
        if n <= 1:
            return 0.0
        if n == 2:
            return 0.5
        return 1.0

    def _store_signal(self, signal: Signal) -> None:
        item = {
            "PK": build_signals_pk(signal.entity_id),
            "SK": build_signals_sk(signal.created_at, signal.signal_id),
            "signal_id": signal.signal_id,
            "entity_id": signal.entity_id,
            "score": Decimal(str(signal.score)),
            "components": json.loads(json.dumps(signal.components), parse_float=Decimal),
            "created_at": signal.created_at,
            "direction_score": Decimal(str(signal.direction_score)),
        }
        self._signals_table.put_item(Item=item)

    def _emit_signal_event(self, signal: Signal) -> None:
        from signalfft_common.events import SignalScored

        event = SignalScored(
            timestamp=signal.created_at,
            source="signal_scoring",
            trace_id=str(uuid.uuid4()),
            payload={
                "signal_id": signal.signal_id,
                "entity_id": signal.entity_id,
                "score": signal.score,
                "weight_version": signal.weight_version,
                "attention_field_version": signal.attention_field_version,
                "direction_score": signal.direction_score,
            },
        )
        msg_body = event.to_sqs_message()
        self._sqs.send_message(
            QueueUrl=self.output_queue_url,
            MessageBody=msg_body,
        )
        if self._risk_input_queue_url:
            try:
                self._sqs.send_message(
                    QueueUrl=self._risk_input_queue_url,
                    MessageBody=msg_body,
                )
            except Exception:
                logger.exception("Failed to publish to risk input queue")
        if self._outcome_tracking_queue_url:
            try:
                self._sqs.send_message(
                    QueueUrl=self._outcome_tracking_queue_url,
                    MessageBody=msg_body,
                )
            except Exception:
                logger.exception("Failed to publish to outcome tracking queue")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = SignalScoringService()
    service.run()
