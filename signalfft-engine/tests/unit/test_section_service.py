"""Tests for SectionExtractorService with moto mocked AWS services."""

from __future__ import annotations

import json
import os
import uuid

import boto3
import pytest
from moto import mock_aws

from signalfft_common.events import BaseEvent, FilingDocumentReady, FilingSectionsReady


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_10K_HTML = """
<html><body>
<h2>Item 1. Business</h2>
<p>We are a technology company providing cloud services globally.</p>
<p>Our products include analytics and machine learning platforms.</p>

<h2>Item 1A. Risk Factors</h2>
<p>Investing in our securities involves significant risk.</p>
<p>Competition in the cloud market is intense.</p>

<h2>Item 7. Management's Discussion and Analysis</h2>
<p>Revenue increased 20% year over year driven by enterprise adoption.</p>
</body></html>
"""


@pytest.fixture
def aws_env():
    with mock_aws():
        region = "us-east-1"
        env = "test"
        os.environ["AWS_REGION"] = region
        os.environ["ENVIRONMENT"] = env
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"

        bucket = f"{env}-signalfft-artifacts"
        os.environ["ARTIFACT_BUCKET"] = bucket

        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=bucket)

        sqs = boto3.client("sqs", region_name=region)
        input_q = sqs.create_queue(QueueName="test-filing-ready")
        output_q = sqs.create_queue(QueueName="test-sections-ready")
        os.environ["FILING_READY_QUEUE_URL"] = input_q["QueueUrl"]
        os.environ["SECTIONS_READY_QUEUE_URL"] = output_q["QueueUrl"]

        dynamodb = boto3.client("dynamodb", region_name=region)
        table_name = f"{env}-signalfft-events"
        os.environ["EVENTS_TABLE"] = table_name
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "region": region,
            "bucket": bucket,
            "input_queue_url": input_q["QueueUrl"],
            "output_queue_url": output_q["QueueUrl"],
            "table_name": table_name,
        }


def _make_service(aws_env):
    """Create a SectionExtractorService after env vars are set."""
    from engine.filing_processing.service import SectionExtractorService
    return SectionExtractorService()


def _upload_filing(aws_env, key: str, html: str) -> str:
    """Upload HTML to S3 and return s3:// URI."""
    s3 = boto3.client("s3", region_name=aws_env["region"])
    s3.put_object(
        Bucket=aws_env["bucket"],
        Key=key,
        Body=html.encode("utf-8"),
    )
    return f"s3://{aws_env['bucket']}/{key}"


def _make_filing_ready_message(
    event_id: str, entity_id: str, s3_uri: str,
    cik: str = "1234567", form_type: str = "10-K", filing_date: str = "2026-02-15",
) -> dict:
    """Create a mock SQS message containing a FilingDocumentReady event."""
    event = FilingDocumentReady(
        timestamp="2026-02-15T00:00:00+00:00",
        source="filing-fetcher",
        trace_id=str(uuid.uuid4()),
        payload={
            "event_id": event_id,
            "entity_id": entity_id,
            "filing_s3_uri": s3_uri,
            "form_type": form_type,
            "filing_date": filing_date,
            "cik": cik,
        },
    )
    return {
        "MessageId": str(uuid.uuid4()),
        "ReceiptHandle": "test-receipt-handle",
        "Body": event.to_sqs_message(),
    }


# ===========================================================================
# Service tests
# ===========================================================================


class TestFetchFiling:
    """Tests for S3 HTML retrieval."""

    def test_fetch_filing_returns_html(self, aws_env):
        """Service should fetch HTML string from S3."""
        service = _make_service(aws_env)
        s3_uri = _upload_filing(aws_env, "filings/test/raw.html", "<html><body>Hello</body></html>")
        result = service._fetch_filing(s3_uri)
        assert "<html>" in result
        assert "Hello" in result


class TestStoreSection:
    """Tests for S3 section storage."""

    def test_correct_s3_path_and_content_type(self, aws_env):
        """Section should be stored at prefix/name.txt with text/plain content type."""
        service = _make_service(aws_env)
        service._store_section("filings/1234567/10-K/2026-02-15/sections", "item_1", "Business text")

        s3 = boto3.client("s3", region_name=aws_env["region"])
        response = s3.get_object(
            Bucket=aws_env["bucket"],
            Key="filings/1234567/10-K/2026-02-15/sections/item_1.txt",
        )
        assert response["ContentType"] == "text/plain"
        body = response["Body"].read().decode("utf-8")
        assert body == "Business text"


