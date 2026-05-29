"""Comprehensive tests for the SEC EDGAR filing collector."""

from __future__ import annotations

import json
import os
import sys

import boto3
import pytest
import responses
from moto import mock_aws

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collectors.edgar.collector import EdgarCollector, lambda_handler, FILING_TYPES


# ---------------------------------------------------------------------------
# Mock EDGAR EFTS API response
# ---------------------------------------------------------------------------

MOCK_EFTS_RESPONSE = {
    "hits": {
        "hits": [
            {
                "_source": {
                    "adsh": "0001234567-26-000001",
                    "form": "10-K",
                    "display_names": ["ACME Corp (ACME)"],
                    "ciks": ["1234567"],
                    "file_date": "2026-02-14",
                    "file_description": "Annual report",
                }
            },
            {
                "_source": {
                    "adsh": "0009876543-26-000002",
                    "form": "10-K",
                    "display_names": ["Widget Inc (WDGT)"],
                    "ciks": ["9876543"],
                    "file_date": "2026-02-14",
                    "file_description": "Annual report",
                }
            },
        ]
    }
}

# Mock SEC CIK->ticker mapping for EntityResolver
MOCK_SEC_TICKERS = {
    "0": {"cik_str": 1234567, "ticker": "ACME", "title": "ACME Corp"},
    "1": {"cik_str": 9876543, "ticker": "WDGT", "title": "Widget Inc"},
    "2": {"cik_str": 42, "ticker": "TINY", "title": "Tiny Co"},
}

MOCK_EFTS_EMPTY_RESPONSE = {"hits": {"hits": []}}


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

        bucket_name = f"{env}-signalfft-artifacts"
        os.environ["ARTIFACT_BUCKET"] = bucket_name

        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=bucket_name)

        sqs = boto3.client("sqs", region_name=region)
        queue = sqs.create_queue(QueueName="test-raw-events")
        os.environ["RAW_EVENTS_QUEUE_URL"] = queue["QueueUrl"]

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


def _register_efts_responses(response_json=None, status=200):
    """Register mocked EDGAR EFTS URL and SEC tickers URL."""
    body = response_json if response_json is not None else MOCK_EFTS_RESPONSE
    responses.add(
        responses.GET,
        "https://efts.sec.gov/LATEST/search-index",
        json=body,
        status=status,
    )
    # Register SEC company_tickers.json for the EntityResolver
    responses.add(
        responses.GET,
        "https://www.sec.gov/files/company_tickers.json",
        json=MOCK_SEC_TICKERS,
        status=200,
    )


# ---------------------------------------------------------------------------
# Test 1: source_name
# ---------------------------------------------------------------------------

class TestEdgarSourceName:
    def test_edgar_source_name(self, aws_env):
        """source_name is 'SEC_EDGAR'."""
        collector = EdgarCollector()
        assert collector.source_name == "SEC_EDGAR"


# ---------------------------------------------------------------------------
# Test 2 & 3: extract_entity_id
# ---------------------------------------------------------------------------

class TestExtractEntityId:
    @responses.activate
    def test_extract_entity_id_resolves_cik_to_ticker(self, aws_env):
        """CIK 1234567 resolves to ticker via SEC mapping."""
        _register_efts_responses()
        collector = EdgarCollector()
        doc = {"cik": "1234567"}
        assert collector.extract_entity_id(doc) == "ACME"

    def test_extract_entity_id_missing(self, aws_env):
        """Missing CIK -> 'CIK_UNKNOWN'."""
        collector = EdgarCollector()
        doc = {"form_type": "10-K"}
        assert collector.extract_entity_id(doc) == "CIK_UNKNOWN"

    def test_extract_entity_id_empty_string(self, aws_env):
        """Empty string CIK -> 'CIK_UNKNOWN'."""
        collector = EdgarCollector()
        doc = {"cik": ""}
        assert collector.extract_entity_id(doc) == "CIK_UNKNOWN"

    @responses.activate
    def test_extract_entity_id_resolves_short_cik(self, aws_env):
        """Short CIK values are resolved to ticker via SEC mapping."""
        _register_efts_responses()
        collector = EdgarCollector()
        doc = {"cik": "42"}
        assert collector.extract_entity_id(doc) == "TINY"

    def test_extract_entity_id_unknown_cik_uses_prefix(self, aws_env):
        """Unknown CIK falls back to CIK_ prefix."""
        collector = EdgarCollector()
        doc = {"cik": "9999999"}
        assert collector.extract_entity_id(doc) == "CIK_9999999"

    @responses.activate
    def test_extract_entity_id_prefers_regex_ticker(self, aws_env):
        """If regex finds ticker in display_names, use that over resolver."""
        _register_efts_responses()
        collector = EdgarCollector()
        doc = {"ticker": "AAPL", "cik": "320193"}
        assert collector.extract_entity_id(doc) == "AAPL"


