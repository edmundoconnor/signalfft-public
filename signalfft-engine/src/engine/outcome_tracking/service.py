"""Outcome tracking ECS Fargate service.

Polls the signals SQS queue for SignalScored events, captures price snapshots
via the Alpaca market data API, and writes outcome records to DynamoDB.
"""

from __future__ import annotations

import logging
import os
import time

import boto3
from alpaca.data.historical import StockHistoricalDataClient

from signalfft_common.events import BaseEvent

from engine.outcome_tracking.price_snapshot import capture_price_snapshot

logger = logging.getLogger(__name__)


class OutcomeTrackingService:
    """Long-running service that captures price snapshots for scored signals."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("OUTCOME_TRACKING_QUEUE_URL", "")
        self._outcomes_table = self._dynamo.Table(
            os.environ.get("OUTCOMES_TABLE", f"{self._env}-signalfft-outcomes")
        )
        self._poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
        self._running = True

        # Alpaca market data client (same keys as trading)
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if api_key and secret_key:
            self._data_client = StockHistoricalDataClient(
                api_key=api_key,
                secret_key=secret_key,
            )
            logger.info("Alpaca market data client initialized")
        else:
            self._data_client = None
            logger.warning("Alpaca API keys not configured — price capture disabled")

    def run(self) -> None:
        """Main service loop -- poll, process, repeat."""
        logger.info("Outcome tracking service starting")
        while self._running:
            try:
                messages = self._poll_messages()
                for msg in messages:
                    self.process_message(msg)
            except Exception:
                logger.exception("Error in poll cycle")
            time.sleep(self._poll_interval)

    def stop(self) -> None:
        """Signal the service to stop."""
        self._running = False

    def _poll_messages(self) -> list[dict]:
        """Poll SQS for SignalScored messages."""
        response = self._sqs.receive_message(
            QueueUrl=self.input_queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
        )
        return response.get("Messages", [])

    def process_message(self, message: dict) -> None:
        """Process a single SQS message containing a SignalScored event."""
        receipt_handle = message["ReceiptHandle"]
        try:
            event = BaseEvent.from_sqs_message(message["Body"])
            payload = event.payload

            signal_id = payload["signal_id"]
            entity_id = payload["entity_id"]
            signal_score = float(payload["score"])
            direction_score = float(payload.get("direction_score", 0.0))
            signal_timestamp = event.timestamp

            if self._data_client is None:
                logger.warning(
                    "Skipping price capture for %s — Alpaca client not configured",
                    signal_id,
                )
                self._sqs.delete_message(
                    QueueUrl=self.input_queue_url,
                    ReceiptHandle=receipt_handle,
                )
                return

            capture_price_snapshot(
                data_client=self._data_client,
                outcomes_table=self._outcomes_table,
                signal_id=signal_id,
                entity_id=entity_id,
                signal_timestamp=signal_timestamp,
                signal_score=signal_score,
                direction_score=direction_score,
            )

            self._sqs.delete_message(
                QueueUrl=self.input_queue_url,
                ReceiptHandle=receipt_handle,
            )

            logger.info("Processed signal %s for entity %s", signal_id, entity_id)

        except Exception:
            logger.exception(
                "Failed to process message %s", message.get("MessageId", "unknown")
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = OutcomeTrackingService()
    service.run()
