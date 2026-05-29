"""Unit tests for the FilingIndexerService (pair + chain indexing)."""

from __future__ import annotations

import json
import os
import uuid

import boto3
import pytest
from moto import mock_aws
from unittest.mock import patch, MagicMock

from signalfft_common.events import BaseEvent, FilingSectionsReady, FilingPairReady, FilingChainReady


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = "2026-02-15T00:00:00+00:00"
TRACE = str(uuid.uuid4())

SAMPLE_HISTORY = [
    {"accession_number": "0000320193-24-000015", "filing_date": "2024-02-15", "form_type": "10-K"},
    {"accession_number": "0000320193-25-000010", "filing_date": "2025-02-14", "form_type": "10-K"},
    {"accession_number": "0000320193-26-000012", "filing_date": "2026-02-15", "form_type": "10-K"},
]


def _make_sections_ready_message(
    entity_id: str = "AAPL",
    cik: str = "320193",
    form_type: str = "10-K",
    filing_date: str = "2026-02-15",
    section_s3_prefix: str = "s3://bucket/filings/320193/10-K/2026-02-15/sections",
) -> dict:
    """Create a mock SQS message containing a FilingSectionsReady event."""
    event = FilingSectionsReady(
        timestamp=NOW,
        source="section_extractor",
        trace_id=TRACE,
        payload={
            "event_id": "evt-001",
            "entity_id": entity_id,
            "cik": cik,
            "form_type": form_type,
            "filing_date": filing_date,
            "sections_available": ["item_1", "item_1a"],
            "section_s3_prefix": section_s3_prefix,
            "total_text_length": 50000,
        },
    )
    return {
        "MessageId": str(uuid.uuid4()),
        "ReceiptHandle": "test-receipt",
        "Body": event.to_sqs_message(),
    }


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
        os.environ["EDGAR_USER_AGENT"] = "TestAgent/1.0"

        sqs = boto3.client("sqs", region_name=region)
        input_q = sqs.create_queue(QueueName="test-filing-indexer")
        output_q = sqs.create_queue(QueueName="test-filing-index-ready")
        os.environ["FILING_INDEXER_QUEUE_URL"] = input_q["QueueUrl"]
        os.environ["FILING_INDEX_READY_QUEUE_URL"] = output_q["QueueUrl"]

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
            "table_name": table_name,
            "input_queue_url": input_q["QueueUrl"],
            "output_queue_url": output_q["QueueUrl"],
        }


def _make_service(aws_env):
    """Create a FilingIndexerService after env vars are set."""
    from engine.filing_processing.indexer import FilingIndexerService
    return FilingIndexerService()


def _seed_prior_sections(aws_env, entity_id: str, form_type: str, filing_date: str, s3_prefix: str):
    """Seed a filing_sections record in DynamoDB for prior filing lookup."""
    dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
    table = dynamo.Table(aws_env["table_name"])
    table.put_item(Item={
        "PK": f"ENTITY#{entity_id}",
        "SK": f"SECTIONS#{form_type}#{filing_date}",
        "entity_id": entity_id,
        "form_type": form_type,
        "filing_date": filing_date,
        "section_s3_prefix": s3_prefix,
        "sections_available": ["item_1", "item_1a"],
        "total_text_length": 30000,
        "created_at": NOW,
        "source": "section_extractor",
    })


# ===========================================================================
# Pair tests (F1.4)
# ===========================================================================


