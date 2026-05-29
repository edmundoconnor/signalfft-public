"""Tests for the Risk Gateway service using moto to mock AWS."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from signalfft_common.events import SignalScored

REGION = "us-east-1"
SIGNALS_TABLE = "test-signals"
CANDIDATES_TABLE = "test-trade-candidates"
INPUT_QUEUE = "test-risk-input"


def _make_signal_scored_message(
    signal_id: str, entity_id: str, score: float, direction_score: float = 0.0,
) -> dict:
    """Create a mock SQS message containing a SignalScored event."""
    event = SignalScored(
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="signal_scoring",
        trace_id=str(uuid.uuid4()),
        payload={
            "signal_id": signal_id,
            "entity_id": entity_id,
            "score": score,
            "weight_version": "default",
            "attention_field_version": "v1",
            "direction_score": direction_score,
        },
    )
    return {
        "Body": event.to_sqs_message(),
        "ReceiptHandle": f"receipt-{signal_id}",
        "MessageId": f"msg-{signal_id}",
    }


def _create_tables():
    """Create signals and trade_candidates DynamoDB tables."""
    client = boto3.client("dynamodb", region_name=REGION)
    client.create_table(
        TableName=SIGNALS_TABLE,
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
    client.create_table(
        TableName=CANDIDATES_TABLE,
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


def _create_queue() -> str:
    """Create the input SQS queue and return URL."""
    sqs = boto3.client("sqs", region_name=REGION)
    result = sqs.create_queue(QueueName=INPUT_QUEUE)
    return result["QueueUrl"]


def _populate_signals():
    """Pre-populate signals table with 5 signals for entity AAPL."""
    dynamo = boto3.resource("dynamodb", region_name=REGION)
    table = dynamo.Table(SIGNALS_TABLE)
    scores = [0.14, 0.11, 0.08, 0.05, 0.02]
    now = datetime.now(timezone.utc).isoformat()
    for i, score in enumerate(scores):
        sig_id = f"SIG_{i}"
        table.put_item(Item={
            "PK": "ENTITY#AAPL",
            "SK": f"SIGNAL#{now}#{sig_id}",
            "signal_id": sig_id,
            "entity_id": "AAPL",
            "score": Decimal(str(score)),
            "created_at": now,
        })


def _make_service():
    """Create a RiskGatewayService with test env vars."""
    os.environ["AWS_DEFAULT_REGION"] = REGION
    os.environ["AWS_REGION"] = REGION
    os.environ["ENVIRONMENT"] = "test"
    os.environ["SIGNALS_TABLE"] = SIGNALS_TABLE
    os.environ["TRADE_CANDIDATES_TABLE"] = CANDIDATES_TABLE
    os.environ["INPUT_QUEUE_URL"] = ""
    os.environ["OUTPUT_QUEUE_URL"] = ""
    os.environ["MIN_SIGNAL_SCORE"] = "0.05"
    os.environ["MAX_CANDIDATES_PER_WINDOW"] = "10"

    from risk_gateway.service import RiskGatewayService
    return RiskGatewayService()


def _get_all_candidates():
    """Scan trade_candidates table and return items."""
    dynamo = boto3.resource("dynamodb", region_name=REGION)
    table = dynamo.Table(CANDIDATES_TABLE)
    response = table.scan()
    return response.get("Items", [])


def _write_approved_candidate(entity_id: str, candidate_id: str | None = None):
    """Write an APPROVED candidate to the table."""
    dynamo = boto3.resource("dynamodb", region_name=REGION)
    table = dynamo.Table(CANDIDATES_TABLE)
    cid = candidate_id or str(uuid.uuid4())
    table.put_item(Item={
        "PK": f"CANDIDATE#{cid}",
        "SK": "META",
        "candidate_id": cid,
        "entity_id": entity_id,
        "signal_id": "SIG_existing",
        "score": Decimal("0.10"),
        "risk_status": "APPROVED",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


@mock_aws
def test_process_batch_generates_candidates():
    """Send 5 SignalScored messages, verify candidates in DynamoDB."""
    _create_tables()
    _create_queue()
    _populate_signals()
    svc = _make_service()

    messages = [
        _make_signal_scored_message(f"SIG_{i}", "AAPL", score)
        for i, score in enumerate([0.14, 0.11, 0.08, 0.05, 0.02])
    ]
    candidates = svc.process_batch(messages)

    # 0.02 is below min_score=0.05, so only 4 candidates
    assert len(candidates) == 4

    items = _get_all_candidates()
    assert len(items) == 4
    for item in items:
        assert item["PK"].startswith("CANDIDATE#")
        assert item["SK"] == "META"
        assert item["entity_id"] == "AAPL"


@mock_aws
def test_risk_approval():
    """Signal with score 0.14, low exposure -> APPROVED."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    messages = [_make_signal_scored_message("SIG_A", "AAPL", 0.14)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["risk_status"] == "APPROVED"
    assert candidates[0]["risk_rejection_reason"] is None

    items = _get_all_candidates()
    assert len(items) == 1
    assert items[0]["risk_status"] == "APPROVED"


