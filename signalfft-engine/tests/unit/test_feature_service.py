"""Tests for FeatureExtractionService with moto mocked AWS services."""

from __future__ import annotations

import json
import os
import uuid

import boto3
import pytest
from moto import mock_aws

from signalfft_common.enums import FeatureType
from signalfft_common.events import BaseEvent, FeatureExtracted, RawEventCollected
from signalfft_common.models import Feature


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        input_q = sqs.create_queue(QueueName="test-raw-events")
        output_q = sqs.create_queue(QueueName="test-features")
        os.environ["RAW_EVENTS_QUEUE_URL"] = input_q["QueueUrl"]
        os.environ["FEATURES_QUEUE_URL"] = output_q["QueueUrl"]

        dynamodb = boto3.client("dynamodb", region_name=region)
        table_name = f"{env}-signalfft-features"
        os.environ["FEATURES_TABLE"] = table_name
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
    """Create a FeatureExtractionService after env vars are set."""
    from engine.feature_extraction.service import FeatureExtractionService
    return FeatureExtractionService()


def _upload_artifact(aws_env, key: str, content: dict) -> str:
    """Upload a JSON artifact to S3 and return the s3:// URI."""
    s3 = boto3.client("s3", region_name=aws_env["region"])
    s3.put_object(
        Bucket=aws_env["bucket"],
        Key=key,
        Body=json.dumps(content).encode("utf-8"),
    )
    return f"s3://{aws_env['bucket']}/{key}"


def _make_raw_event_message(event_id: str, entity_id: str, s3_uri: str) -> dict:
    """Create a mock SQS message containing a RawEventCollected event."""
    event = RawEventCollected(
        timestamp="2026-01-15T00:00:00+00:00",
        source="test-collector",
        trace_id=str(uuid.uuid4()),
        payload={
            "event_id": event_id,
            "entity_id": entity_id,
            "source": "test",
            "content_hash": "abc123",
            "raw_artifact_s3": s3_uri,
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


class TestFetchArtifact:
    """Tests for S3 artifact fetching."""

    def test_fetch_artifact(self, aws_env):
        """Service should fetch and parse JSON from S3."""
        service = _make_service(aws_env)
        content = {"text": "Goldman Sachs reported earnings growth."}
        s3_uri = _upload_artifact(aws_env, "events/test-artifact.json", content)

        result = service._fetch_artifact(s3_uri)
        assert result == content


class TestStoreFeature:
    """Tests for DynamoDB feature storage."""

    def test_store_feature(self, aws_env):
        """Feature should be written to DynamoDB with correct PK/SK."""
        service = _make_service(aws_env)
        feature = Feature(
            feature_id="feat-001",
            event_id="evt-001",
            entity_id="ent-001",
            feature_type=FeatureType.SENTIMENT,
            value={"polarity": 0.5, "magnitude": 0.3},
            created_at="2026-01-15T00:00:00+00:00",
        )
        service._store_feature(feature)

        # Verify the item is in DynamoDB
        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(
            Key={
                "PK": "EVENT#evt-001",
                "SK": "FEATURE#feat-001",
            }
        )
        item = response["Item"]
        assert item["event_id"] == "evt-001"
        assert item["entity_id"] == "ent-001"
        assert item["feature_type"] == "SENTIMENT"
        assert item["feature_id"] == "feat-001"


class TestEmitFeatureEvent:
    """Tests for SQS event emission."""

    def test_emit_feature_event(self, aws_env):
        """FeatureExtracted event should be sent to the output queue and be deserializable."""
        service = _make_service(aws_env)
        feature = Feature(
            feature_id="feat-002",
            event_id="evt-002",
            entity_id="ent-002",
            feature_type=FeatureType.ENTITY_MENTION,
            value={"name": "Apple Inc", "mention_count": 3},
            created_at="2026-01-15T00:00:00+00:00",
        )
        service._emit_feature_event(feature)

        # Read from output queue
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        assert len(messages) == 1

        # Deserialize and verify
        event = BaseEvent.from_sqs_message(messages[0]["Body"])
        assert isinstance(event, FeatureExtracted)
        assert event.payload["feature_id"] == "feat-002"
        assert event.payload["event_id"] == "evt-002"
        assert event.payload["entity_id"] == "ent-002"
        assert event.payload["feature_type"] == "ENTITY_MENTION"


class TestProcessMessage:
    """Tests for end-to-end message processing."""

    def test_process_message_end_to_end(self, aws_env):
        """Full message processing: fetch artifact, extract, store, emit."""
        service = _make_service(aws_env)
        content = {"text": "Goldman Sachs reported strong growth on 2026-01-15."}
        s3_uri = _upload_artifact(aws_env, "events/full-test.json", content)
        message = _make_raw_event_message("evt-e2e", "ent-e2e", s3_uri)

        service.process_message(message)

        # Verify features stored in DynamoDB
        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.scan()
        items = response["Items"]
        assert len(items) > 0
        # All items should reference our event
        for item in items:
            assert item["PK"] == "EVENT#evt-e2e"
            assert item["event_id"] == "evt-e2e"
            assert item["entity_id"] == "ent-e2e"

        # Verify events emitted to output queue
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        assert len(messages) > 0

    def test_process_message_error_doesnt_crash(self, aws_env):
        """A malformed message should be logged but not crash the service."""
        service = _make_service(aws_env)
        bad_message = {
            "MessageId": "bad-msg-001",
            "ReceiptHandle": "bad-receipt",
            "Body": '{"this_is": "not_a_valid_event"}',
        }
        # Should not raise
        service.process_message(bad_message)


class TestPollMessages:
    """Tests for SQS polling."""

    def test_poll_messages_empty(self, aws_env):
        """When no messages are in the queue, poll should return empty list."""
        service = _make_service(aws_env)
        messages = service._poll_messages()
        assert messages == []


class TestServiceLifecycle:
    """Tests for service initialization and lifecycle."""

    def test_stop_sets_flag(self, aws_env):
        """stop() should set _running to False."""
        service = _make_service(aws_env)
        assert service._running is True
        service.stop()
        assert service._running is False

    def test_service_init_defaults(self, aws_env):
        """Service should pick up env vars and set correct defaults."""
        service = _make_service(aws_env)
        assert service._region == "us-east-1"
        assert service._env == "test"
        assert service.input_queue_url == aws_env["input_queue_url"]
        assert service.output_queue_url == aws_env["output_queue_url"]
        assert service._poll_interval == 5
        assert service._max_messages == 10
        assert service._running is True
