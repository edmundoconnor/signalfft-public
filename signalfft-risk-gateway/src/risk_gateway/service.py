"""
Risk Gateway SQS consumer service.
Consumes SignalScored events, generates trade candidates, applies risk checks,
and writes results to trade_candidates DynamoDB table.

Decision Plane service — reads from intelligence tables, writes to decision tables.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

import boto3

from signalfft_common.events import BaseEvent, TradeCandidateGenerated
from signalfft_common.dynamo.keys import build_trade_candidates_pk, build_trade_candidates_sk

from risk_gateway.rules import run_all_checks, RiskConfig
from risk_gateway.candidate_generator import generate_candidates
from risk_gateway.provenance import stamp_provenance

logger = logging.getLogger(__name__)

POSITION_SIZE = 1000.0
_NEUTRAL_THRESHOLD = 0.05


def _derive_direction(direction_score: float) -> str:
    """Derive LONG/SHORT/NEUTRAL from a direction score."""
    if direction_score > _NEUTRAL_THRESHOLD:
        return "LONG"
    if direction_score < -_NEUTRAL_THRESHOLD:
        return "SHORT"
    return "NEUTRAL"


class RiskGatewayService:
    """Long-running service that consumes SignalScored events and gates trade candidates."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "prod")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("INPUT_QUEUE_URL", "")
        self.output_queue_url = os.environ.get("OUTPUT_QUEUE_URL", "")

        self._signals_table = self._dynamo.Table(
            os.environ.get("SIGNALS_TABLE", f"{self._env}-signalfft-signals")
        )
        self._candidates_table = self._dynamo.Table(
            os.environ.get("TRADE_CANDIDATES_TABLE", f"{self._env}-signalfft-trade-candidates")
        )

        min_score = float(os.environ.get("MIN_SIGNAL_SCORE", "0.05"))
        max_per_window = int(os.environ.get("MAX_CANDIDATES_PER_WINDOW", "10"))
        self._config = RiskConfig(
            min_signal_score=min_score,
            max_candidates_per_window=max_per_window,
        )

        self._allow_short = os.environ.get("ALLOW_SHORT", "false").lower() == "true"
        self._running = True

    def _fetch_signal(self, signal_id: str, entity_id: str) -> dict | None:
        """Read full signal record from signals table."""
        response = self._signals_table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": f"ENTITY#{entity_id}",
                ":prefix": "SIGNAL#",
            },
        )
        for item in response.get("Items", []):
            if item.get("signal_id") == signal_id:
                return {
                    "signal_id": item["signal_id"],
                    "entity_id": item["entity_id"],
                    "score": float(item.get("score", 0)),
                }
        return None

    def _get_entity_exposure(self, entity_id: str) -> tuple[float, int]:
        """Query trade_candidates table for APPROVED candidates for this entity.
        Returns (total_exposure_for_entity, active_candidate_count).
        """
        response = self._candidates_table.scan(
            FilterExpression="entity_id = :eid AND risk_status = :status",
            ExpressionAttributeValues={
                ":eid": entity_id,
                ":status": "APPROVED",
            },
            ProjectionExpression="candidate_id",
        )
        count = response.get("Count", 0)
        return count * POSITION_SIZE, count

    def _get_total_exposure(self) -> float:
        """Count all APPROVED candidates for total exposure."""
        response = self._candidates_table.scan(
            FilterExpression="risk_status = :status",
            ExpressionAttributeValues={":status": "APPROVED"},
            Select="COUNT",
        )
        return response.get("Count", 0) * POSITION_SIZE

    def _get_window_candidate_count(self) -> int:
        """Count candidates created in the last 5 minutes."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        response = self._candidates_table.scan(
            FilterExpression="created_at >= :cutoff",
            ExpressionAttributeValues={":cutoff": cutoff},
            Select="COUNT",
        )
        return response.get("Count", 0)

    def _write_candidate(self, candidate: dict) -> None:
        """Write candidate to trade_candidates table."""
        item: dict[str, Any] = {
            "PK": build_trade_candidates_pk(candidate["candidate_id"]),
            "SK": build_trade_candidates_sk(),
        }
        for key, val in candidate.items():
            if val is None:
                continue
            if isinstance(val, float):
                item[key] = Decimal(str(val))
            else:
                item[key] = val
        self._candidates_table.put_item(Item=item)

    def _publish_approved(self, candidate: dict) -> None:
        """Publish TradeCandidateGenerated event if OUTPUT_QUEUE_URL is set."""
        if not self.output_queue_url:
            return
        event = TradeCandidateGenerated(
            timestamp=candidate["created_at"],
            source="risk_gateway",
            trace_id=str(uuid.uuid4()),
            payload={
                "candidate_id": candidate["candidate_id"],
                "signal_id": candidate["signal_id"],
                "entity_id": candidate["entity_id"],
                "score": candidate["score"],
                "risk_status": candidate["risk_status"],
                "direction": candidate.get("direction", ""),
                "direction_score": candidate.get("direction_score", 0.0),
            },
        )
        self._sqs.send_message(
            QueueUrl=self.output_queue_url,
            MessageBody=event.to_sqs_message(),
        )

    def process_batch(self, messages: list[dict]) -> list[dict]:
        """Process a batch of SignalScored messages through the risk pipeline."""
        signals: list[dict] = []
        for msg in messages:
            try:
                body = msg["Body"] if "Body" in msg else msg
                event = BaseEvent.from_sqs_message(body if isinstance(body, str) else json.dumps(body))
                payload = event.payload

                signals.append({
                    "signal_id": payload["signal_id"],
                    "entity_id": payload["entity_id"],
                    "score": float(payload["score"]),
                    "direction_score": float(payload.get("direction_score", 0.0)),
                })
            except Exception:
                logger.exception("Failed to parse SQS message")

        if not signals:
            return []

        candidates = generate_candidates(
            signals,
            min_score=self._config.min_signal_score,
        )

        approved = 0
        rejected = 0
        all_candidates: list[dict] = []

        for candidate in candidates:
            entity_id = candidate["entity_id"]

            entity_exposure, entity_count = self._get_entity_exposure(entity_id)
            total_exposure = self._get_total_exposure()
            window_count = self._get_window_candidate_count()

            passed, reason, checks = run_all_checks(
                score=candidate["score"],
                current_entity_exposure=entity_exposure,
                current_total_exposure=total_exposure,
                current_entity_candidate_count=entity_count,
                current_window_candidate_count=window_count,
                config=self._config,
            )

            if passed:
                candidate["risk_status"] = "APPROVED"
                candidate["risk_rejection_reason"] = None
                approved += 1
            else:
                candidate["risk_status"] = "REJECTED"
                candidate["risk_rejection_reason"] = reason
                rejected += 1

            # Derive direction from direction_score
            direction_score = candidate.get("direction_score", 0.0)
            candidate["direction"] = _derive_direction(direction_score)

            stamp_provenance(candidate)
            candidate["checks_performed"] = checks
            self._write_candidate(candidate)

            if passed:
                if candidate["direction"] == "SHORT" and not self._allow_short:
                    logger.info(
                        "Skipping SHORT candidate %s for %s (ALLOW_SHORT=false)",
                        candidate["candidate_id"], entity_id,
                    )
                else:
                    self._publish_approved(candidate)

            all_candidates.append(candidate)

        logger.info(
            "Processed %d signals -> %d approved, %d rejected",
            len(signals), approved, rejected,
        )
        return all_candidates

    def run(self) -> None:
        """Long-running SQS consumer loop with graceful SIGTERM shutdown."""
        def _handle_sigterm(signum: int, frame: Any) -> None:
            logger.info("Received SIGTERM, shutting down")
            self._running = False

        signal.signal(signal.SIGTERM, _handle_sigterm)
        logger.info("Risk gateway starting — consuming from %s", self.input_queue_url)

        while self._running:
            try:
                response = self._sqs.receive_message(
                    QueueUrl=self.input_queue_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20,
                )
                messages = response.get("Messages", [])
                if not messages:
                    continue

                self.process_batch(messages)

                for msg in messages:
                    try:
                        self._sqs.delete_message(
                            QueueUrl=self.input_queue_url,
                            ReceiptHandle=msg["ReceiptHandle"],
                        )
                    except Exception:
                        logger.exception("Failed to delete message %s", msg.get("MessageId"))

            except Exception:
                logger.exception("Error in poll cycle")
                time.sleep(5)

        logger.info("Risk gateway shutting down gracefully")
