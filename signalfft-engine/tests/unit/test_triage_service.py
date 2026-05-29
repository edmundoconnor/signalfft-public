"""Tests for QuietFilingTriageService — SQS consumer, DynamoDB writes,
shadow scoring, fan-out, and caching."""

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

from engine.ai_edges.quiet_filing_triage.service import QuietFilingTriageService
from engine.ai_edges.quiet_filing_triage.triage import (
    TriageAssessment,
    clear_prompt_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENTITY_ID = "AAPL"
EVENT_ID = "evt-001"
CIK = "0000320193"
FORM_TYPE = "8-K"
FILING_DATE = "2026-02-25"

SAMPLE_ASSESSMENT = TriageAssessment(
    materiality_score=8,
    attention_likelihood="low",
    direction="bearish",
    reasoning="CEO resignation disclosed in after-hours filing.",
    key_material_items=["CEO resignation effective March 1"],
    suggested_urgency="act",
    is_quiet_filing=True,
    boost_multiplier=1.5,
    claude_model_version="claude-sonnet-4-20250514",
    prompt_version="abc123",
    entity_id=ENTITY_ID,
    form_type=FORM_TYPE,
    filing_date=FILING_DATE,
    input_tokens=5000,
    output_tokens=200,
    estimated_cost=0.018,
    created_at=datetime.now(timezone.utc).isoformat(),
)

SECTIONS_READY_PAYLOAD = {
    "event_id": EVENT_ID,
    "entity_id": ENTITY_ID,
    "cik": CIK,
    "form_type": FORM_TYPE,
    "filing_date": FILING_DATE,
    "sections_available": ["Item1", "Item7", "full_text"],
    "section_s3_prefix": "s3://test-bucket/filings/0000320193/8-K/2026-02-25/sections",
    "total_text_length": 15000,
}

VALID_CLAUDE_JSON = json.dumps({
    "materiality_score": 8,
    "attention_likelihood": "low",
    "direction": "bearish",
    "reasoning": "CEO resignation disclosed in after-hours filing.",
    "key_material_items": ["CEO resignation effective March 1"],
    "suggested_urgency": "act",
})


def _make_sqs_message(payload: dict) -> dict:
    """Build a mock SQS message wrapping a FilingSectionsReady event."""
    event = {
        "event_type": "FILING_SECTIONS_READY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "section_extractor",
        "trace_id": str(uuid.uuid4()),
        "payload": payload,
    }
    return {
        "MessageId": "msg-001",
        "Body": json.dumps(event),
        "ReceiptHandle": "receipt-001",
    }


def _mock_claude_response(text: str = VALID_CLAUDE_JSON):
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=5000, output_tokens=200)
    return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear():
    clear_prompt_cache()
    yield
    clear_prompt_cache()


@pytest.fixture
def aws_env():
    """Set up mock AWS resources: DynamoDB tables, SQS queues, S3 bucket."""
    with mock_aws():
        region = "us-east-1"
        env = "test"
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region
        os.environ["ENVIRONMENT"] = env

        # DynamoDB tables
        dynamodb = boto3.client("dynamodb", region_name=region)

        for table_name in [
            f"{env}-signalfft-events",
            f"{env}-signalfft-features",
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
        triage_q = sqs.create_queue(QueueName=f"{env}-signalfft-triage-input")
        output_q = sqs.create_queue(QueueName=f"{env}-signalfft-sections-ready")

        os.environ["TRIAGE_INPUT_QUEUE_URL"] = triage_q["QueueUrl"]
        os.environ["SECTIONS_READY_QUEUE_URL"] = output_q["QueueUrl"]

        # S3 bucket
        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket="test-bucket")

        # Put a section file so _load_section_text works
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/0000320193/8-K/2026-02-25/sections/Item7.txt",
            Body=b"The company's revenue declined 15% year-over-year...",
        )
        s3.put_object(
            Bucket="test-bucket",
            Key="filings/0000320193/8-K/2026-02-25/sections/Item1.txt",
            Body=b"Business overview section text.",
        )

        yield {
            "region": region,
            "env": env,
            "triage_queue_url": triage_q["QueueUrl"],
            "output_queue_url": output_q["QueueUrl"],
        }

        # Cleanup env
        for key in ["AWS_REGION", "AWS_DEFAULT_REGION", "ENVIRONMENT",
                     "TRIAGE_INPUT_QUEUE_URL", "SECTIONS_READY_QUEUE_URL"]:
            os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# _load_section_text
# ---------------------------------------------------------------------------

class TestLoadSectionText:
    def test_prefers_item7_mda(self, aws_env):
        service = QuietFilingTriageService()
        text = service._load_section_text(
            "s3://test-bucket/filings/0000320193/8-K/2026-02-25/sections",
            ["Item1", "Item7", "full_text"],
        )
        assert "revenue declined" in text

    def test_fallback_to_lower_priority(self, aws_env):
        service = QuietFilingTriageService()
        text = service._load_section_text(
            "s3://test-bucket/filings/0000320193/8-K/2026-02-25/sections",
            ["Item1", "full_text"],  # no Item7
        )
        assert "Business overview" in text

    def test_empty_sections_returns_empty(self, aws_env):
        service = QuietFilingTriageService()
        text = service._load_section_text(
            "s3://test-bucket/filings/0000320193/8-K/2026-02-25/sections",
            [],
        )
        assert text == ""


