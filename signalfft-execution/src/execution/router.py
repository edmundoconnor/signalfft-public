"""
Execution Router — consumes approved trade candidates from SQS and routes to broker adapter.
Execution Plane service: reads from candidates queue, writes to execution telemetry.
"""

import json
import logging
import os
import signal
import time

import boto3
from datetime import datetime, timezone

from execution.adapters.paper_trade import PaperTradeBroker
from execution.telemetry import TelemetryRecorder
from signalfft_common.graph.writer import GraphWriter

logger = logging.getLogger(__name__)


class ExecutionRouter:
    def __init__(self):
        """
        Initialize from environment variables:
        - INPUT_QUEUE_URL: SQS queue with TradeCandidateGenerated events
        - BROKER_MODE: "paper" (default) — selects which BrokerAdapter to use
        - EXECUTION_TELEMETRY_TABLE: (optional) DynamoDB table for telemetry records
        """
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._input_queue_url = os.environ.get("INPUT_QUEUE_URL", "")
        self._broker_mode = os.environ.get("BROKER_MODE", "paper")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._broker = self._get_broker()
        self._telemetry = TelemetryRecorder(region=self._region)
        self._running = True

        graph_table = os.environ.get("GRAPH_EDGES_TABLE")
        if graph_table:
            self._graph_writer = GraphWriter(table_name=graph_table, region=self._region)
            logger.info("Outcome feedback enabled via graph table: %s", graph_table)
        else:
            self._graph_writer = None
            logger.info("Outcome feedback disabled — GRAPH_EDGES_TABLE not set")

    def _get_broker(self):
        """Select broker based on BROKER_MODE."""
        mode = os.environ.get("BROKER_MODE", "paper")
        if mode == "paper":
            return PaperTradeBroker()
        if mode == "alpaca":
            from execution.adapters.alpaca_broker import AlpacaBroker

            broker = AlpacaBroker()
            if broker.enabled:
                try:
                    acct = broker.get_account()
                    logger.info(
                        "Alpaca account: buying_power=$%s portfolio_value=$%s",
                        acct.get("buying_power"),
                        acct.get("portfolio_value"),
                    )
                except Exception:
                    logger.warning("Could not fetch Alpaca account info", exc_info=True)
            return broker
        raise ValueError(f"Unsupported broker mode: {mode}. Supported: 'paper', 'alpaca'.")

    def process_candidate(self, message_body: dict) -> dict | None:
        """
        Process a single TradeCandidateGenerated event:
        1. Parse candidate fields from payload.
        2. Build order and submit to broker.
        3. Record telemetry.
        4. Return outcome dict.
        """
        payload = message_body.get("payload", message_body)
        candidate_id = payload["candidate_id"]
        entity_id = payload["entity_id"]
        signal_id = payload.get("signal_id", "")
        direction = payload.get("direction", "")

        if direction == "SHORT":
            logger.info("Skipping SHORT candidate %s for %s", candidate_id, entity_id)
            return None
        if direction == "NEUTRAL":
            logger.info("Skipping NEUTRAL candidate %s for %s", candidate_id, entity_id)
            return None

        order = {
            "candidate_id": candidate_id,
            "entity_id": entity_id,
            "direction": "BUY",
            "quantity": 100,
            "order_type": "MARKET",
            "limit_price": 100.0,
        }

        fill_result = self._broker.submit_order(order)

        # Skip telemetry and graph feedback for non-tradeable or failed orders
        if fill_result.get("status") == "SKIPPED":
            logger.info("Skipped non-tradeable entity: %s (%s)", entity_id, fill_result.get("error", ""))
            return None

        outcome = self._telemetry.record_fill(
            candidate_id=candidate_id,
            signal_id=signal_id,
            entity_id=entity_id,
            fill_result=fill_result,
        )

        logger.info(
            "Executed %s %s %s @ %s (slippage: %s, status: %s)",
            fill_result["direction"],
            fill_result["quantity"],
            entity_id,
            fill_result["fill_price"],
            fill_result["slippage"],
            fill_result["status"],
        )

        # Outcome feedback to Memory Graph
        if self._graph_writer and outcome:
            try:
                self._graph_writer.link_signal_outcome(
                    signal_id=signal_id,
                    outcome_id=outcome["outcome_id"],
                    metadata={
                        "fill_price": str(outcome["fill_price"]),
                        "slippage": str(outcome["slippage"]),
                        "direction": outcome["direction"],
                        "candidate_id": candidate_id,
                    },
                )
                logger.info(
                    "Outcome %s fed back to memory graph for signal %s",
                    outcome["outcome_id"],
                    signal_id,
                )
            except Exception:
                logger.warning(
                    "Graph feedback failed for outcome %s",
                    outcome.get("outcome_id", "?"),
                    exc_info=True,
                )

        return outcome

    def run(self):
        """
        Long-running SQS consumer loop with graceful SIGTERM shutdown.
        """
        def _handle_sigterm(signum, frame):
            logger.info("Received SIGTERM, shutting down")
            self._running = False

        signal.signal(signal.SIGTERM, _handle_sigterm)

        if self._broker_mode == "alpaca":
            from execution.adapters.alpaca_broker import AlpacaBroker

            notional = self._broker.notional if isinstance(self._broker, AlpacaBroker) else "?"
            logger.info("Execution router started in alpaca mode (paper trading, $%s/trade)", notional)
        else:
            logger.info("Execution router started in %s mode", self._broker_mode)

        while self._running:
            try:
                response = self._sqs.receive_message(
                    QueueUrl=self._input_queue_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20,
                )
                messages = response.get("Messages", [])

                for msg in messages:
                    try:
                        body = json.loads(msg["Body"])
                        self.process_candidate(body)
                        self._sqs.delete_message(
                            QueueUrl=self._input_queue_url,
                            ReceiptHandle=msg["ReceiptHandle"],
                        )
                    except Exception:
                        logger.warning("Failed to process message %s", msg.get("MessageId"), exc_info=True)

            except Exception:
                logger.exception("Error in poll cycle")
                time.sleep(5)

        logger.info("Execution router shutting down gracefully")