class TestStoreSectionsMetadata:
    """Tests for DynamoDB metadata storage."""

    def test_correct_pk_sk_and_fields(self, aws_env):
        """Metadata record should have correct PK/SK and all required fields."""
        service = _make_service(aws_env)
        service._store_sections_metadata(
            entity_id="AAPL",
            event_id="evt-001",
            cik="0000320193",
            form_type="10-K",
            filing_date="2026-02-15",
            sections_available=["item_1", "item_1a", "item_7"],
            section_s3_prefix="s3://bucket/filings/0000320193/10-K/2026-02-15/sections",
            total_text_length=50000,
        )

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(
            Key={
                "PK": "ENTITY#AAPL",
                "SK": "SECTIONS#10-K#2026-02-15",
            }
        )
        item = response["Item"]
        assert item["entity_id"] == "AAPL"
        assert item["event_id"] == "evt-001"
        assert item["cik"] == "0000320193"
        assert item["form_type"] == "10-K"
        assert item["sections_available"] == ["item_1", "item_1a", "item_7"]
        assert item["total_text_length"] == 50000
        assert item["source"] == "section_extractor"
        assert "created_at" in item


class TestEmitSectionsReady:
    """Tests for SQS event emission."""

    def test_sqs_message_format(self, aws_env):
        """FilingSectionsReady event should be deserializable from SQS."""
        service = _make_service(aws_env)
        service._emit_sections_ready(
            event_id="evt-001",
            entity_id="AAPL",
            cik="0000320193",
            form_type="10-K",
            filing_date="2026-02-15",
            sections_available=["item_1", "item_1a"],
            section_s3_prefix="s3://bucket/prefix",
            total_text_length=25000,
        )

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        assert len(messages) == 1

        event = BaseEvent.from_sqs_message(messages[0]["Body"])
        assert isinstance(event, FilingSectionsReady)
        assert event.payload["event_id"] == "evt-001"
        assert event.payload["entity_id"] == "AAPL"
        assert event.payload["sections_available"] == ["item_1", "item_1a"]
        assert event.payload["total_text_length"] == 25000

    def test_skips_without_queue_url(self, aws_env):
        """When output queue URL is empty, no message should be sent."""
        service = _make_service(aws_env)
        service.output_queue_url = ""

        # Should not raise
        service._emit_sections_ready(
            event_id="evt-001",
            entity_id="AAPL",
            cik="0000320193",
            form_type="10-K",
            filing_date="2026-02-15",
            sections_available=["item_1"],
            section_s3_prefix="s3://bucket/prefix",
            total_text_length=10000,
        )

        # Queue should be empty
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        assert len(response.get("Messages", [])) == 0


class TestProcessMessage:
    """Tests for end-to-end message processing."""

    def test_end_to_end(self, aws_env):
        """Full flow: S3 fetch → parse → store sections → DynamoDB → SQS."""
        service = _make_service(aws_env)
        s3_uri = _upload_filing(
            aws_env, "filings/1234567/10-K/2026-02-15/raw.html", SAMPLE_10K_HTML
        )
        message = _make_filing_ready_message("evt-e2e", "AAPL", s3_uri)

        service.process_message(message)

        # Verify sections stored in S3
        s3 = boto3.client("s3", region_name=aws_env["region"])
        response = s3.list_objects_v2(
            Bucket=aws_env["bucket"],
            Prefix="filings/1234567/10-K/2026-02-15/sections/",
        )
        keys = [obj["Key"] for obj in response.get("Contents", [])]
        assert any("item_1.txt" in k for k in keys)
        assert any("item_1a.txt" in k for k in keys)
        assert any("item_7.txt" in k for k in keys)

        # Verify DynamoDB record
        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        db_response = table.get_item(
            Key={
                "PK": "ENTITY#AAPL",
                "SK": "SECTIONS#10-K#2026-02-15",
            }
        )
        item = db_response["Item"]
        assert item["entity_id"] == "AAPL"
        assert "item_1" in item["sections_available"]
        assert item["total_text_length"] > 0

        # Verify SQS event emitted
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        sqs_response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        messages = sqs_response.get("Messages", [])
        assert len(messages) == 1
        event = BaseEvent.from_sqs_message(messages[0]["Body"])
        assert isinstance(event, FilingSectionsReady)

    def test_bad_message_doesnt_crash(self, aws_env):
        """Malformed message should be logged but not crash."""
        service = _make_service(aws_env)
        bad_message = {
            "MessageId": "bad-msg-001",
            "ReceiptHandle": "bad-receipt",
            "Body": '{"this_is": "not_a_valid_event"}',
        }
        # Should not raise
        service.process_message(bad_message)

    def test_empty_filing_skips(self, aws_env):
        """Filing with only whitespace content should be skipped."""
        service = _make_service(aws_env)
        s3_uri = _upload_filing(aws_env, "filings/empty/raw.html", "   \n  ")
        message = _make_filing_ready_message("evt-empty", "AAPL", s3_uri)

        service.process_message(message)

        # No DynamoDB record written
        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.scan()
        assert len(response["Items"]) == 0


