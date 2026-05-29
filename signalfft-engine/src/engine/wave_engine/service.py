"""Wave engine ECS Fargate service.

Monitors signals over time windows and detects wave patterns.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

from signalfft_common.events import BaseEvent

logger = logging.getLogger(__name__)


class WaveEngineService:
    """Detects signal waves by aggregating signals over time windows."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("SIGNALS_QUEUE_URL", "")
        self.output_queue_url = os.environ.get("WAVES_QUEUE_URL", "")
        self._waves_table = self._dynamo.Table(
            os.environ.get("WAVES_TABLE", f"{self._env}-signalfft-waves")
        )
        self._signals_table = self._dynamo.Table(
            os.environ.get("SIGNALS_TABLE", f"{self._env}-signalfft-signals")
        )
        self._poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "10"))
        self._running = True

        graph_table = os.environ.get("GRAPH_EDGES_TABLE")
        if graph_table:
            from engine.memory_graph.writer import GraphWriter
            self._graph = GraphWriter(table_name=graph_table, region=self._region)
        else:
            logger.debug("Graph writes disabled — GRAPH_EDGES_TABLE not set")
            self._graph = None

    def run(self) -> None:
        logger.info("Wave engine service starting")
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

    def process_message(self, message: dict, ack: bool = True) -> bool:
        receipt_handle = message["ReceiptHandle"]
        try:
            event = BaseEvent.from_sqs_message(message["Body"])
            payload = event.payload

            entity_id = payload.get("entity_id", "")
            signal_id = payload.get("signal_id", "")
            score = float(payload.get("score", 0.0))

            # Create wave record
            wave_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()

            self._waves_table.put_item(
                Item={
                    "PK": f"ENTITY#{entity_id}",
                    "SK": f"WAVE#{now}#{wave_id}",
                    "wave_id": wave_id,
                    "entity_id": entity_id,
                    "signal_id": signal_id,
                    "amplitude": Decimal(str(score)),
                    "created_at": now,
                }
            )

            # Write graph edges
            if self._graph:
                try:
                    self._graph.on_wave_created(
                        wave_id=wave_id,
                        entity_id=entity_id,
                        signal_ids=[signal_id],
                    )
                except Exception:
                    logger.exception("Graph write failed for wave %s", wave_id)

            if ack:
                self._sqs.delete_message(
                    QueueUrl=self.input_queue_url,
                    ReceiptHandle=receipt_handle,
                )

            logger.info("Wave %s recorded for entity %s", wave_id, entity_id)
            return True

        except Exception:
            logger.exception("Failed to process message %s", message.get("MessageId"))
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = WaveEngineService()
    service.run()
