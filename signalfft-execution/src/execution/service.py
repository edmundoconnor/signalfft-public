"""Execution router ECS Fargate service.

Order routing and broker adapter for SignalFFT (paper-trade first).
Polls candidates queue and simulates order execution.
"""

from __future__ import annotations

import json
import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)


class ExecutionRouterService:
    """Routes trade candidates to broker (paper-trade mode)."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)

        self._input_queue_url = os.environ.get("CANDIDATES_QUEUE_URL", "")
        self._poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
        self._paper_trade = os.environ.get("PAPER_TRADE", "true").lower() == "true"
        self._running = True

    def run(self) -> None:
        logger.info("Execution router starting (paper_trade=%s)", self._paper_trade)
        while self._running:
            try:
                messages = self._poll_messages()
                for msg in messages:
                    self._process_message(msg)
            except Exception:
                logger.exception("Error in poll cycle")
            time.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False

    def _poll_messages(self) -> list[dict]:
        response = self._sqs.receive_message(
            QueueUrl=self._input_queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
        )
        return response.get("Messages", [])

    def _process_message(self, message: dict) -> None:
        receipt_handle = message["ReceiptHandle"]
        try:
            body = json.loads(message["Body"])
            candidate_id = body.get("candidate_id", "")
            entity_id = body.get("entity_id", "")
            score = body.get("score", 0.0)

            if self._paper_trade:
                logger.info(
                    "[PAPER] Would execute trade for %s (candidate=%s, score=%.4f)",
                    entity_id, candidate_id, score,
                )
            else:
                logger.warning("Live trading not implemented")

            self._sqs.delete_message(
                QueueUrl=self._input_queue_url,
                ReceiptHandle=receipt_handle,
            )

        except Exception:
            logger.exception("Failed to process message %s", message.get("MessageId"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = ExecutionRouterService()
    service.run()
