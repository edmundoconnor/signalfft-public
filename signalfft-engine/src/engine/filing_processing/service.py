"""Section extractor ECS Fargate service.

Polls the filing-ready SQS queue for FilingDocumentReady events, extracts
named text sections from raw SEC filing HTML, stores sections in S3,
writes metadata to DynamoDB, and emits FilingSectionsReady events.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3

from signalfft_common.dynamo.keys import build_filing_sections_pk, build_filing_sections_sk
from signalfft_common.events import BaseEvent, FilingSectionsReady

from engine.filing_processing.extractor import extract_sections

logger = logging.getLogger(__name__)


class SectionExtractorService:
    """Long-running service that extracts sections from SEC filing HTML."""

    def __init__(self) -> None:
        self._region = os.environ.get("AWS_REGION", "us-east-1")
        self._env = os.environ.get("ENVIRONMENT", "dev")

        self._sqs = boto3.client("sqs", region_name=self._region)
        self._s3 = boto3.client("s3", region_name=self._region)
        self._dynamo = boto3.resource("dynamodb", region_name=self._region)

        self.input_queue_url = os.environ.get("FILING_READY_QUEUE_URL", "")
        self.output_queue_url = os.environ.get("SECTIONS_READY_QUEUE_URL", "")
        self.filing_indexer_queue_url = os.environ.get("FILING_INDEXER_QUEUE_URL", "")
        self.triage_input_queue_url = os.environ.get("TRIAGE_INPUT_QUEUE_URL", "")
        self._events_table = self._dynamo.Table(
            os.environ.get("EVENTS_TABLE", f"{self._env}-signalfft-events")
        )
        self._bucket = os.environ.get("ARTIFACT_BUCKET", f"{self._env}-signalfft-artifacts")
        self._poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
        self._running = True

    def run(self) -> None:
        """Main service loop -- poll, process, repeat."""
        logger.info("Section extractor service starting")
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
        """Poll SQS for FilingDocumentReady messages."""
        response = self._sqs.receive_message(
            QueueUrl=self.input_queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20,
        )
        return response.get("Messages", [])

    def process_message(self, message: dict, ack: bool = True) -> bool:
        """Process a single SQS message containing a FilingDocumentReady event."""
        receipt_handle = message["ReceiptHandle"]
        try:
            event = BaseEvent.from_sqs_message(message["Body"])
            payload = event.payload

            event_id = payload["event_id"]
            entity_id = payload["entity_id"]
            cik = payload["cik"]
            form_type = payload["form_type"]
            filing_date = payload["filing_date"]
            filing_s3_uri = payload["filing_s3_uri"]

            # 1. Fetch HTML from S3
            html_content = self._fetch_filing(filing_s3_uri)

            if not html_content.strip():
                logger.warning("Empty filing content for event %s", event_id)
                if ack:
                    self._sqs.delete_message(
                        QueueUrl=self.input_queue_url,
                        ReceiptHandle=receipt_handle,
                    )
                return True

            # 2. Extract sections (pure function)
            sections = extract_sections(html_content, form_type)

            if not sections:
                logger.warning("No sections extracted for event %s", event_id)
                if ack:
                    self._sqs.delete_message(
                        QueueUrl=self.input_queue_url,
                        ReceiptHandle=receipt_handle,
                    )
                return True

            # 3. Store sections to S3
            section_prefix = f"filings/{cik}/{form_type}/{filing_date}/sections"
            total_text_length = 0
            for section_name, text in sections.items():
                self._store_section(section_prefix, section_name, text)
                total_text_length += len(text)

            # 4. Write filing_sections record to DynamoDB
            self._store_sections_metadata(
                entity_id=entity_id,
                event_id=event_id,
                cik=cik,
                form_type=form_type,
                filing_date=filing_date,
                sections_available=list(sections.keys()),
                section_s3_prefix=f"s3://{self._bucket}/{section_prefix}",
                total_text_length=total_text_length,
            )

            # 5. Emit FilingSectionsReady
            self._emit_sections_ready(
                event_id=event_id,
                entity_id=entity_id,
                cik=cik,
                form_type=form_type,
                filing_date=filing_date,
                sections_available=list(sections.keys()),
                section_s3_prefix=f"s3://{self._bucket}/{section_prefix}",
                total_text_length=total_text_length,
            )

            # 5b. Fan-out: emit to filing-indexer queue
            self._emit_to_filing_indexer(
                event_id=event_id,
                entity_id=entity_id,
                cik=cik,
                form_type=form_type,
                filing_date=filing_date,
                sections_available=list(sections.keys()),
                section_s3_prefix=f"s3://{self._bucket}/{section_prefix}",
                total_text_length=total_text_length,
            )

            # 5c. Fan-out: emit to triage-input queue (Edge 1)
            self._emit_to_triage_input(
                event_id=event_id,
                entity_id=entity_id,
                cik=cik,
                form_type=form_type,
                filing_date=filing_date,
                sections_available=list(sections.keys()),
                section_s3_prefix=f"s3://{self._bucket}/{section_prefix}",
                total_text_length=total_text_length,
            )

            if ack:
                self._sqs.delete_message(
                    QueueUrl=self.input_queue_url,
                    ReceiptHandle=receipt_handle,
                )

            logger.info(
                "Processed filing %s: %d sections extracted (%d chars total)",
                event_id,
                len(sections),
                total_text_length,
            )
            return True

        except Exception:
            logger.exception(
                "Failed to process message %s",
                message.get("MessageId", "unknown"),
            )
            return False

    def _fetch_filing(self, s3_uri: str) -> str:
        """Fetch raw HTML from S3."""
        parts = s3_uri.replace("s3://", "").split("/", 1)
        bucket = parts[0]
        key = parts[1]

        response = self._s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read().decode("utf-8")

    def _store_section(self, prefix: str, section_name: str, text: str) -> None:
        """Store a single section as a text file in S3."""
        key = f"{prefix}/{section_name}.txt"
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType="text/plain",
        )

    def _store_sections_metadata(
        self,
        *,
        entity_id: str,
        event_id: str,
        cik: str,
        form_type: str,
        filing_date: str,
        sections_available: list[str],
        section_s3_prefix: str,
        total_text_length: int,
    ) -> None:
        """Write filing sections metadata to DynamoDB events table."""
        now = datetime.now(timezone.utc).isoformat()
        self._events_table.put_item(
            Item={
                "PK": build_filing_sections_pk(entity_id),
                "SK": build_filing_sections_sk(form_type, filing_date),
                "entity_id": entity_id,
                "event_id": event_id,
                "cik": cik,
                "form_type": form_type,
                "filing_date": filing_date,
                "sections_available": sections_available,
                "section_s3_prefix": section_s3_prefix,
                "total_text_length": total_text_length,
                "created_at": now,
                "source": "section_extractor",
            }
        )

    def _emit_sections_ready(
        self,
        *,
        event_id: str,
        entity_id: str,
        cik: str,
        form_type: str,
        filing_date: str,
        sections_available: list[str],
        section_s3_prefix: str,
        total_text_length: int,
    ) -> None:
        """Emit FilingSectionsReady event to the sections-ready queue."""
        if not self.output_queue_url:
            logger.debug("No output queue configured — skipping FilingSectionsReady emission")
            return

        now = datetime.now(timezone.utc).isoformat()
        event = FilingSectionsReady(
            timestamp=now,
            source="section_extractor",
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": event_id,
                "entity_id": entity_id,
                "cik": cik,
                "form_type": form_type,
                "filing_date": filing_date,
                "sections_available": sections_available,
                "section_s3_prefix": section_s3_prefix,
                "total_text_length": total_text_length,
            },
        )
        self._sqs.send_message(
            QueueUrl=self.output_queue_url,
            MessageBody=event.to_sqs_message(),
        )

    def _emit_to_filing_indexer(
        self,
        *,
        event_id: str,
        entity_id: str,
        cik: str,
        form_type: str,
        filing_date: str,
        sections_available: list[str],
        section_s3_prefix: str,
        total_text_length: int,
    ) -> None:
        """Fan-out: emit FilingSectionsReady to the filing-indexer queue."""
        if not self.filing_indexer_queue_url:
            logger.debug("No filing-indexer queue configured — skipping fan-out")
            return

        now = datetime.now(timezone.utc).isoformat()
        event = FilingSectionsReady(
            timestamp=now,
            source="section_extractor",
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": event_id,
                "entity_id": entity_id,
                "cik": cik,
                "form_type": form_type,
                "filing_date": filing_date,
                "sections_available": sections_available,
                "section_s3_prefix": section_s3_prefix,
                "total_text_length": total_text_length,
            },
        )
        try:
            self._sqs.send_message(
                QueueUrl=self.filing_indexer_queue_url,
                MessageBody=event.to_sqs_message(),
            )
        except Exception:
            logger.exception("Failed to fan-out to filing-indexer queue")

    def _emit_to_triage_input(
        self,
        *,
        event_id: str,
        entity_id: str,
        cik: str,
        form_type: str,
        filing_date: str,
        sections_available: list[str],
        section_s3_prefix: str,
        total_text_length: int,
    ) -> None:
        """Fan-out: emit FilingSectionsReady to the triage-input queue (Edge 1)."""
        if not self.triage_input_queue_url:
            logger.debug("No triage-input queue configured — skipping fan-out")
            return

        now = datetime.now(timezone.utc).isoformat()
        event = FilingSectionsReady(
            timestamp=now,
            source="section_extractor",
            trace_id=str(uuid.uuid4()),
            payload={
                "event_id": event_id,
                "entity_id": entity_id,
                "cik": cik,
                "form_type": form_type,
                "filing_date": filing_date,
                "sections_available": sections_available,
                "section_s3_prefix": section_s3_prefix,
                "total_text_length": total_text_length,
            },
        )
        try:
            self._sqs.send_message(
                QueueUrl=self.triage_input_queue_url,
                MessageBody=event.to_sqs_message(),
            )
        except Exception:
            logger.exception("Failed to fan-out to triage-input queue")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    service = SectionExtractorService()
    service.run()