# ---------------------------------------------------------------------------
# Test 4, 5, 6: extract_event_type
# ---------------------------------------------------------------------------

class TestExtractEventType:
    def test_extract_event_type_10k(self, aws_env):
        """form '10-K' -> 'SEC_10K'."""
        collector = EdgarCollector()
        doc = {"form_type": "10-K"}
        assert collector.extract_event_type(doc) == "SEC_10K"

    def test_extract_event_type_8k(self, aws_env):
        """form '8-K' -> 'SEC_8K'."""
        collector = EdgarCollector()
        doc = {"form_type": "8-K"}
        assert collector.extract_event_type(doc) == "SEC_8K"

    def test_extract_event_type_def14a(self, aws_env):
        """form 'DEF 14A' -> 'SEC_DEF_14A'."""
        collector = EdgarCollector()
        doc = {"form_type": "DEF 14A"}
        assert collector.extract_event_type(doc) == "SEC_DEF_14A"

    def test_extract_event_type_10q(self, aws_env):
        """form '10-Q' -> 'SEC_10Q'."""
        collector = EdgarCollector()
        doc = {"form_type": "10-Q"}
        assert collector.extract_event_type(doc) == "SEC_10Q"

    def test_extract_event_type_missing(self, aws_env):
        """Missing form_type -> 'SEC_UNKNOWN'."""
        collector = EdgarCollector()
        doc = {}
        assert collector.extract_event_type(doc) == "SEC_UNKNOWN"


# ---------------------------------------------------------------------------
# Test 7: collect parses EFTS response
# ---------------------------------------------------------------------------

class TestCollect:
    @responses.activate
    def test_collect_parses_efts_response(self, aws_env):
        """Mock EFTS API, verify filings parsed correctly."""
        _register_efts_responses()
        collector = EdgarCollector()

        filings = collector.collect()

        assert len(filings) > 0
        first = filings[0]
        assert first["accession_number"] == "0001234567-26-000001"
        assert first["form_type"] == "10-K"
        assert first["company_name"] == "ACME Corp (ACME)"
        assert first["ticker"] == "ACME"
        assert first["cik"] == "1234567"
        assert first["filed_date"] == "2026-02-14"
        assert first["description"] == "Annual report"

    @responses.activate
    def test_collect_handles_api_error(self, aws_env):
        """Mock 500 response, collect returns empty list (doesn't crash)."""
        _register_efts_responses(status=500)
        collector = EdgarCollector()

        filings = collector.collect()

        assert filings == []

    @responses.activate
    def test_collect_respects_max_filings(self, aws_env):
        """Set max=2, verify truncation even when more filings returned."""
        # Build a response with more filings than max
        many_hits = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "adsh": f"000{i:07d}-26-000001",
                            "form": "10-K",
                            "display_names": [f"Company {i}"],
                            "ciks": [str(i)],
                            "file_date": "2026-02-14",
                            "file_description": "Annual report",
                        }
                    }
                    for i in range(10)
                ]
            }
        }
        _register_efts_responses(response_json=many_hits)

        os.environ["EDGAR_MAX_FILINGS"] = "2"
        try:
            collector = EdgarCollector()
            filings = collector.collect()
            assert len(filings) == 2
        finally:
            os.environ.pop("EDGAR_MAX_FILINGS", None)

    @responses.activate
    def test_collect_multiple_form_types(self, aws_env):
        """Each filing type triggers a separate API call."""
        _register_efts_responses()
        os.environ["EDGAR_FILING_TYPES"] = "10-K,8-K"
        try:
            collector = EdgarCollector()
            filings = collector.collect()
            # Two form types, each returns 2 filings from mock
            assert len(filings) == 4
            # 3 calls: 1 SEC tickers + 2 EFTS form types
            efts_calls = [c for c in responses.calls if "efts.sec.gov" in c.request.url]
            assert len(efts_calls) == 2
        finally:
            os.environ.pop("EDGAR_FILING_TYPES", None)


# ---------------------------------------------------------------------------
# Test 10: Full pipeline with mocked EDGAR + mocked AWS
# ---------------------------------------------------------------------------