class TestBuildFilingPair:
    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_pair_with_prior_filing_and_sections(self, mock_fetch, aws_env):
        """When prior filing exists and has sections, both prefixes should be set."""
        mock_fetch.return_value = SAMPLE_HISTORY

        # Seed prior filing's sections in DynamoDB
        _seed_prior_sections(
            aws_env, "AAPL", "10-K", "2025-02-14",
            "s3://bucket/filings/320193/10-K/2025-02-14/sections",
        )

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        # Verify pair record in DynamoDB
        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "PAIR#10-K#2026-02-15",
        })
        item = response["Item"]
        assert item["entity_id"] == "AAPL"
        assert item["form_type"] == "10-K"
        assert item["current_filing_date"] == "2026-02-15"
        assert item["prior_filing_date"] == "2025-02-14"
        assert item["current_s3_prefix"] == "s3://bucket/filings/320193/10-K/2026-02-15/sections"
        assert item["prior_s3_prefix"] == "s3://bucket/filings/320193/10-K/2025-02-14/sections"
        assert "pair_id" in item
        assert "created_at" in item

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_pair_no_prior_filing(self, mock_fetch, aws_env):
        """First filing of this type should have None for prior fields."""
        # Only one filing in history — the current one
        mock_fetch.return_value = [
            {"accession_number": "0000320193-26-000012", "filing_date": "2026-02-15", "form_type": "10-K"},
        ]

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "PAIR#10-K#2026-02-15",
        })
        item = response["Item"]
        assert item["prior_filing_date"] is None
        assert item["prior_s3_prefix"] is None

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_pair_prior_sections_not_yet_processed(self, mock_fetch, aws_env):
        """Prior filing exists in history but hasn't been section-extracted yet."""
        mock_fetch.return_value = SAMPLE_HISTORY
        # Do NOT seed prior sections — simulating not-yet-processed

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "PAIR#10-K#2026-02-15",
        })
        item = response["Item"]
        assert item["prior_filing_date"] == "2025-02-14"
        assert item["prior_s3_prefix"] is None  # Not yet processed

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_pair_dynamo_write_correct_pk_sk(self, mock_fetch, aws_env):
        """Pair record should use ENTITY#{entity} / PAIR#{form}#{date} keys."""
        mock_fetch.return_value = SAMPLE_HISTORY

        service = _make_service(aws_env)
        message = _make_sections_ready_message(entity_id="BSX", form_type="10-Q", filing_date="2026-01-01")
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#BSX",
            "SK": "PAIR#10-Q#2026-01-01",
        })
        assert "Item" in response

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_pair_empty_history(self, mock_fetch, aws_env):
        """When SEC returns no history, pair should still be written with no prior."""
        mock_fetch.return_value = []

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "PAIR#10-K#2026-02-15",
        })
        item = response["Item"]
        assert item["prior_filing_date"] is None
        assert item["prior_s3_prefix"] is None


# ===========================================================================
# Chain tests (F1.5)
# ===========================================================================


class TestBuildFilingChain:
    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_chain_with_history(self, mock_fetch, aws_env):
        """Chain should contain all filing dates from history."""
        mock_fetch.return_value = SAMPLE_HISTORY

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "CHAIN#10-K",
        })
        item = response["Item"]
        assert item["entity_id"] == "AAPL"
        assert item["form_type"] == "10-K"
        assert item["chain_length"] == 3
        assert item["latest_filing_date"] == "2026-02-15"
        assert item["filing_dates"] == ["2024-02-15", "2025-02-14", "2026-02-15"]
        assert "chain_id" in item
        assert "updated_at" in item

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_chain_first_filing(self, mock_fetch, aws_env):
        """First filing should create chain with length 1."""
        mock_fetch.return_value = [
            {"accession_number": "acc-001", "filing_date": "2026-02-15", "form_type": "10-K"},
        ]

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "CHAIN#10-K",
        })
        item = response["Item"]
        assert item["chain_length"] == 1
        assert item["filing_dates"] == ["2026-02-15"]

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_chain_upsert_preserves_chain_id(self, mock_fetch, aws_env):
        """Re-processing should preserve the existing chain_id."""
        mock_fetch.return_value = SAMPLE_HISTORY[:2]  # First 2 filings

        service = _make_service(aws_env)
        message = _make_sections_ready_message(filing_date="2025-02-14")
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        first = table.get_item(Key={"PK": "ENTITY#AAPL", "SK": "CHAIN#10-K"})
        original_chain_id = first["Item"]["chain_id"]

        # Process again with full history
        mock_fetch.return_value = SAMPLE_HISTORY
        message2 = _make_sections_ready_message(filing_date="2026-02-15")
        service.process_message(message2)

        second = table.get_item(Key={"PK": "ENTITY#AAPL", "SK": "CHAIN#10-K"})
        assert second["Item"]["chain_id"] == original_chain_id
        assert second["Item"]["chain_length"] == 3

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_chain_empty_history_includes_current(self, mock_fetch, aws_env):
        """Even with empty SEC history, the current filing should be in chain."""
        mock_fetch.return_value = []

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "CHAIN#10-K",
        })
        item = response["Item"]
        assert item["chain_length"] == 1
        assert item["filing_dates"] == ["2026-02-15"]

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_chain_no_date_component_in_sk(self, mock_fetch, aws_env):
        """Chain SK should be CHAIN#{form_type} only (no date)."""
        mock_fetch.return_value = SAMPLE_HISTORY

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        response = table.get_item(Key={
            "PK": "ENTITY#AAPL",
            "SK": "CHAIN#10-K",
        })
        assert "Item" in response
        assert response["Item"]["SK"] == "CHAIN#10-K"