class TestServiceLifecycle:
    """Tests for service initialization and lifecycle."""

    def test_stop_sets_flag(self, aws_env):
        """stop() should set _running to False."""
        service = _make_service(aws_env)
        assert service._running is True
        service.stop()
        assert service._running is False

    def test_env_var_initialization(self, aws_env):
        """Service should pick up env vars correctly."""
        service = _make_service(aws_env)
        assert service._region == "us-east-1"
        assert service._env == "test"
        assert service.input_queue_url == aws_env["input_queue_url"]
        assert service.output_queue_url == aws_env["output_queue_url"]
        assert service._bucket == aws_env["bucket"]
        assert service._running is True


# ===========================================================================
# Fan-out to filing-indexer queue
# ===========================================================================


class TestFilingIndexerFanOut:
    """Tests for the fan-out from SectionExtractorService to filing-indexer queue."""

    def test_fan_out_sends_to_indexer_queue(self, aws_env):
        """process_message should send FilingSectionsReady to filing-indexer queue."""
        # Create additional queue for fan-out
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        indexer_q = sqs.create_queue(QueueName="test-filing-indexer")
        os.environ["FILING_INDEXER_QUEUE_URL"] = indexer_q["QueueUrl"]

        service = _make_service(aws_env)
        s3_uri = _upload_filing(
            aws_env, "filings/1234567/10-K/2026-02-15/raw.html", SAMPLE_10K_HTML
        )
        message = _make_filing_ready_message("evt-fanout", "AAPL", s3_uri)

        service.process_message(message)

        # Verify message sent to filing-indexer queue
        response = sqs.receive_message(
            QueueUrl=indexer_q["QueueUrl"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        assert len(messages) == 1

        event = BaseEvent.from_sqs_message(messages[0]["Body"])
        assert isinstance(event, FilingSectionsReady)
        assert event.payload["entity_id"] == "AAPL"
        assert event.payload["form_type"] == "10-K"

    def test_fan_out_skipped_without_queue_url(self, aws_env):
        """No fan-out when FILING_INDEXER_QUEUE_URL is not set."""
        os.environ["FILING_INDEXER_QUEUE_URL"] = ""

        service = _make_service(aws_env)
        s3_uri = _upload_filing(
            aws_env, "filings/1234567/10-K/2026-02-15/raw2.html", SAMPLE_10K_HTML
        )
        message = _make_filing_ready_message("evt-nofanout", "AAPL", s3_uri)

        # Should not raise
        service.process_message(message)

    def test_fan_out_failure_doesnt_crash(self, aws_env):
        """Fan-out failure should be logged but not crash the service."""
        os.environ["FILING_INDEXER_QUEUE_URL"] = "https://sqs.us-east-1.amazonaws.com/000000000000/nonexistent"

        service = _make_service(aws_env)
        s3_uri = _upload_filing(
            aws_env, "filings/1234567/10-K/2026-02-15/raw3.html", SAMPLE_10K_HTML
        )
        message = _make_filing_ready_message("evt-fail", "AAPL", s3_uri)

        # Should not raise despite invalid queue
        service.process_message(message)