class TestRunFullPipeline:
    @responses.activate
    def test_run_full_pipeline_with_mock_api(self, aws_env):
        """End-to-end with mocked EDGAR + mocked AWS."""
        _register_efts_responses()

        # Restrict to single form type so we get exactly 2 filings
        os.environ["EDGAR_FILING_TYPES"] = "10-K"
        try:
            collector = EdgarCollector()
            result = collector.run()

            assert result["collected"] == 2
            assert result["stored"] == 2
            assert result["duplicates"] == 0
            assert result["errors"] == 0

            # Verify S3 objects exist
            s3 = boto3.client("s3", region_name=aws_env["region"])
            objects = s3.list_objects_v2(
                Bucket=aws_env["bucket"], Prefix="raw/SEC_EDGAR/"
            )
            assert objects["KeyCount"] == 2

            # Verify DynamoDB records
            dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
            table = dynamodb.Table(aws_env["table_name"])
            scan = table.scan()
            assert len(scan["Items"]) == 2

            # Check entity IDs are resolved tickers (via regex from display_names)
            entity_ids = {item["entity_id"] for item in scan["Items"]}
            assert "ACME" in entity_ids
            assert "WDGT" in entity_ids

            # Verify SQS messages
            sqs = boto3.client("sqs", region_name=aws_env["region"])
            msgs = sqs.receive_message(
                QueueUrl=aws_env["queue_url"],
                MaxNumberOfMessages=10,
            )
            assert len(msgs.get("Messages", [])) == 2
        finally:
            os.environ.pop("EDGAR_FILING_TYPES", None)

    @responses.activate
    def test_run_deduplication_across_runs(self, aws_env):
        """Second run with same filing should detect duplicate."""
        # Register SEC tickers mock for EntityResolver
        responses.add(
            responses.GET,
            "https://www.sec.gov/files/company_tickers.json",
            json=MOCK_SEC_TICKERS,
            status=200,
        )
        # Use a response with a single filing so dedup scan reliably finds it
        single_filing_response = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "adsh": "0001234567-26-000001",
                            "form": "10-K",
                            "display_names": ["ACME Corp (ACME)"],
                            "ciks": ["1234567"],
                            "file_date": "2026-02-14",
                            "file_description": "Annual report",
                        }
                    },
                ]
            }
        }
        responses.add(
            responses.GET,
            "https://efts.sec.gov/LATEST/search-index",
            json=single_filing_response,
            status=200,
        )
        os.environ["EDGAR_FILING_TYPES"] = "10-K"
        try:
            collector1 = EdgarCollector()
            result1 = collector1.run()
            assert result1["stored"] == 1
            assert result1["duplicates"] == 0

            # Run again -- same filing should be deduped
            responses.add(
                responses.GET,
                "https://efts.sec.gov/LATEST/search-index",
                json=single_filing_response,
                status=200,
            )
            collector2 = EdgarCollector()
            result2 = collector2.run()
            assert result2["collected"] == 1
            assert result2["stored"] == 0
            assert result2["duplicates"] == 1
        finally:
            os.environ.pop("EDGAR_FILING_TYPES", None)


# ---------------------------------------------------------------------------
# Test 11: Lambda handler returns 200
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    @responses.activate
    def test_lambda_handler_returns_200(self, aws_env):
        """lambda_handler returns statusCode 200."""
        _register_efts_responses()
        os.environ["EDGAR_FILING_TYPES"] = "10-K"
        try:
            response = lambda_handler({}, None)

            assert response["statusCode"] == 200
            body = json.loads(response["body"])
            assert "collected" in body
            assert "stored" in body
            assert "duplicates" in body
            assert "errors" in body
        finally:
            os.environ.pop("EDGAR_FILING_TYPES", None)

    @responses.activate
    def test_lambda_handler_body_is_valid_json(self, aws_env):
        """Lambda handler body is parseable JSON with expected stats keys."""
        _register_efts_responses(response_json=MOCK_EFTS_EMPTY_RESPONSE)
        os.environ["EDGAR_FILING_TYPES"] = "10-K"
        try:
            response = lambda_handler({}, None)
            body = json.loads(response["body"])
            assert body["collected"] == 0
            assert body["errors"] == 0
        finally:
            os.environ.pop("EDGAR_FILING_TYPES", None)


# ---------------------------------------------------------------------------
# Test 12: Filing types from environment variable
# ---------------------------------------------------------------------------