# ===========================================================================
# Event emission tests
# ===========================================================================


class TestEventEmission:
    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_pair_ready_emitted(self, mock_fetch, aws_env):
        """FilingPairReady event should be sent to output queue."""
        mock_fetch.return_value = SAMPLE_HISTORY
        _seed_prior_sections(
            aws_env, "AAPL", "10-K", "2025-02-14",
            "s3://bucket/filings/320193/10-K/2025-02-14/sections",
        )

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        # Should have both a pair and chain event
        events = [BaseEvent.from_sqs_message(m["Body"]) for m in messages]
        event_types = {e.event_type for e in events}
        assert "FILING_PAIR_READY" in event_types
        assert "FILING_CHAIN_READY" in event_types

        pair_event = next(e for e in events if e.event_type == "FILING_PAIR_READY")
        assert isinstance(pair_event, FilingPairReady)
        assert pair_event.payload["entity_id"] == "AAPL"
        assert pair_event.payload["current_filing_date"] == "2026-02-15"
        assert pair_event.payload["prior_filing_date"] == "2025-02-14"
        assert pair_event.payload["pair_id"] is not None

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_chain_ready_emitted(self, mock_fetch, aws_env):
        """FilingChainReady event should be sent to output queue."""
        mock_fetch.return_value = SAMPLE_HISTORY

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        events = [BaseEvent.from_sqs_message(m["Body"]) for m in messages]
        chain_event = next(e for e in events if e.event_type == "FILING_CHAIN_READY")
        assert isinstance(chain_event, FilingChainReady)
        assert chain_event.payload["chain_length"] == 3
        assert chain_event.payload["filing_dates"] == ["2024-02-15", "2025-02-14", "2026-02-15"]

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_no_emission_without_queue_url(self, mock_fetch, aws_env):
        """No events should be sent when output queue URL is empty."""
        mock_fetch.return_value = SAMPLE_HISTORY

        service = _make_service(aws_env)
        service._output_queue_url = ""
        message = _make_sections_ready_message()
        service.process_message(message)

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
        )
        assert len(response.get("Messages", [])) == 0


# ===========================================================================
# Service integration / process_message tests
# ===========================================================================


class TestProcessMessage:
    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_end_to_end(self, mock_fetch, aws_env):
        """Full flow: consume event → backfill → pair + chain → emit."""
        mock_fetch.return_value = SAMPLE_HISTORY
        _seed_prior_sections(
            aws_env, "AAPL", "10-K", "2025-02-14",
            "s3://bucket/filings/320193/10-K/2025-02-14/sections",
        )

        service = _make_service(aws_env)
        message = _make_sections_ready_message()
        service.process_message(message)

        # Verify pair in DynamoDB
        dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamo.Table(aws_env["table_name"])
        pair = table.get_item(Key={"PK": "ENTITY#AAPL", "SK": "PAIR#10-K#2026-02-15"})
        assert "Item" in pair

        # Verify chain in DynamoDB
        chain = table.get_item(Key={"PK": "ENTITY#AAPL", "SK": "CHAIN#10-K"})
        assert "Item" in chain
        assert chain["Item"]["chain_length"] == 3

        # Verify events emitted
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        response = sqs.receive_message(
            QueueUrl=aws_env["output_queue_url"],
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
        )
        assert len(response.get("Messages", [])) == 2

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_backfill_called_with_correct_args(self, mock_fetch, aws_env):
        """fetch_filing_history should be called with cik, form_type, user_agent."""
        mock_fetch.return_value = []

        service = _make_service(aws_env)
        message = _make_sections_ready_message(cik="999888", form_type="8-K")
        service.process_message(message)

        mock_fetch.assert_called_once_with("999888", "8-K", "TestAgent/1.0")

    @patch("engine.filing_processing.indexer.fetch_filing_history")
    def test_service_initialization(self, mock_fetch, aws_env):
        """Service should pick up env vars correctly."""
        service = _make_service(aws_env)
        assert service.input_queue_url == aws_env["input_queue_url"]
        assert service._output_queue_url == aws_env["output_queue_url"]
        assert service._user_agent == "TestAgent/1.0"
