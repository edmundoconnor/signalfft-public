"""Comprehensive tests for the BaseCollector framework."""

from __future__ import annotations

import json
import os
import sys

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock, patch

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collectors.base import BaseCollector, make_lambda_handler


# ---------------------------------------------------------------------------
# Concrete test subclass
# ---------------------------------------------------------------------------

class StubCollector(BaseCollector):
    """Minimal concrete collector for testing."""

    source_name = "TEST_SOURCE"

    def __init__(self, docs=None):
        super().__init__()
        self._docs = docs or []

    def collect(self):
        return self._docs

    def extract_entity_id(self, doc):
        return doc.get("entity_id", "unknown")

    def extract_event_type(self, doc):
        return doc.get("event_type", "TEST_EVENT")


class FailingCollector(BaseCollector):
    """Collector whose collect() always raises."""

    source_name = "FAILING_SOURCE"

    def collect(self):
        raise RuntimeError("External API unreachable")

    def extract_entity_id(self, doc):
        return "n/a"

    def extract_event_type(self, doc):
        return "n/a"


class PartialFailCollector(BaseCollector):
    """Collector that returns docs, but extract_entity_id raises on specific docs."""

    source_name = "PARTIAL_FAIL"

    def __init__(self, docs=None):
        super().__init__()
        self._docs = docs or []

    def collect(self):
        return self._docs

    def extract_entity_id(self, doc):
        if doc.get("bad"):
            raise ValueError("Bad document")
        return doc.get("entity_id", "unknown")

    def extract_event_type(self, doc):
        return doc.get("event_type", "TEST_EVENT")


class HookTrackingCollector(BaseCollector):
    """Collector that tracks on_event_stored calls."""

    source_name = "HOOK_TRACKER"

    def __init__(self, docs=None):
        super().__init__()
        self._docs = docs or []
        self.hook_calls = []

    def collect(self):
        return self._docs

    def extract_entity_id(self, doc):
        return doc.get("entity_id", "unknown")

    def extract_event_type(self, doc):
        return doc.get("event_type", "TEST_EVENT")

    def on_event_stored(self, event_id, entity_id, doc):
        self.hook_calls.append({"event_id": event_id, "entity_id": entity_id, "doc": doc})


class FailingHookCollector(BaseCollector):
    """Collector whose on_event_stored always raises."""

    source_name = "FAILING_HOOK"

    def __init__(self, docs=None):
        super().__init__()
        self._docs = docs or []

    def collect(self):
        return self._docs

    def extract_entity_id(self, doc):
        return doc.get("entity_id", "unknown")

    def extract_event_type(self, doc):
        return doc.get("event_type", "TEST_EVENT")

    def on_event_stored(self, event_id, entity_id, doc):
        raise RuntimeError("Hook exploded")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_env():
    with mock_aws():
        region = "us-east-1"
        env = "test"

        # Set env vars
        os.environ["AWS_REGION"] = region
        os.environ["ENVIRONMENT"] = env
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"

        bucket_name = f"{env}-signalfft-artifacts"
        os.environ["ARTIFACT_BUCKET"] = bucket_name

        # Create S3 bucket
        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=bucket_name)

        # Create SQS queue
        sqs = boto3.client("sqs", region_name=region)
        queue = sqs.create_queue(QueueName="test-raw-events")
        os.environ["RAW_EVENTS_QUEUE_URL"] = queue["QueueUrl"]

        # Create DynamoDB events table
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
            "bucket": bucket_name,
            "queue_url": queue["QueueUrl"],
            "table_name": table_name,
        }


# ---------------------------------------------------------------------------
# Content hash tests
# ---------------------------------------------------------------------------

