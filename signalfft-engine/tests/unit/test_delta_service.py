"""Tests for SemanticDeltaService — SQS consumer, S3 loading, per-section loop,
DynamoDB writes, shadow scoring, event emission, 10-K vs 10-Q, skip logic."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from engine.ai_edges.semantic_delta.analyzer import SemanticShift, clear_prompt_cache
from engine.ai_edges.semantic_delta.scoring import clear_config_cache
from engine.ai_edges.semantic_delta.service import SemanticDeltaService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENTITY_ID = "AAPL"
PAIR_ID = "pair-001"
FORM_TYPE_10K = "10-K"
FORM_TYPE_10Q = "10-Q"
CURRENT_DATE = "2026-03-01"
PRIOR_DATE = "2025-03-01"
CURRENT_S3_PREFIX = "s3://test-bucket/filings/AAPL/10-K/2026-03-01/sections"
PRIOR_S3_PREFIX = "s3://test-bucket/filings/AAPL/10-K/2025-03-01/sections"

SAMPLE_SHIFTS = [
    SemanticShift(
        shift_type="risk_escalation",
        description="New cybersecurity risk.",
        severity=4,
        direction="bearish",
        evidence={"previous_excerpt": "none", "current_excerpt": "data breach"},
    ),
    SemanticShift(
        shift_type="guidance_change",
        description="Revenue guidance raised.",
        severity=3,
        direction="bullish",
        evidence={"previous_excerpt": "$5B", "current_excerpt": "$5.5B"},
    ),
]


def _make_pair_ready_payload(
    form_type: str = FORM_TYPE_10K,
    prior_s3_prefix: str | None = PRIOR_S3_PREFIX,
    prior_filing_date: str | None = PRIOR_DATE,
) -> dict:
    return {
        "entity_id": ENTITY_ID,
        "form_type": form_type,
        "current_filing_date": CURRENT_DATE,
        "prior_filing_date": prior_filing_date,
        "current_s3_prefix": CURRENT_S3_PREFIX,
        "prior_s3_prefix": prior_s3_prefix,
        "pair_id": PAIR_ID,
    }


def _make_sqs_message(payload: dict) -> dict:
    event = {
        "event_type": "FILING_PAIR_READY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "filing_indexer",
        "trace_id": str(uuid.uuid4()),
        "payload": payload,
    }
    return {
        "MessageId": "msg-001",
        "Body": json.dumps(event),
        "ReceiptHandle": "receipt-001",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear():
    clear_prompt_cache()
    clear_config_cache()
    yield
    clear_prompt_cache()
    clear_config_cache()


@pytest.fixture
def aws_env():
    """Set up mock AWS: DynamoDB tables, SQS queues, S3 bucket with sections."""
    with mock_aws():
        region = "us-east-1"
        env = "test"
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["ENVIRONMENT"] = env

        # DynamoDB tables
        dynamodb = boto3.client("dynamodb", region_name=region)
        for table_name in [
            f"{env}-signalfft-semantic-deltas",
            f"{env}-signalfft-shadow-scores",
        ]:
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

        # SQS queues
        sqs = boto3.client("sqs", region_name=region)
        input_q = sqs.create_queue(QueueName=f"{env}-signalfft-delta-analysis")
        output_q = sqs.create_queue(QueueName=f"{env}-signalfft-delta-complete")

        os.environ["DELTA_ANALYSIS_QUEUE_URL"] = input_q["QueueUrl"]
        os.environ["DELTA_COMPLETE_QUEUE_URL"] = output_q["QueueUrl"]

        # S3 bucket with section files
        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket="test-bucket")

        # Current 10-K sections
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-K/2026-03-01/sections/item_7.txt",
            Body=b"Current MD&A text with improved revenue outlook...",
        )
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-K/2026-03-01/sections/item_1a.txt",
            Body=b"Current risk factors including cybersecurity...",
        )

        # Prior 10-K sections
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-K/2025-03-01/sections/item_7.txt",
            Body=b"Previous MD&A text with standard revenue...",
        )
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-K/2025-03-01/sections/item_1a.txt",
            Body=b"Previous risk factors without cybersecurity...",
        )

        # 10-Q sections for testing
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-Q/2026-06-01/sections/part1_item2.txt",
            Body=b"Current 10-Q MD&A text...",
        )
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-Q/2026-06-01/sections/part2_item1a.txt",
            Body=b"Current 10-Q risk factors...",
        )
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-Q/2025-06-01/sections/part1_item2.txt",
            Body=b"Previous 10-Q MD&A text...",
        )
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-Q/2025-06-01/sections/part2_item1a.txt",
            Body=b"Previous 10-Q risk factors...",
        )

        yield {
            "region": region,
            "env": env,
            "input_queue_url": input_q["QueueUrl"],
            "output_queue_url": output_q["QueueUrl"],
        }

        for key in ["AWS_REGION", "AWS_DEFAULT_REGION", "ENVIRONMENT",
                     "DELTA_ANALYSIS_QUEUE_URL", "DELTA_COMPLETE_QUEUE_URL"]:
            os.environ.pop(key, None)


def _mock_analyze_delta(shifts=None):
    """Return a mock for analyze_delta that returns predefined shifts."""
    if shifts is None:
        shifts = SAMPLE_SHIFTS

    async def fake_analyze_delta(**kwargs):
        return list(shifts)

    return fake_analyze_delta


# ---------------------------------------------------------------------------
# _load_section_text
# ---------------------------------------------------------------------------

class TestLoadSectionText:
    def test_loads_existing_section(self, aws_env):
        service = SemanticDeltaService()
        text = service._load_section_text(CURRENT_S3_PREFIX, "item_7")
        assert "improved revenue" in text

    def test_missing_section_returns_empty(self, aws_env):
        service = SemanticDeltaService()
        text = service._load_section_text(CURRENT_S3_PREFIX, "item_99")
        assert text == ""

    def test_invalid_prefix_returns_empty(self, aws_env):
        service = SemanticDeltaService()
        text = service._load_section_text("s3://nonexistent/path", "item_7")
        assert text == ""


# ---------------------------------------------------------------------------
# Skip logic
# ---------------------------------------------------------------------------

class TestSkipLogic:
    def test_skip_if_no_prior(self, aws_env):
        """No prior filing → skip delta analysis."""
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload(prior_s3_prefix=None, prior_filing_date=None)
        message = _make_sqs_message(payload)

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta") as mock_analyze:
            service.process_message(message)
            mock_analyze.assert_not_called()

    def test_skip_8k(self, aws_env):
        """8-K filings are not supported for delta analysis."""
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload(form_type="8-K")
        message = _make_sqs_message(payload)

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta") as mock_analyze:
            service.process_message(message)
            mock_analyze.assert_not_called()


# ---------------------------------------------------------------------------
# Target sections by form type
# ---------------------------------------------------------------------------

class TestTargetSections:
    def test_10k_targets_item7_and_item1a(self, aws_env):
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload(form_type="10-K")
        message = _make_sqs_message(payload)

        call_sections = []

        async def fake_analyze(current_text, previous_text, entity_id,
                               form_type, section_name, current_date, previous_date):
            call_sections.append(section_name)
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        assert "item_7" in call_sections
        assert "item_1a" in call_sections

    def test_10q_targets_part1_item2_and_part2_item1a(self, aws_env):
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload(form_type="10-Q")
        payload["current_s3_prefix"] = "s3://test-bucket/filings/AAPL/10-Q/2026-06-01/sections"
        payload["prior_s3_prefix"] = "s3://test-bucket/filings/AAPL/10-Q/2025-06-01/sections"
        message = _make_sqs_message(payload)

        call_sections = []

        async def fake_analyze(current_text, previous_text, entity_id,
                               form_type, section_name, current_date, previous_date):
            call_sections.append(section_name)
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        assert "part1_item2" in call_sections
        assert "part2_item1a" in call_sections


# ---------------------------------------------------------------------------
# DynamoDB writes
# ---------------------------------------------------------------------------

class TestDynamoDBWrites:
    def test_stores_section_results(self, aws_env):
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        async def fake_analyze(**kwargs):
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        # Check semantic_deltas table
        response = service._deltas_table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"ENTITY#{ENTITY_ID}"},
        )
        items = response.get("Items", [])
        assert len(items) >= 1

        item = items[0]
        assert item["entity_id"] == ENTITY_ID
        assert item["pair_id"] == PAIR_ID
        assert item["source"] == "semantic_delta"
        assert "shifts" in item
        assert item["shift_count"] == 2

    def test_stores_shadow_score(self, aws_env):
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        async def fake_analyze(**kwargs):
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        # Check shadow_scores table
        response = service._shadow_table.get_item(Key={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": f"SHADOW#{PAIR_ID}#semantic_delta",
        })
        item = response["Item"]
        assert item["entity_id"] == ENTITY_ID
        assert item["edge_name"] == "semantic_delta"
        assert item["pair_id"] == PAIR_ID
        assert "composite_score" in item
        assert "dominant_direction" in item


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------

class TestEventEmission:
    def test_emits_delta_complete(self, aws_env):
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        async def fake_analyze(**kwargs):
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        sqs = boto3.client("sqs", region_name="us-east-1")
        messages = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
        )
        assert len(messages.get("Messages", [])) == 1

        body = json.loads(messages["Messages"][0]["Body"])
        assert body["event_type"] == "DELTA_ANALYSIS_COMPLETE"
        assert body["payload"]["entity_id"] == ENTITY_ID
        assert body["payload"]["pair_id"] == PAIR_ID
        assert body["payload"]["form_type"] == FORM_TYPE_10K
        assert "sections_analyzed" in body["payload"]
        assert "shift_count" in body["payload"]

    def test_no_output_queue_silently_skips(self, aws_env):
        service = SemanticDeltaService()
        service._output_queue_url = ""
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        async def fake_analyze(**kwargs):
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)
        # Should not raise


# ---------------------------------------------------------------------------
# Missing section handling
# ---------------------------------------------------------------------------

class TestMissingSections:
    def test_skips_missing_sections(self, aws_env):
        """If a section is missing from S3, skip it but process others."""
        # Remove item_1a from current
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.delete_object(
            Bucket="test-bucket",
            Key="filings/AAPL/10-K/2026-03-01/sections/item_1a.txt",
        )

        service = SemanticDeltaService()
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        call_sections = []

        async def fake_analyze(current_text, previous_text, entity_id,
                               form_type, section_name, current_date, previous_date):
            call_sections.append(section_name)
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        # Only item_7 should be analyzed (item_1a missing in current)
        assert "item_7" in call_sections
        assert "item_1a" not in call_sections


# ---------------------------------------------------------------------------
# No shifts scenario
# ---------------------------------------------------------------------------

class TestNoShifts:
    def test_empty_shifts_produces_zero_score(self, aws_env):
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        async def fake_analyze(**kwargs):
            return []

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        # Shadow score should exist with zero composite
        response = service._shadow_table.get_item(Key={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": f"SHADOW#{PAIR_ID}#semantic_delta",
        })
        item = response["Item"]
        assert item["composite_score"] == Decimal("0.0")
        assert item["dominant_direction"] == "neutral"
        assert item["shift_count"] == 0


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------

class TestExceptionHandling:
    def test_bad_json_message_does_not_crash(self, aws_env):
        service = SemanticDeltaService()
        bad_message = {"MessageId": "bad", "Body": "not json"}
        service.process_message(bad_message)  # Should not raise

    def test_analyzer_exception_does_not_crash(self, aws_env):
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        async def failing_analyze(**kwargs):
            raise RuntimeError("Claude exploded")

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=failing_analyze):
            service.process_message(message)  # Should not raise


# ---------------------------------------------------------------------------
# Full integration
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_end_to_end_10k(self, aws_env):
        """Full: SQS message → S3 load → analyze → score → DynamoDB → SQS out."""
        service = SemanticDeltaService()
        payload = _make_pair_ready_payload()
        message = _make_sqs_message(payload)

        async def fake_analyze(**kwargs):
            return SAMPLE_SHIFTS

        with patch("engine.ai_edges.semantic_delta.service.analyze_delta", side_effect=fake_analyze):
            service.process_message(message)

        # Verify per-section deltas
        response = service._deltas_table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"ENTITY#{ENTITY_ID}"},
        )
        items = response.get("Items", [])
        assert len(items) == 2  # item_7 + item_1a

        section_names = {item["section_name"] for item in items}
        assert section_names == {"item_7", "item_1a"}

        # Verify shadow score
        response = service._shadow_table.get_item(Key={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": f"SHADOW#{PAIR_ID}#semantic_delta",
        })
        assert "Item" in response

        # Verify event emission
        sqs = boto3.client("sqs", region_name="us-east-1")
        messages = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
        )
        assert len(messages.get("Messages", [])) == 1
        body = json.loads(messages["Messages"][0]["Body"])
        assert body["payload"]["sections_analyzed"] == ["item_7", "item_1a"]
        assert body["payload"]["shift_count"] == 4  # 2 shifts * 2 sections