class TestFilingTypesFromEnv:
    def test_filing_types_from_env(self, aws_env):
        """Custom EDGAR_FILING_TYPES env var overrides defaults."""
        os.environ["EDGAR_FILING_TYPES"] = "8-K,S-1"
        try:
            collector = EdgarCollector()
            assert collector._filing_types == ["8-K", "S-1"]
        finally:
            os.environ.pop("EDGAR_FILING_TYPES", None)

    def test_filing_types_default(self, aws_env):
        """Without env var, uses the default FILING_TYPES tuple."""
        os.environ.pop("EDGAR_FILING_TYPES", None)
        collector = EdgarCollector()
        assert collector._filing_types == list(FILING_TYPES)

    def test_user_agent_from_env(self, aws_env):
        """Custom EDGAR_USER_AGENT env var is used."""
        os.environ["EDGAR_USER_AGENT"] = "MyApp/1.0 (me@test.com)"
        try:
            collector = EdgarCollector()
            assert collector._user_agent == "MyApp/1.0 (me@test.com)"
            assert collector._session.headers["User-Agent"] == "MyApp/1.0 (me@test.com)"
        finally:
            os.environ.pop("EDGAR_USER_AGENT", None)


# ---------------------------------------------------------------------------
# Test: on_event_stored filing-fetch publish
# ---------------------------------------------------------------------------

class TestOnEventStoredFilingFetch:
    def test_on_event_stored_publishes_filing_document_requested(self, aws_env):
        """When FILING_FETCH_QUEUE_URL is set and doc has filing_url, publishes message."""
        from signalfft_common.events import BaseEvent

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        fetch_queue = sqs.create_queue(QueueName="test-filing-fetch")
        os.environ["FILING_FETCH_QUEUE_URL"] = fetch_queue["QueueUrl"]
        try:
            collector = EdgarCollector()
            doc = {
                "filing_url": "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/index.htm",
                "form_type": "10-K",
                "filed_date": "2026-02-14",
                "cik": "1234567",
            }
            collector.on_event_stored(event_id="evt-001", entity_id="ACME", doc=doc)

            msgs = sqs.receive_message(
                QueueUrl=fetch_queue["QueueUrl"],
                MaxNumberOfMessages=1,
            )
            assert len(msgs.get("Messages", [])) == 1

            body = msgs["Messages"][0]["Body"]
            event = BaseEvent.from_sqs_message(body)
            assert event.event_type == "FILING_DOCUMENT_REQUESTED"
            assert event.payload["event_id"] == "evt-001"
            assert event.payload["entity_id"] == "ACME"
            assert event.payload["filing_url"] == doc["filing_url"]
            assert event.payload["form_type"] == "10-K"
            assert event.payload["cik"] == "1234567"
        finally:
            os.environ.pop("FILING_FETCH_QUEUE_URL", None)

    def test_on_event_stored_skips_when_no_queue_url(self, aws_env):
        """When FILING_FETCH_QUEUE_URL is not set, no message is published."""
        os.environ.pop("FILING_FETCH_QUEUE_URL", None)
        collector = EdgarCollector()
        doc = {
            "filing_url": "https://www.sec.gov/Archives/edgar/data/1234567/index.htm",
            "form_type": "10-K",
            "filed_date": "2026-02-14",
            "cik": "1234567",
        }
        # Should not raise
        collector.on_event_stored(event_id="evt-001", entity_id="ACME", doc=doc)

    def test_on_event_stored_skips_when_no_filing_url(self, aws_env):
        """When doc has no filing_url, no message is published."""
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        fetch_queue = sqs.create_queue(QueueName="test-filing-fetch-2")
        os.environ["FILING_FETCH_QUEUE_URL"] = fetch_queue["QueueUrl"]
        try:
            collector = EdgarCollector()
            doc = {"form_type": "10-K", "filed_date": "2026-02-14", "cik": "1234567"}
            collector.on_event_stored(event_id="evt-001", entity_id="ACME", doc=doc)

            msgs = sqs.receive_message(
                QueueUrl=fetch_queue["QueueUrl"],
                MaxNumberOfMessages=1,
            )
            assert len(msgs.get("Messages", [])) == 0
        finally:
            os.environ.pop("FILING_FETCH_QUEUE_URL", None)

    def test_filing_document_requested_schema_valid(self, aws_env):
        """FilingDocumentRequested validates required payload fields."""
        from signalfft_common.events import FilingDocumentRequested

        event = FilingDocumentRequested(
            timestamp="2026-02-14T12:00:00+00:00",
            source="SEC_EDGAR",
            trace_id="trace-001",
            payload={
                "event_id": "evt-001",
                "entity_id": "ACME",
                "filing_url": "https://www.sec.gov/filing",
                "form_type": "10-K",
                "filing_date": "2026-02-14",
                "cik": "1234567",
            },
        )
        assert event.event_type == "FILING_DOCUMENT_REQUESTED"

        # Missing field should raise
        with pytest.raises(Exception):
            FilingDocumentRequested(
                timestamp="2026-02-14T12:00:00+00:00",
                source="SEC_EDGAR",
                trace_id="trace-001",
                payload={"event_id": "evt-001"},  # missing required fields
            )