class TestContentHash:
    """Tests for the content_hash method."""

    def test_content_hash_deterministic(self, aws_env):
        """Same doc produces the same hash every time."""
        collector = StubCollector()
        doc = {"title": "Test", "value": 42}
        hash1 = collector.content_hash(doc)
        hash2 = collector.content_hash(doc)
        assert hash1 == hash2

    def test_content_hash_different_docs(self, aws_env):
        """Different docs produce different hashes."""
        collector = StubCollector()
        doc1 = {"title": "Alpha"}
        doc2 = {"title": "Beta"}
        assert collector.content_hash(doc1) != collector.content_hash(doc2)

    def test_content_hash_key_order_independent(self, aws_env):
        """Dict key order does not affect the hash (sort_keys=True)."""
        collector = StubCollector()
        doc_a = {"z_key": 1, "a_key": 2}
        doc_b = {"a_key": 2, "z_key": 1}
        assert collector.content_hash(doc_a) == collector.content_hash(doc_b)

    def test_content_hash_is_sha256_hex(self, aws_env):
        """Hash output is a valid 64-character hex string (SHA-256)."""
        collector = StubCollector()
        h = collector.content_hash({"data": "test"})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# S3 artifact storage tests
# ---------------------------------------------------------------------------

class TestStoreArtifact:
    """Tests for the store_artifact method."""

    def test_store_artifact_uploads_to_s3(self, aws_env):
        """Verify the S3 object exists after calling store_artifact."""
        collector = StubCollector()
        doc = {"filing": "10-K", "content": "annual report"}
        event_id = "evt-001"

        collector.store_artifact(event_id, doc)

        s3 = boto3.client("s3", region_name=aws_env["region"])
        key = f"raw/TEST_SOURCE/{event_id}.json"
        obj = s3.get_object(Bucket=aws_env["bucket"], Key=key)
        assert obj is not None

    def test_store_artifact_returns_s3_uri(self, aws_env):
        """Returns the correct s3:// URI format."""
        collector = StubCollector()
        doc = {"data": "value"}
        event_id = "evt-002"

        uri = collector.store_artifact(event_id, doc)

        expected = f"s3://{aws_env['bucket']}/raw/TEST_SOURCE/{event_id}.json"
        assert uri == expected

    def test_store_artifact_content(self, aws_env):
        """Verify the stored content matches the original document."""
        collector = StubCollector()
        doc = {"ticker": "AAPL", "price": 150.25}
        event_id = "evt-003"

        collector.store_artifact(event_id, doc)

        s3 = boto3.client("s3", region_name=aws_env["region"])
        key = f"raw/TEST_SOURCE/{event_id}.json"
        obj = s3.get_object(Bucket=aws_env["bucket"], Key=key)
        stored = json.loads(obj["Body"].read().decode("utf-8"))
        assert stored == doc


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

class TestIsDuplicate:
    """Tests for the is_duplicate method."""

    def test_is_duplicate_false_when_empty(self, aws_env):
        """Returns False when the events table is empty."""
        collector = StubCollector()
        assert collector.is_duplicate("abc123deadbeef") is False

    def test_is_duplicate_true_when_exists(self, aws_env):
        """Returns True after storing an event with the same content hash."""
        collector = StubCollector()
        ch = "deadbeefcafebabe1234567890abcdef"

        # Manually insert a record with this content_hash
        collector._events_table.put_item(
            Item={
                "PK": "ENTITY#ent-1",
                "SK": "EVENT#2026-01-01T00:00:00#evt-existing",
                "content_hash": ch,
                "event_id": "evt-existing",
                "entity_id": "ent-1",
                "source": "TEST_SOURCE",
                "raw_artifact_s3": "s3://bucket/key",
                "event_type": "TEST",
                "created_at": "2026-01-01T00:00:00",
            }
        )

        assert collector.is_duplicate(ch) is True


# ---------------------------------------------------------------------------
# SQS event emission tests
# ---------------------------------------------------------------------------

