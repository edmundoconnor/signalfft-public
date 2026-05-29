"""Base collector framework for SignalFFT data ingestion."""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BaseCollector(abc.ABC):
    """Abstract base for all SignalFFT data collectors.

    Subclasses must implement:
    - source_name: class-level string identifying the data source
    - collect() -> list of raw documents from the external source
    - extract_entity_id(doc) -> entity ID for a document
    - extract_event_type(doc) -> event type string
    """

    source_name: str  # e.g. "SEC_EDGAR", "NEWS_RSS"

    def __init__(self):
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")
        self._s3 = boto3.client("s3", region_name=self._region)
        self._sqs = boto3.client("sqs", region_name=self._region)
        self._dynamo_resource = boto3.resource("dynamodb", region_name=self._region)

        self._bucket = (
            os.environ.get("ARTIFACTS_BUCKET")
            or os.environ.get("ARTIFACT_BUCKET")
            or f"{self._env}-signalfft-artifacts"
        )
        logger.info("Resolved S3 bucket: %s", self._bucket)
        self._queue_url = os.environ.get("RAW_EVENTS_QUEUE_URL", "")
        self._events_table = self._dynamo_resource.Table(
            os.environ.get("EVENTS_TABLE", f"{self._env}-signalfft-events")
        )

    @abc.abstractmethod
    def collect(self) -> list[dict[str, Any]]:
        """Fetch raw documents from external source. Each dict represents one raw document."""

    @abc.abstractmethod
    def extract_entity_id(self, doc: dict[str, Any]) -> str:
        """Extract or derive the entity_id for a raw document."""

    @abc.abstractmethod
    def extract_event_type(self, doc: dict[str, Any]) -> str:
        """Return the event type string (e.g. 'SEC_10K', 'NEWS_ARTICLE')."""

    def on_event_stored(self, event_id: str, entity_id: str, doc: dict[str, Any]) -> None:
        """Hook called after event stored and emitted. Override in subclasses."""

    def content_hash(self, doc: dict[str, Any]) -> str:
        """Compute SHA-256 content hash for deduplication."""
        serialized = json.dumps(doc, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def is_duplicate(self, content_hash: str) -> bool:
        """Check if an event with this content hash already exists in DynamoDB.

        Uses a Scan with FilterExpression. For production scale, consider
        a GSI on content_hash.
        """
        response = self._events_table.scan(
            FilterExpression="content_hash = :ch",
            ExpressionAttributeValues={":ch": content_hash},
            Limit=1,
        )
        return len(response.get("Items", [])) > 0

    def store_artifact(self, event_id: str, doc: dict[str, Any]) -> str:
        """Store raw document in S3 and return the S3 URI."""
        key = f"raw/{self.source_name}/{event_id}.json"
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(doc, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return f"s3://{self._bucket}/{key}"

    def emit_event(self, event_id: str, entity_id: str, source: str,
                   content_hash: str, raw_artifact_s3: str) -> None:
        """Send RawEventCollected event to SQS queue."""
        from signalfft_common.events import RawEventCollected

        now = datetime.now(timezone.utc).isoformat()
        event = RawEventCollected(
            timestamp=now,
            source=self.source_name,
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": event_id,
                "entity_id": entity_id,
                "source": source,
                "content_hash": content_hash,
                "raw_artifact_s3": raw_artifact_s3,
            },
        )
        self._sqs.send_message(
            QueueUrl=self._queue_url,
            MessageBody=event.to_sqs_message(),
        )

    def store_event_record(self, event_id: str, entity_id: str,
                           event_type: str, content_hash: str,
                           raw_artifact_s3: str, timestamp: str) -> None:
        """Write the Event record to DynamoDB events table."""
        from signalfft_common.dynamo.keys import build_events_pk, build_events_sk

        self._events_table.put_item(
            Item={
                "PK": build_events_pk(entity_id),
                "SK": build_events_sk(timestamp, event_id),
                "event_id": event_id,
                "entity_id": entity_id,
                "source": self.source_name,
                "raw_artifact_s3": raw_artifact_s3,
                "event_type": event_type,
                "content_hash": content_hash,
                "created_at": timestamp,
            }
        )

    def run(self) -> dict[str, Any]:
        """Execute the full collection pipeline.

        Returns a summary dict with counts of processed, duplicates, errors.
        """
        now = datetime.now(timezone.utc).isoformat()
        stats = {"collected": 0, "stored": 0, "duplicates": 0, "errors": 0}

        try:
            docs = self.collect()
        except Exception:
            logger.exception("Failed to collect from %s", self.source_name)
            stats["errors"] = 1
            return stats

        stats["collected"] = len(docs)

        for doc in docs:
            try:
                ch = self.content_hash(doc)
                if self.is_duplicate(ch):
                    stats["duplicates"] += 1
                    logger.debug("Duplicate skipped: %s", ch[:12])
                    continue

                event_id = str(uuid.uuid4())
                entity_id = self.extract_entity_id(doc)
                event_type = self.extract_event_type(doc)

                # Store raw artifact in S3
                s3_uri = self.store_artifact(event_id, doc)

                # Write event record to DynamoDB
                self.store_event_record(
                    event_id=event_id,
                    entity_id=entity_id,
                    event_type=event_type,
                    content_hash=ch,
                    raw_artifact_s3=s3_uri,
                    timestamp=now,
                )

                # Emit SQS event
                self.emit_event(
                    event_id=event_id,
                    entity_id=entity_id,
                    source=self.source_name,
                    content_hash=ch,
                    raw_artifact_s3=s3_uri,
                )

                stats["stored"] += 1

                try:
                    self.on_event_stored(event_id=event_id, entity_id=entity_id, doc=doc)
                except Exception:
                    logger.warning("on_event_stored hook failed for %s", event_id, exc_info=True)

            except Exception:
                logger.exception("Error processing document")
                stats["errors"] += 1

        logger.info(
            "%s collection complete: %s",
            self.source_name,
            json.dumps(stats),
        )
        return stats


def make_lambda_handler(collector_class: type[BaseCollector]):
    """Factory that returns a Lambda handler function for a collector class."""
    def lambda_handler(event: dict, context: Any) -> dict:
        collector = collector_class()
        result = collector.run()
        return {
            "statusCode": 200,
            "body": json.dumps(result),
        }
    return lambda_handler