# ---------------------------------------------------------------------------
# _is_cached
# ---------------------------------------------------------------------------

class TestIsCached:
    def test_not_cached_empty_table(self, aws_env):
        service = QuietFilingTriageService()
        assert service._is_cached(ENTITY_ID, FORM_TYPE, FILING_DATE, "v1") is False

    def test_cached_after_store(self, aws_env):
        service = QuietFilingTriageService()

        # Manually write a triage record
        service._events_table.put_item(Item={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": f"TRIAGE#{FILING_DATE}#{EVENT_ID}",
            "prompt_version": "v1",
        })

        assert service._is_cached(ENTITY_ID, FORM_TYPE, FILING_DATE, "v1") is True

    def test_different_prompt_version_not_cached(self, aws_env):
        service = QuietFilingTriageService()

        service._events_table.put_item(Item={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": f"TRIAGE#{FILING_DATE}#{EVENT_ID}",
            "prompt_version": "v1",
        })

        assert service._is_cached(ENTITY_ID, FORM_TYPE, FILING_DATE, "v2") is False


# ---------------------------------------------------------------------------
# _store_triage_result
# ---------------------------------------------------------------------------

class TestStoreTriageResult:
    def test_writes_to_dynamodb(self, aws_env):
        service = QuietFilingTriageService()
        service._store_triage_result(SAMPLE_ASSESSMENT, EVENT_ID)

        response = service._events_table.get_item(Key={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": f"TRIAGE#{FILING_DATE}#{EVENT_ID}",
        })
        item = response["Item"]
        assert item["entity_id"] == ENTITY_ID
        assert item["materiality_score"] == 8
        assert item["direction"] == "bearish"
        assert item["is_quiet_filing"] is True
        assert item["source"] == "quiet_filing_triage"
        assert item["boost_multiplier"] == Decimal("1.5")


# ---------------------------------------------------------------------------
# _emit_triage_complete
# ---------------------------------------------------------------------------

class TestEmitTriageComplete:
    def test_emits_to_output_queue(self, aws_env):
        service = QuietFilingTriageService()
        service._emit_triage_complete(SAMPLE_ASSESSMENT, EVENT_ID, "trace-001")

        sqs = boto3.client("sqs", region_name="us-east-1")
        messages = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
        )
        assert len(messages.get("Messages", [])) == 1

        body = json.loads(messages["Messages"][0]["Body"])
        assert body["event_type"] == "TRIAGE_COMPLETE"
        assert body["payload"]["entity_id"] == ENTITY_ID
        assert body["payload"]["materiality_score"] == 8

    def test_no_queue_configured_silently_skips(self, aws_env):
        service = QuietFilingTriageService()
        service.output_queue_url = ""
        # Should not raise
        service._emit_triage_complete(SAMPLE_ASSESSMENT, EVENT_ID, "trace-001")


# ---------------------------------------------------------------------------
# store_shadow_score
# ---------------------------------------------------------------------------

class TestStoreShadowScore:
    def test_writes_shadow_score(self, aws_env):
        service = QuietFilingTriageService()
        service.store_shadow_score(
            entity_id=ENTITY_ID,
            signal_id="sig-001",
            original_score=0.75,
            assessment=SAMPLE_ASSESSMENT,
        )

        response = service._shadow_table.get_item(Key={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": "SHADOW#sig-001#quiet_filing_triage",
        })
        item = response["Item"]
        assert item["original_score"] == Decimal("0.75")
        assert item["shadow_score"] == Decimal("1.125")  # 0.75 * 1.5
        assert item["boost_applied"] is True
        assert item["edge_name"] == "quiet_filing_triage"

    def test_no_boost_shadow_score(self, aws_env):
        non_quiet = TriageAssessment(
            materiality_score=3,
            attention_likelihood="high",
            direction="neutral",
            reasoning="Routine filing.",
            key_material_items=[],
            suggested_urgency="monitor",
            is_quiet_filing=False,
            boost_multiplier=1.0,
            claude_model_version="test",
            prompt_version="v1",
            entity_id=ENTITY_ID,
            form_type="10-K",
            filing_date=FILING_DATE,
        )
        service = QuietFilingTriageService()
        service.store_shadow_score(ENTITY_ID, "sig-002", 0.5, non_quiet)

        response = service._shadow_table.get_item(Key={
            "PK": f"ENTITY#{ENTITY_ID}",
            "SK": "SHADOW#sig-002#quiet_filing_triage",
        })
        item = response["Item"]
        assert item["shadow_score"] == Decimal("0.5")  # 0.5 * 1.0
        assert item["boost_applied"] is False


