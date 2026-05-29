"""Tests for the SignalScoringService — direction score integration."""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from signalfft_common.events import BaseEvent, SignalScored


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_env():
    with mock_aws():
        region = "us-east-1"
        env = "test"
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["ENVIRONMENT"] = env
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
        os.environ["AWS_SECURITY_TOKEN"] = "testing"
        os.environ["AWS_SESSION_TOKEN"] = "testing"

        dynamodb = boto3.client("dynamodb", region_name=region)

        # Features table
        features_table = f"{env}-signalfft-features"
        os.environ["FEATURES_TABLE"] = features_table
        dynamodb.create_table(
            TableName=features_table,
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

        # Signals table
        signals_table = f"{env}-signalfft-signals"
        os.environ["SIGNALS_TABLE"] = signals_table
        dynamodb.create_table(
            TableName=signals_table,
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

        # Events table
        events_table = f"{env}-signalfft-events"
        os.environ["EVENTS_TABLE"] = events_table
        dynamodb.create_table(
            TableName=events_table,
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

        # SQS queues
        sqs = boto3.client("sqs", region_name=region)
        features_q = sqs.create_queue(QueueName="test-features")
        signals_q = sqs.create_queue(QueueName="test-signals")
        os.environ["FEATURES_QUEUE_URL"] = features_q["QueueUrl"]
        os.environ["SIGNALS_QUEUE_URL"] = signals_q["QueueUrl"]
        os.environ.pop("RISK_INPUT_QUEUE_URL", None)
        os.environ.pop("OUTCOME_TRACKING_QUEUE_URL", None)
        os.environ.pop("GRAPH_EDGES_TABLE", None)

        yield {
            "region": region,
            "features_table": features_table,
            "signals_table": signals_table,
            "events_table": events_table,
            "signals_queue_url": signals_q["QueueUrl"],
        }


def _put_feature(aws_env, event_id: str, feature_type: str, value: dict, sk_suffix: str = ""):
    """Insert a feature into the features table."""
    dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
    table = dynamo.Table(aws_env["features_table"])
    sk = f"FEATURE#{sk_suffix or str(uuid.uuid4())}"
    table.put_item(Item={
        "PK": f"EVENT#{event_id}",
        "SK": sk,
        "feature_type": feature_type,
        "value": json.loads(json.dumps(value), parse_float=Decimal),
    })


def _make_sqs_message(event_id: str, entity_id: str) -> dict:
    """Create a mock SQS message with a FeatureExtracted-like event."""
    from signalfft_common.events import FeatureExtracted
    event = FeatureExtracted(
        timestamp="2026-02-25T10:00:00+00:00",
        source="feature_extraction",
        trace_id=str(uuid.uuid4()),
        payload={
            "feature_id": str(uuid.uuid4()),
            "event_id": event_id,
            "entity_id": entity_id,
            "feature_type": "SENTIMENT",
        },
    )
    return {
        "MessageId": str(uuid.uuid4()),
        "ReceiptHandle": "test-receipt",
        "Body": event.to_sqs_message(),
    }


def _make_service(aws_env):
    """Create a SignalScoringService with test env."""
    from engine.signal_scoring.service import SignalScoringService
    return SignalScoringService()


def _get_signals(aws_env, entity_id: str) -> list[dict]:
    """Scan signals table for items matching entity."""
    dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
    table = dynamo.Table(aws_env["signals_table"])
    response = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": f"ENTITY#{entity_id}"},
    )
    return response.get("Items", [])


# ===========================================================================
# _build_components tests
# ===========================================================================


class TestBuildComponents:
    """Tests for _build_components returning (dict, lexicon_polarity) tuple."""

    def test_returns_tuple(self, aws_env):
        """_build_components should return a (dict, float) tuple."""
        svc = _make_service(aws_env)
        _put_feature(aws_env, "evt-1", "SENTIMENT", {
            "polarity": 0.5, "magnitude": 0.3,
            "positive_terms": ["growth"], "negative_terms": [],
            "lexicon_polarity": 0.25,
        })
        result = svc._build_components("evt-1", "AAPL")
        assert isinstance(result, tuple)
        assert len(result) == 2
        components, lp = result
        assert isinstance(components, dict)
        assert isinstance(lp, float)

    def test_lexicon_polarity_extracted(self, aws_env):
        """lexicon_polarity should be read from the SENTIMENT feature."""
        svc = _make_service(aws_env)
        _put_feature(aws_env, "evt-2", "SENTIMENT", {
            "polarity": 0.3, "magnitude": 0.5,
            "positive_terms": ["beat"], "negative_terms": [],
            "lexicon_polarity": 0.4,
        })
        _, lp = svc._build_components("evt-2", "AAPL")
        assert lp == pytest.approx(0.4)

    def test_missing_lexicon_polarity_defaults_zero(self, aws_env):
        """When SENTIMENT feature has no lexicon_polarity, default to 0.0."""
        svc = _make_service(aws_env)
        _put_feature(aws_env, "evt-3", "SENTIMENT", {
            "polarity": 0.5, "magnitude": 0.3,
            "positive_terms": ["growth"], "negative_terms": [],
        })
        _, lp = svc._build_components("evt-3", "AAPL")
        assert lp == 0.0

    def test_no_sentiment_defaults_zero(self, aws_env):
        """When there are no SENTIMENT features, lexicon_polarity should be 0.0."""
        svc = _make_service(aws_env)
        _put_feature(aws_env, "evt-4", "ENTITY_MENTION", {
            "name": "Apple", "mention_count": 2,
        })
        _, lp = svc._build_components("evt-4", "AAPL")
        assert lp == 0.0


# ===========================================================================
# DynamoDB storage tests
# ===========================================================================


class TestDirectionScoreStorage:
    """Tests for direction_score being stored in DynamoDB signal records."""

    def test_direction_score_in_dynamo(self, aws_env):
        """direction_score should be stored in the DynamoDB signal record."""
        svc = _make_service(aws_env)
        _put_feature(aws_env, "evt-store", "SENTIMENT", {
            "polarity": 0.5, "magnitude": 0.3,
            "positive_terms": ["growth"], "negative_terms": [],
            "lexicon_polarity": 0.33,
        })
        msg = _make_sqs_message("evt-store", "AAPL")
        svc.process_message(msg)

        items = _get_signals(aws_env, "AAPL")
        assert len(items) == 1
        assert "direction_score" in items[0]
        assert float(items[0]["direction_score"]) == pytest.approx(0.33, abs=0.01)


# ===========================================================================
# Event emission tests
# ===========================================================================


class TestDirectionScoreEvent:
    """Tests for direction_score being included in SignalScored event payload."""

    def test_direction_score_in_event(self, aws_env):
        """direction_score should be in the SignalScored event payload on the output queue."""
        # Create an output queue to capture the event
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        out_q = sqs.create_queue(QueueName="test-signals-out")
        os.environ["SIGNALS_QUEUE_URL"] = out_q["QueueUrl"]

        svc = _make_service(aws_env)
        _put_feature(aws_env, "evt-emit", "SENTIMENT", {
            "polarity": 0.5, "magnitude": 0.3,
            "positive_terms": ["growth"], "negative_terms": [],
            "lexicon_polarity": 0.25,
        })
        msg = _make_sqs_message("evt-emit", "MSFT")
        svc.process_message(msg)

        # Read the emitted event from the output queue
        response = sqs.receive_message(
            QueueUrl=out_q["QueueUrl"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        assert len(messages) == 1
        event = BaseEvent.from_sqs_message(messages[0]["Body"])
        assert "direction_score" in event.payload
        assert float(event.payload["direction_score"]) == pytest.approx(0.25, abs=0.01)