@mock_aws
def test_risk_rejection_low_score():
    """Signal with score 0.02 (below 0.05) -> no candidate generated."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    messages = [_make_signal_scored_message("SIG_LOW", "AAPL", 0.02)]
    candidates = svc.process_batch(messages)

    # Score 0.02 < min_score 0.05: filtered out by generate_candidates
    assert len(candidates) == 0
    items = _get_all_candidates()
    assert len(items) == 0


@mock_aws
def test_entity_candidate_limit():
    """Pre-populate 3 APPROVED for AAPL, new signal -> REJECTED."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    # Pre-populate 3 approved candidates for AAPL
    for i in range(3):
        _write_approved_candidate("AAPL")

    messages = [_make_signal_scored_message("SIG_NEW", "AAPL", 0.14)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["risk_status"] == "REJECTED"
    assert "Entity has 3 active candidates" in candidates[0]["risk_rejection_reason"]


@mock_aws
def test_provenance_fields():
    """Verify all 4 provenance fields present on candidate."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    messages = [_make_signal_scored_message("SIG_PROV", "MSFT", 0.10)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    c = candidates[0]
    assert "signal_model_version" in c
    assert "attention_field_version" in c
    assert "opus_config_version" in c
    assert "engine_container_sha" in c

    items = _get_all_candidates()
    assert len(items) == 1
    item = items[0]
    assert "signal_model_version" in item
    assert "attention_field_version" in item
    assert "opus_config_version" in item
    assert "engine_container_sha" in item


@mock_aws
def test_graceful_no_signals():
    """Empty message list -> no errors, no candidates."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    candidates = svc.process_batch([])
    assert candidates == []

    items = _get_all_candidates()
    assert len(items) == 0


# ===========================================================================
# Direction derivation tests
# ===========================================================================


@mock_aws
def test_direction_long():
    """direction_score > 0.05 should derive LONG direction."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    messages = [_make_signal_scored_message("SIG_LONG", "AAPL", 0.14, direction_score=0.3)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["direction"] == "LONG"


@mock_aws
def test_direction_short():
    """direction_score < -0.05 should derive SHORT direction."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    messages = [_make_signal_scored_message("SIG_SHORT", "AAPL", 0.14, direction_score=-0.3)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["direction"] == "SHORT"


@mock_aws
def test_direction_neutral():
    """direction_score in dead zone should derive NEUTRAL direction."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    messages = [_make_signal_scored_message("SIG_NEUTRAL", "AAPL", 0.14, direction_score=0.03)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["direction"] == "NEUTRAL"


@mock_aws
def test_short_blocked_by_default():
    """SHORT candidate should be written to DDB but not published when ALLOW_SHORT=false."""
    _create_tables()
    queue_url = _create_queue()
    svc = _make_service()
    # Create an output queue to verify no message is published
    sqs = boto3.client("sqs", region_name=REGION)
    out_q = sqs.create_queue(QueueName="test-risk-output")
    svc.output_queue_url = out_q["QueueUrl"]

    messages = [_make_signal_scored_message("SIG_SHORT_BLOCKED", "AAPL", 0.14, direction_score=-0.3)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["risk_status"] == "APPROVED"
    assert candidates[0]["direction"] == "SHORT"

    # Should be in DDB
    items = _get_all_candidates()
    assert len(items) == 1

    # Should NOT be published
    response = sqs.receive_message(
        QueueUrl=out_q["QueueUrl"],
        MaxNumberOfMessages=1,
        WaitTimeSeconds=0,
    )
    assert len(response.get("Messages", [])) == 0


@mock_aws
def test_short_published_when_allowed():
    """SHORT candidate should be published when ALLOW_SHORT=true."""
    _create_tables()
    _create_queue()
    os.environ["ALLOW_SHORT"] = "true"
    svc = _make_service()
    sqs = boto3.client("sqs", region_name=REGION)
    out_q = sqs.create_queue(QueueName="test-risk-output-allow")
    svc.output_queue_url = out_q["QueueUrl"]

    messages = [_make_signal_scored_message("SIG_SHORT_OK", "AAPL", 0.14, direction_score=-0.3)]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["direction"] == "SHORT"

    # Should be published
    response = sqs.receive_message(
        QueueUrl=out_q["QueueUrl"],
        MaxNumberOfMessages=1,
        WaitTimeSeconds=0,
    )
    assert len(response.get("Messages", [])) == 1

    # Cleanup
    os.environ.pop("ALLOW_SHORT", None)


@mock_aws
def test_backwards_compat_missing_direction_score():
    """Missing direction_score in payload should default to NEUTRAL."""
    _create_tables()
    _create_queue()
    svc = _make_service()

    # Create a message without direction_score in payload
    event = SignalScored(
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="signal_scoring",
        trace_id=str(uuid.uuid4()),
        payload={
            "signal_id": "SIG_COMPAT",
            "entity_id": "AAPL",
            "score": 0.14,
            "weight_version": "default",
            "attention_field_version": "v1",
        },
    )
    messages = [{
        "Body": event.to_sqs_message(),
        "ReceiptHandle": "receipt-compat",
        "MessageId": "msg-compat",
    }]
    candidates = svc.process_batch(messages)

    assert len(candidates) == 1
    assert candidates[0]["direction"] == "NEUTRAL"