# ---------------------------------------------------------------------------
# process_message (full integration)
# ---------------------------------------------------------------------------

class TestProcessMessage:
    def test_full_pipeline(self, aws_env):
        """End-to-end: SQS message → S3 read → Claude → DynamoDB → SQS out."""
        mock_resp = _mock_claude_response()

        with patch("engine.ai_edges.quiet_filing_triage.triage.anthropic") as mock_mod:
            mock_client = MagicMock()
            mock_mod.AsyncAnthropic.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_resp)

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                service = QuietFilingTriageService()
                message = _make_sqs_message(SECTIONS_READY_PAYLOAD)
                service.process_message(message)
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)

        # Verify triage result in DynamoDB
        response = service._events_table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"ENTITY#{ENTITY_ID}",
                ":sk": f"TRIAGE#{FILING_DATE}",
            },
        )
        items = response.get("Items", [])
        assert len(items) == 1
        assert items[0]["materiality_score"] == 8
        assert items[0]["direction"] == "bearish"

        # Verify TriageComplete event in output queue
        sqs = boto3.client("sqs", region_name="us-east-1")
        messages = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
        )
        assert len(messages.get("Messages", [])) == 1

    def test_skips_when_cached(self, aws_env):
        """Message is skipped if triage already cached for same prompt version."""
        # Pre-populate cache
        service = QuietFilingTriageService()
        prompt_version = "abc123"

        # We need to mock get_prompt_version to return a known value
        with patch("engine.ai_edges.quiet_filing_triage.service.get_prompt_version", return_value=prompt_version):
            service._events_table.put_item(Item={
                "PK": f"ENTITY#{ENTITY_ID}",
                "SK": f"TRIAGE#{FILING_DATE}#{EVENT_ID}",
                "prompt_version": prompt_version,
            })

            message = _make_sqs_message(SECTIONS_READY_PAYLOAD)
            service.process_message(message)

        # No TriageComplete event should be emitted
        sqs = boto3.client("sqs", region_name="us-east-1")
        messages = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
        )
        assert len(messages.get("Messages", [])) == 0

    def test_skips_when_no_section_text(self, aws_env):
        """If no section text found in S3, skip triage."""
        payload = dict(SECTIONS_READY_PAYLOAD)
        payload["section_s3_prefix"] = "s3://test-bucket/filings/nonexistent/path"
        payload["sections_available"] = ["missing_section"]

        service = QuietFilingTriageService()
        message = _make_sqs_message(payload)
        service.process_message(message)

        # No triage record should be written
        response = service._events_table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"ENTITY#{ENTITY_ID}",
                ":sk": "TRIAGE#",
            },
        )
        assert len(response.get("Items", [])) == 0

    def test_exception_does_not_propagate(self, aws_env):
        """Errors are logged but don't crash the service."""
        bad_message = {
            "MessageId": "bad-msg",
            "Body": "not valid json at all",
        }
        service = QuietFilingTriageService()
        # Should not raise
        service.process_message(bad_message)


# ---------------------------------------------------------------------------
# _get_tier1_keywords
# ---------------------------------------------------------------------------

class TestGetTier1Keywords:
    def test_returns_matched_terms(self, aws_env):
        service = QuietFilingTriageService()

        # Seed features table with a TRIAGE feature
        service._features_table.put_item(Item={
            "PK": f"EVENT#{EVENT_ID}",
            "SK": "FEATURE#feat-001",
            "feature_type": "TRIAGE",
            "value": {
                "matched_terms": [
                    {"term": "merger", "category": "corporate_actions"},
                    {"term": "acquisition", "category": "corporate_actions"},
                ],
            },
        })

        keywords = service._get_tier1_keywords(EVENT_ID)
        assert len(keywords) == 2
        assert keywords[0]["term"] == "merger"

    def test_no_features_returns_empty(self, aws_env):
        service = QuietFilingTriageService()
        keywords = service._get_tier1_keywords("nonexistent-event")
        assert keywords == []


# ---------------------------------------------------------------------------
# Fan-out from SectionExtractorService
# ---------------------------------------------------------------------------

class TestSectionExtractorFanOut:
    def test_triage_input_queue_configured(self, aws_env):
        """Verify SectionExtractorService has triage_input_queue_url attribute."""
        os.environ["TRIAGE_INPUT_QUEUE_URL"] = "https://sqs.us-east-1.amazonaws.com/test/triage"
        os.environ["FILING_READY_QUEUE_URL"] = "https://sqs.us-east-1.amazonaws.com/test/ready"
        try:
            from engine.filing_processing.service import SectionExtractorService
            svc = SectionExtractorService()
            assert svc.triage_input_queue_url == "https://sqs.us-east-1.amazonaws.com/test/triage"
            assert hasattr(svc, "_emit_to_triage_input")
        finally:
            os.environ.pop("TRIAGE_INPUT_QUEUE_URL", None)
            os.environ.pop("FILING_READY_QUEUE_URL", None)
