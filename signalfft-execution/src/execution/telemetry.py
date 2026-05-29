"""
Records execution outcomes to DynamoDB and CloudWatch logs.
Each fill creates an Outcome record for the audit trail and memory graph feedback.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

logger = logging.getLogger(__name__)


class TelemetryRecorder:
    def __init__(self, table_name: str | None = None, region: str = "us-east-1"):
        """
        Initialize with DynamoDB table for execution telemetry.
        table_name defaults to env var EXECUTION_TELEMETRY_TABLE or "prod-signalfft-execution-telemetry".
        If table_name is explicitly empty string or None, operates in log-only mode.
        """
        self.table_name = table_name or os.environ.get(
            "EXECUTION_TELEMETRY_TABLE", "prod-signalfft-execution-telemetry"
        )
        self.region = region
        self._table = None

        if self.table_name:
            try:
                dynamodb = boto3.resource("dynamodb", region_name=self.region)
                self._table = dynamodb.Table(self.table_name)
                self._table.load()
            except Exception:
                logger.warning("DynamoDB table %s not available, running in log-only mode", self.table_name)
                self._table = None

    def record_fill(self, candidate_id: str, signal_id: str, entity_id: str, fill_result: dict) -> dict:
        """
        Create outcome record from fill result and optionally persist to DynamoDB.
        """
        outcome_id = str(uuid.uuid4())
        outcome = {
            "outcome_id": outcome_id,
            "candidate_id": candidate_id,
            "signal_id": signal_id,
            "entity_id": entity_id,
            "fill_price": fill_result["fill_price"],
            "slippage": fill_result["slippage"],
            "latency_ms": fill_result["latency_ms"],
            "direction": fill_result["direction"],
            "quantity": fill_result["quantity"],
            "status": fill_result["status"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Execution outcome: entity=%s fill_price=%s slippage=%s",
            entity_id,
            fill_result["fill_price"],
            fill_result["slippage"],
        )

        if self._table is not None:
            try:
                item = {
                    "PK": f"OUTCOME#{outcome_id}",
                    "SK": "META",
                    **outcome,
                }
                # DynamoDB requires Decimal instead of float
                for k, v in item.items():
                    if isinstance(v, float):
                        item[k] = Decimal(str(v))
                self._table.put_item(Item=item)
            except Exception:
                logger.exception("Failed to write outcome %s to DynamoDB", outcome_id)

        return outcome