class TestEmitEvent:
    """Tests for the emit_event method."""

    def test_emit_event_sends_to_sqs(self, aws_env):
        """Verify an SQS message is sent when emit_event is called."""
        collector = StubCollector()
        collector.emit_event(
            event_id="evt-sqs-1",
            entity_id="ent-1",
            source="TEST_SOURCE",
            content_hash="hash123",
            raw_artifact_s3="s3://bucket/key.json",
        )

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["queue_url"],
            MaxNumberOfMessages=1,
        )
        messages = response.get("Messages", [])
        assert len(messages) == 1

    def test_emit_event_message_deserializable(self, aws_env):
        """The SQS message can be deserialized back into a RawEventCollected event."""
        from signalfft_common.events import BaseEvent

        collector = StubCollector()
        collector.emit_event(
            event_id="evt-sqs-2",
            entity_id="ent-2",
            source="TEST_SOURCE",
            content_hash="hash456",
            raw_artifact_s3="s3://bucket/another.json",
        )

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["queue_url"],
            MaxNumberOfMessages=1,
        )
        body = response["Messages"][0]["Body"]
        event = BaseEvent.from_sqs_message(body)

        assert event.event_type == "RAW_EVENT_COLLECTED"
        assert event.payload["event_id"] == "evt-sqs-2"
        assert event.payload["entity_id"] == "ent-2"
        assert event.payload["content_hash"] == "hash456"
        assert event.payload["raw_artifact_s3"] == "s3://bucket/another.json"


# ---------------------------------------------------------------------------
# DynamoDB event record tests
# ---------------------------------------------------------------------------

class TestStoreEventRecord:
    """Tests for the store_event_record method."""

    def test_store_event_record_writes_to_dynamo(self, aws_env):
        """Verify the DynamoDB item is written with correct PK/SK structure."""
        from signalfft_common.dynamo.keys import build_events_pk, build_events_sk

        collector = StubCollector()
        event_id = "evt-dynamo-1"
        entity_id = "ent-10"
        timestamp = "2026-02-15T12:00:00+00:00"

        collector.store_event_record(
            event_id=event_id,
            entity_id=entity_id,
            event_type="SEC_10K",
            content_hash="hashXYZ",
            raw_artifact_s3="s3://bucket/raw/key.json",
            timestamp=timestamp,
        )

        # Read it back
        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        response = table.get_item(
            Key={
                "PK": build_events_pk(entity_id),
                "SK": build_events_sk(timestamp, event_id),
            }
        )
        item = response["Item"]
        assert item["event_id"] == event_id
        assert item["entity_id"] == entity_id
        assert item["source"] == "TEST_SOURCE"
        assert item["event_type"] == "SEC_10K"
        assert item["content_hash"] == "hashXYZ"
        assert item["raw_artifact_s3"] == "s3://bucket/raw/key.json"
        assert item["created_at"] == timestamp


# ---------------------------------------------------------------------------
# Full pipeline (run) tests
# ---------------------------------------------------------------------------

class TestRun:
    """Tests for the run() pipeline method."""

    def test_run_full_pipeline(self, aws_env):
        """End-to-end: collect -> deduplicate -> store -> emit."""
        docs = [
            {"entity_id": "ent-A", "event_type": "NEWS_ARTICLE", "headline": "Breaking News"},
            {"entity_id": "ent-B", "event_type": "SEC_10K", "body": "Annual report"},
        ]
        collector = StubCollector(docs=docs)
        result = collector.run()

        assert result["collected"] == 2
        assert result["stored"] == 2
        assert result["duplicates"] == 0
        assert result["errors"] == 0

        # Verify S3 objects exist (2 artifacts)
        s3 = boto3.client("s3", region_name=aws_env["region"])
        objects = s3.list_objects_v2(Bucket=aws_env["bucket"], Prefix="raw/TEST_SOURCE/")
        assert objects["KeyCount"] == 2

        # Verify DynamoDB records (2 events)
        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        assert len(scan["Items"]) == 2

        # Verify SQS messages (2 messages)
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        msgs = sqs.receive_message(
            QueueUrl=aws_env["queue_url"],
            MaxNumberOfMessages=10,
        )
        assert len(msgs.get("Messages", [])) == 2

    def test_run_deduplication(self, aws_env):
        """Duplicate docs within the same batch are skipped after the first."""
        doc = {"entity_id": "ent-dup", "event_type": "NEWS", "content": "same"}
        collector = StubCollector(docs=[doc, doc])
        result = collector.run()

        # First doc stored, second detected as duplicate
        assert result["collected"] == 2
        assert result["stored"] == 1
        assert result["duplicates"] == 1
        assert result["errors"] == 0

    def test_run_error_handling(self, aws_env):
        """An error in one doc does not stop the pipeline for the remaining docs."""
        docs = [
            {"bad": True, "entity_id": "ent-bad"},   # Will raise in extract_entity_id
            {"entity_id": "ent-good", "event_type": "OK"},
        ]
        collector = PartialFailCollector(docs=docs)
        result = collector.run()

        assert result["collected"] == 2
        assert result["errors"] == 1
        assert result["stored"] == 1

    def test_run_collect_failure(self, aws_env):
        """If collect() itself raises, return error stats immediately."""
        collector = FailingCollector()
        result = collector.run()

        assert result["collected"] == 0
        assert result["stored"] == 0
        assert result["duplicates"] == 0
        assert result["errors"] == 1

    def test_run_empty_collect(self, aws_env):
        """Collecting zero documents returns zeroed stats with no errors."""
        collector = StubCollector(docs=[])
        result = collector.run()

        assert result["collected"] == 0
        assert result["stored"] == 0
        assert result["duplicates"] == 0
        assert result["errors"] == 0


