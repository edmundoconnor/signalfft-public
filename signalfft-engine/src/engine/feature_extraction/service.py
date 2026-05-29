"""Feature extraction ECS Fargate service.

Polls raw-events SQS queue, extracts features, writes to DynamoDB,
emits FeatureExtracted events.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

from signalfft_common.dynamo.keys import build_features_pk, build_features_sk
from signalfft_common.enums import FeatureType
from signalfft_common.events import BaseEvent, FeatureExtracted, HighPriorityFiling
from signalfft_common.models import Feature

from engine.feature_extraction.extractor import extract_features

logger = logging.getLogger(__name__)


class FeatureExtractionService:
    """Long-running service that processes raw events and extracts features."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._s3 = boto3.client("s3", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("RAW_EVENTS_QUEUE_URL", "")
        self.output_queue_url = os.environ.get("FEATURES_QUEUE_URL", "")
        # NOTE: HIGH_PRIORITY_QUEUE_URL requires a new SQS queue in Terraform
        self.high_priority_queue_url = os.environ.get("HIGH_PRIORITY_QUEUE_URL", "")
        self._features_table = self._dynamo.Table(
            os.environ.get("FEATURES_TABLE", f"{self._env}-signalfft-features")
        )
        self._poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
        self._max_messages = int(os.environ.get("MAX_MESSAGES_PER_POLL", "10"))
        self._running = True

    def run(self) -> None:
        """Main service loop -- poll, process, repeat."""
        logger.info("Feature extraction service starting")
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
        """Poll SQS for raw event messages."""
        response = self._sqs.receive_message(
            QueueUrl=self.input_queue_url,
            MaxNumberOfMessages=self._max_messages,
            WaitTimeSeconds=20,
        )
        return response.get("Messages", [])

    def process_message(self, message: dict) -> None:
        """Process a single SQS message."""
        receipt_handle = message["ReceiptHandle"]
        try:
            event = BaseEvent.from_sqs_message(message["Body"])
            payload = event.payload

            event_id = payload["event_id"]
            entity_id = payload["entity_id"]
            source = payload.get("source", "")
            s3_uri = payload["raw_artifact_s3"]

            # Fetch raw content from S3
            content = self._fetch_artifact(s3_uri)

            # Extract features (pure function)
            features = extract_features(event_id, entity_id, content, source=source)

            # Store features and emit events
            for feature in features:
                self._store_feature(feature)
                self._emit_feature_event(feature)

            # Publish HighPriorityFiling if triage matched
            triage_features = [
                f for f in features if f.feature_type == FeatureType.TRIAGE
            ]
            if triage_features and self.high_priority_queue_url:
                self._emit_high_priority_event(
                    triage_features[0], event_id, entity_id, event.trace_id,
                )

            # Delete message from queue
            self._sqs.delete_message(
                QueueUrl=self.input_queue_url,
                ReceiptHandle=receipt_handle,
            )

            logger.info(
                "Processed event %s: %d features extracted",
                event_id,
                len(features),
            )

        except Exception:
            logger.exception(
                "Failed to process message %s",
                message.get("MessageId", "unknown"),
            )

    def _fetch_artifact(self, s3_uri: str) -> dict:
        """Fetch and parse raw artifact from S3."""
        # Parse s3://bucket/key
        parts = s3_uri.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1]

        response = self._s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read().decode("utf-8")
        return json.loads(body)

    def _store_feature(self, feature: Feature) -> None:
        """Write a Feature to DynamoDB."""
        item = asdict(feature)
        # Convert FeatureType enum to string
        ft = item.get("feature_type")
        if hasattr(ft, "value"):
            item["feature_type"] = ft.value
        elif not isinstance(ft, str):
            item["feature_type"] = str(ft)

        # DynamoDB requires Decimal instead of float
        item["value"] = _floats_to_decimals(item.get("value", {}))

        item["PK"] = build_features_pk(feature.event_id)
        item["SK"] = build_features_sk(feature.feature_id)
        self._features_table.put_item(Item=item)

    def _emit_feature_event(self, feature: Feature) -> None:
        """Emit FeatureExtracted event to the features SQS queue."""
        now = datetime.now(timezone.utc).isoformat()

        # Get feature_type as string
        ft = feature.feature_type
        feature_type_str = ft.value if hasattr(ft, "value") else str(ft)

        event = FeatureExtracted(
            timestamp=now,
            source="feature_extraction",
            trace_id=str(uuid.uuid4()),
            payload={
                "feature_id": feature.feature_id,
                "event_id": feature.event_id,
                "entity_id": feature.entity_id,
                "feature_type": feature_type_str,
            },
        )
        self._sqs.send_message(
            QueueUrl=self.output_queue_url,
            MessageBody=event.to_sqs_message(),
        )

    def _emit_high_priority_event(
        self,
        triage_feature: Feature,
        event_id: str,
        entity_id: str,
        trace_id: str,
    ) -> None:
        """Emit HighPriorityFiling event to the high-priority SQS queue."""
        now = datetime.now(timezone.utc).isoformat()
        value = triage_feature.value

        event = HighPriorityFiling(
            timestamp=now,
            source="feature_extraction",
            trace_id=trace_id,
            payload={
                "event_id": event_id,
                "entity_id": entity_id,
                "priority_level": value.get("priority_level", "MEDIUM"),
                "matched_categories": value.get("matched_categories", []),
                "matched_terms": value.get("matched_terms", []),
            },
        )
        self._sqs.send_message(
            QueueUrl=self.high_priority_queue_url,
            MessageBody=event.to_sqs_message(),
        )


def _floats_to_decimals(obj: Any) -> Any:
    """Recursively convert float values to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _floats_to_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimals(v) for v in obj]
    return obj


# For use with the Dockerfile ENTRYPOINT
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = FeatureExtractionService()
    service.run()