# ---------------------------------------------------------------------------
# make_lambda_handler tests
# ---------------------------------------------------------------------------

class TestMakeLambdaHandler:
    """Tests for the make_lambda_handler factory function."""

    def test_make_lambda_handler(self, aws_env):
        """Factory returns a callable that invokes run() and wraps the result."""
        handler = make_lambda_handler(StubCollector)
        response = handler({}, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "collected" in body
        assert "stored" in body
        assert "duplicates" in body
        assert "errors" in body

    def test_make_lambda_handler_with_docs(self, aws_env):
        """Handler processes docs when the collector has data."""
        # Create a custom class so __init__ takes no args (as make_lambda_handler expects)
        class PreloadedCollector(BaseCollector):
            source_name = "PRELOADED"

            def collect(self):
                return [{"entity_id": "ent-pre", "event_type": "TEST", "data": "hello"}]

            def extract_entity_id(self, doc):
                return doc["entity_id"]

            def extract_event_type(self, doc):
                return doc["event_type"]

        handler = make_lambda_handler(PreloadedCollector)
        response = handler({}, None)

        body = json.loads(response["body"])
        assert body["collected"] == 1
        assert body["stored"] == 1
        assert body["errors"] == 0


# ---------------------------------------------------------------------------
# on_event_stored hook tests
# ---------------------------------------------------------------------------

class TestOnEventStoredHook:
    """Tests for the on_event_stored hook mechanism."""

    def test_on_event_stored_called_after_emit(self, aws_env):
        """Hook is called once per stored document with correct args."""
        docs = [
            {"entity_id": "ent-A", "event_type": "TEST", "data": "alpha"},
            {"entity_id": "ent-B", "event_type": "TEST", "data": "beta"},
        ]
        collector = HookTrackingCollector(docs=docs)
        result = collector.run()

        assert result["stored"] == 2
        assert len(collector.hook_calls) == 2
        assert collector.hook_calls[0]["entity_id"] == "ent-A"
        assert collector.hook_calls[0]["doc"] == docs[0]
        assert collector.hook_calls[1]["entity_id"] == "ent-B"
        assert collector.hook_calls[1]["doc"] == docs[1]

    def test_on_event_stored_default_noop(self, aws_env):
        """Default implementation is a no-op (doesn't raise)."""
        docs = [{"entity_id": "ent-C", "event_type": "TEST"}]
        collector = StubCollector(docs=docs)
        result = collector.run()
        assert result["stored"] == 1
        assert result["errors"] == 0

    def test_on_event_stored_error_does_not_break_pipeline(self, aws_env):
        """Hook failure does not affect stored count or stop pipeline."""
        docs = [
            {"entity_id": "ent-D", "event_type": "TEST", "data": "first"},
            {"entity_id": "ent-E", "event_type": "TEST", "data": "second"},
        ]
        collector = FailingHookCollector(docs=docs)
        result = collector.run()

        # Both documents should still be stored despite hook failures
        assert result["stored"] == 2
        assert result["errors"] == 0
