"""Comprehensive tests for the Filing Fetcher Lambda."""

from __future__ import annotations

import json
import os
import sys
import time

import boto3
import pytest
import responses
from moto import mock_aws
from unittest.mock import patch

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collectors.filing_fetch.collector import (
    FilingFetcher,
    _IndexTableParser,
    lambda_handler,
)


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_INDEX_JSON = {
    "directory": {
        "item": [
            {"name": "0001234567-26-000001-index.htm", "type": "index"},
            {"name": "primary-doc.htm", "type": "filing"},
            {"name": "exhibit1.htm", "type": "exhibit"},
        ]
    }
}

MOCK_INDEX_JSON_NO_HTM = {
    "directory": {
        "item": [
            {"name": "filing.xml", "type": "filing"},
            {"name": "data.json", "type": "data"},
        ]
    }
}

MOCK_INDEX_HTML = """
<html><body>
<table>
<tr><td><a href="primary-doc.htm">primary-doc.htm</a></td><td>Filing</td></tr>
<tr><td><a href="exhibit1.htm">exhibit1.htm</a></td><td>Exhibit</td></tr>
</table>
</body></html>
"""

MOCK_INDEX_HTML_NO_DOCS = """
<html><body>
<table>
<tr><td><a href="data.xml">data.xml</a></td><td>XBRL Data</td></tr>
</table>
</body></html>
"""

MOCK_FILING_CONTENT = "<html><body><h1>Annual Report</h1><p>Full filing text here...</p></body></html>"

MOCK_SMALL_FILING = "<html><body>tiny</body></html>"

BASE_FILING_URL = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/0001234567-26-000001-index.htm"
BASE_URL = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001"


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

        # Create filing-ready queue
        sqs = boto3.client("sqs", region_name=region)
        ready_queue = sqs.create_queue(QueueName="test-filing-ready")
        os.environ["FILING_READY_QUEUE_URL"] = ready_queue["QueueUrl"]

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
            "ready_queue_url": ready_queue["QueueUrl"],
            "table_name": table_name,
        }


def _make_sqs_message(
    event_id="evt-001",
    entity_id="ACME",
    filing_url=BASE_FILING_URL,
    form_type="10-K",
    filing_date="2026-02-14",
    cik="1234567",
):
    """Build a mock SQS record matching FilingDocumentRequested format."""
    body = {
        "event_type": "FILING_DOCUMENT_REQUESTED",
        "timestamp": "2026-02-14T12:00:00+00:00",
        "source": "SEC_EDGAR",
        "trace_id": "trace-001",
        "payload": {
            "event_id": event_id,
            "entity_id": entity_id,
            "filing_url": filing_url,
            "form_type": form_type,
            "filing_date": filing_date,
            "cik": cik,
        },
    }
    return {"body": json.dumps(body)}


def _seed_event_record(aws_env, event_id="evt-001", entity_id="ACME"):
    """Insert a minimal event record into DynamoDB for update tests."""
    from signalfft_common.dynamo.keys import build_events_pk, build_events_sk

    dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
    table = dynamodb.Table(aws_env["table_name"])
    table.put_item(
        Item={
            "PK": build_events_pk(entity_id),
            "SK": build_events_sk("2026-02-14T12:00:00+00:00", event_id),
            "event_id": event_id,
            "entity_id": entity_id,
            "source": "SEC_EDGAR",
            "event_type": "SEC_10K",
            "content_hash": "hash123",
            "raw_artifact_s3": "s3://bucket/raw/key.json",
            "created_at": "2026-02-14T12:00:00+00:00",
        }
    )


# ---------------------------------------------------------------------------
# TestFindPrimaryDocument
# ---------------------------------------------------------------------------

class TestFindPrimaryDocument:
    @responses.activate
    def test_find_primary_document_json_index(self, aws_env):
        """JSON index is tried first and primary .htm doc is extracted."""
        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON,
            status=200,
        )
        fetcher = FilingFetcher()
        result = fetcher._find_primary_document(BASE_FILING_URL)
        assert result == f"{BASE_URL}/primary-doc.htm"

    @responses.activate
    def test_find_primary_document_html_fallback(self, aws_env):
        """When JSON index fails, falls back to HTML index parsing."""
        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            status=404,
        )
        responses.add(
            responses.GET,
            BASE_FILING_URL,
            body=MOCK_INDEX_HTML,
            status=200,
        )
        fetcher = FilingFetcher()
        result = fetcher._find_primary_document(BASE_FILING_URL)
        assert result == f"{BASE_URL}/primary-doc.htm"

    @responses.activate
    def test_find_primary_document_both_fail(self, aws_env):
        """Returns None when both JSON and HTML index fail."""
        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            status=404,
        )
        responses.add(
            responses.GET,
            BASE_FILING_URL,
            body=MOCK_INDEX_HTML_NO_DOCS,
            status=200,
        )
        fetcher = FilingFetcher()
        result = fetcher._find_primary_document(BASE_FILING_URL)
        assert result is None

    @responses.activate
    def test_find_primary_document_json_no_htm_files(self, aws_env):
        """JSON index with no .htm files falls back to HTML."""
        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON_NO_HTM,
            status=200,
        )
        responses.add(
            responses.GET,
            BASE_FILING_URL,
            body=MOCK_INDEX_HTML,
            status=200,
        )
        fetcher = FilingFetcher()
        result = fetcher._find_primary_document(BASE_FILING_URL)
        assert result == f"{BASE_URL}/primary-doc.htm"

    @responses.activate
    def test_find_primary_document_skips_index_htm(self, aws_env):
        """Documents named *index* are skipped in JSON index."""
        index_json = {
            "directory": {
                "item": [
                    {"name": "filing-index.htm"},
                    {"name": "actual-filing.htm"},
                ]
            }
        }
        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=index_json,
            status=200,
        )
        fetcher = FilingFetcher()
        result = fetcher._find_primary_document(BASE_FILING_URL)
        assert result == f"{BASE_URL}/actual-filing.htm"


# ---------------------------------------------------------------------------
# TestIndexTableParser
# ---------------------------------------------------------------------------

class TestIndexTableParser:
    def test_parses_htm_links(self):
        """Extracts .htm links from table cells."""
        parser = _IndexTableParser()
        parser.feed(MOCK_INDEX_HTML)
        assert "primary-doc.htm" in parser.documents
        assert "exhibit1.htm" in parser.documents

    def test_skips_index_links(self):
        """Links containing 'index' are skipped."""
        html = '<table><tr><td><a href="filing-index.htm">index</a></td></tr></table>'
        parser = _IndexTableParser()
        parser.feed(html)
        assert len(parser.documents) == 0

    def test_no_links(self):
        """Table with no .htm links returns empty list."""
        html = '<table><tr><td><a href="data.xml">data</a></td></tr></table>'
        parser = _IndexTableParser()
        parser.feed(html)
        assert len(parser.documents) == 0

    def test_html_extension(self):
        """Also picks up .html extension."""
        html = '<table><tr><td><a href="document.html">doc</a></td></tr></table>'
        parser = _IndexTableParser()
        parser.feed(html)
        assert "document.html" in parser.documents


# ---------------------------------------------------------------------------
# TestFetchWithRateLimit
# ---------------------------------------------------------------------------

class TestFetchWithRateLimit:
    @responses.activate
    def test_successful_fetch(self, aws_env):
        """200 response is returned directly."""
        responses.add(responses.GET, "https://example.com/doc.htm", body="content", status=200)
        fetcher = FilingFetcher()
        resp = fetcher._fetch_with_rate_limit("https://example.com/doc.htm")
        assert resp.status_code == 200
        assert resp.text == "content"

    @responses.activate
    def test_429_retry(self, aws_env):
        """429 triggers retry and eventual success."""
        responses.add(responses.GET, "https://example.com/doc.htm", status=429)
        responses.add(responses.GET, "https://example.com/doc.htm", body="ok", status=200)
        fetcher = FilingFetcher()
        with patch("collectors.filing_fetch.collector.time.sleep"):
            resp = fetcher._fetch_with_rate_limit("https://example.com/doc.htm")
        assert resp.status_code == 200

    @responses.activate
    def test_503_retry(self, aws_env):
        """503 triggers retry and eventual success."""
        responses.add(responses.GET, "https://example.com/doc.htm", status=503)
        responses.add(responses.GET, "https://example.com/doc.htm", body="ok", status=200)
        fetcher = FilingFetcher()
        with patch("collectors.filing_fetch.collector.time.sleep"):
            resp = fetcher._fetch_with_rate_limit("https://example.com/doc.htm")
        assert resp.status_code == 200

    @responses.activate
    def test_404_no_retry(self, aws_env):
        """404 is returned immediately without retry."""
        responses.add(responses.GET, "https://example.com/doc.htm", status=404)
        fetcher = FilingFetcher()
        resp = fetcher._fetch_with_rate_limit("https://example.com/doc.htm")
        assert resp.status_code == 404

    @responses.activate
    def test_rate_limit_delay_enforced(self, aws_env):
        """100ms delay between requests is enforced."""
        responses.add(responses.GET, "https://example.com/a", body="a", status=200)
        responses.add(responses.GET, "https://example.com/b", body="b", status=200)
        fetcher = FilingFetcher()
        with patch("collectors.filing_fetch.collector.time.sleep") as mock_sleep, \
             patch("collectors.filing_fetch.collector.time.monotonic") as mock_monotonic:
            # First call: no delay needed (last_request_time is 0)
            mock_monotonic.return_value = 100.0
            fetcher._fetch_with_rate_limit("https://example.com/a")

            # Second call: simulate only 0.05s elapsed
            mock_monotonic.return_value = 100.05
            fetcher._last_request_time = 100.0
            fetcher._fetch_with_rate_limit("https://example.com/b")

            # Should sleep for ~0.05s to make up the 0.1s gap
            sleep_calls = [c for c in mock_sleep.call_args_list if c[0][0] < 0.2]
            assert len(sleep_calls) >= 1
            assert 0.04 < sleep_calls[-1][0][0] < 0.06

    @responses.activate
    def test_exhausted_retries_raises(self, aws_env):
        """After 4 attempts of 429, raise HTTPError."""
        for _ in range(4):
            responses.add(responses.GET, "https://example.com/doc.htm", status=429)
        fetcher = FilingFetcher()
        with patch("collectors.filing_fetch.collector.time.sleep"):
            with pytest.raises(Exception):
                fetcher._fetch_with_rate_limit("https://example.com/doc.htm")


# ---------------------------------------------------------------------------
# TestStoreFiling
# ---------------------------------------------------------------------------

class TestStoreFiling:
    def test_s3_key_structure(self, aws_env):
        """Filing stored at filings/{cik}/{form_type}/{date}/raw.html."""
        fetcher = FilingFetcher()
        uri = fetcher._store_filing("1234567", "10-K", "2026-02-14", MOCK_FILING_CONTENT)
        assert uri == f"s3://{aws_env['bucket']}/filings/1234567/10-K/2026-02-14/raw.html"

    def test_content_type_html(self, aws_env):
        """Content type is text/html."""
        fetcher = FilingFetcher()
        fetcher._store_filing("1234567", "10-K", "2026-02-14", MOCK_FILING_CONTENT)
        s3 = boto3.client("s3", region_name=aws_env["region"])
        obj = s3.get_object(
            Bucket=aws_env["bucket"],
            Key="filings/1234567/10-K/2026-02-14/raw.html",
        )
        assert obj["ContentType"] == "text/html"

    def test_s3_content_matches(self, aws_env):
        """Stored content matches input."""
        fetcher = FilingFetcher()
        fetcher._store_filing("1234567", "10-K", "2026-02-14", MOCK_FILING_CONTENT)
        s3 = boto3.client("s3", region_name=aws_env["region"])
        obj = s3.get_object(
            Bucket=aws_env["bucket"],
            Key="filings/1234567/10-K/2026-02-14/raw.html",
        )
        stored = obj["Body"].read().decode("utf-8")
        assert stored == MOCK_FILING_CONTENT

    def test_form_type_space_normalization(self, aws_env):
        """Form type with spaces normalizes to underscores in S3 path."""
        fetcher = FilingFetcher()
        uri = fetcher._store_filing("1234567", "DEF 14A", "2026-02-14", "content")
        assert "DEF_14A" in uri
        assert uri == f"s3://{aws_env['bucket']}/filings/1234567/DEF_14A/2026-02-14/raw.html"

    def test_s3_uri_format(self, aws_env):
        """Returns proper s3:// URI."""
        fetcher = FilingFetcher()
        uri = fetcher._store_filing("42", "8-K", "2026-01-01", "content")
        assert uri.startswith("s3://")
        assert "/filings/42/8-K/2026-01-01/raw.html" in uri


# ---------------------------------------------------------------------------
# TestStoreFilingWithRetry
# ---------------------------------------------------------------------------

class TestStoreFilingWithRetry:
    def test_retry_on_s3_failure(self, aws_env):
        """Retries on S3 failure and succeeds on second attempt."""
        fetcher = FilingFetcher()
        original = fetcher._store_filing
        call_count = {"n": 0}

        def failing_then_ok(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("S3 error")
            return original(*args, **kwargs)

        fetcher._store_filing = failing_then_ok
        uri = fetcher._store_filing_with_retry("1234567", "10-K", "2026-02-14", "content")
        assert uri.startswith("s3://")
        assert call_count["n"] == 2

    def test_three_failures_raises(self, aws_env):
        """Three consecutive failures raises the last error."""
        fetcher = FilingFetcher()
        fetcher._store_filing = lambda *a, **k: (_ for _ in ()).throw(Exception("S3 error"))
        with pytest.raises(Exception, match="S3 error"):
            fetcher._store_filing_with_retry("1234567", "10-K", "2026-02-14", "content")


# ---------------------------------------------------------------------------
# TestUpdateEventRecord
# ---------------------------------------------------------------------------

class TestUpdateEventRecord:
    def test_adds_filing_s3_uri(self, aws_env):
        """UpdateItem adds filing_s3_uri without overwriting existing fields."""
        _seed_event_record(aws_env)
        fetcher = FilingFetcher()
        fetcher._update_event_record("evt-001", "ACME", "s3://bucket/filings/1234567/10-K/2026-02-14/raw.html")

        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        item = scan["Items"][0]
        assert item["filing_s3_uri"] == "s3://bucket/filings/1234567/10-K/2026-02-14/raw.html"
        # Existing fields preserved
        assert item["event_id"] == "evt-001"
        assert item["source"] == "SEC_EDGAR"
        assert item["content_hash"] == "hash123"

    def test_event_not_found(self, aws_env):
        """No error raised when event record is not found."""
        fetcher = FilingFetcher()
        # Should log warning but not raise
        fetcher._update_event_record("evt-nonexistent", "UNKNOWN", "s3://bucket/key")


# ---------------------------------------------------------------------------
# TestMarkFetchFailed
# ---------------------------------------------------------------------------

class TestMarkFetchFailed:
    def test_sets_filing_fetch_status(self, aws_env):
        """Sets filing_fetch_status to FILING_FETCH_FAILED."""
        _seed_event_record(aws_env)
        fetcher = FilingFetcher()
        fetcher._mark_fetch_failed("evt-001", "ACME")

        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        item = scan["Items"][0]
        assert item["filing_fetch_status"] == "FILING_FETCH_FAILED"

    def test_mark_fetch_failed_event_not_found(self, aws_env):
        """No error raised when event record not found."""
        fetcher = FilingFetcher()
        fetcher._mark_fetch_failed("evt-missing", "UNKNOWN")


# ---------------------------------------------------------------------------
# TestEmitFilingReady
# ---------------------------------------------------------------------------

class TestEmitFilingReady:
    def test_sends_sqs_message(self, aws_env):
        """Publishes FilingDocumentReady message to filing-ready queue."""
        fetcher = FilingFetcher()
        fetcher._emit_filing_ready(
            event_id="evt-001",
            entity_id="ACME",
            filing_s3_uri="s3://bucket/filings/1234567/10-K/2026-02-14/raw.html",
            form_type="10-K",
            filing_date="2026-02-14",
            cik="1234567",
        )

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        msgs = sqs.receive_message(
            QueueUrl=aws_env["ready_queue_url"],
            MaxNumberOfMessages=1,
        )
        assert len(msgs.get("Messages", [])) == 1

    def test_message_deserializable(self, aws_env):
        """SQS message can be deserialized via BaseEvent.from_sqs_message."""
        from signalfft_common.events import BaseEvent

        fetcher = FilingFetcher()
        fetcher._emit_filing_ready(
            event_id="evt-001",
            entity_id="ACME",
            filing_s3_uri="s3://bucket/filings/1234567/10-K/2026-02-14/raw.html",
            form_type="10-K",
            filing_date="2026-02-14",
            cik="1234567",
        )

        sqs = boto3.client("sqs", region_name=aws_env["region"])
        msgs = sqs.receive_message(
            QueueUrl=aws_env["ready_queue_url"],
            MaxNumberOfMessages=1,
        )
        body = msgs["Messages"][0]["Body"]
        event = BaseEvent.from_sqs_message(body)
        assert event.event_type == "FILING_DOCUMENT_READY"
        assert event.payload["event_id"] == "evt-001"
        assert event.payload["filing_s3_uri"] == "s3://bucket/filings/1234567/10-K/2026-02-14/raw.html"
        assert event.payload["form_type"] == "10-K"
        assert event.payload["cik"] == "1234567"

    def test_skips_when_no_queue_url(self, aws_env):
        """No error when FILING_READY_QUEUE_URL is not set."""
        old = os.environ.pop("FILING_READY_QUEUE_URL", None)
        try:
            fetcher = FilingFetcher()
            # Should not raise
            fetcher._emit_filing_ready("evt-001", "ACME", "s3://bucket/key", "10-K", "2026-02-14", "1234567")
        finally:
            if old:
                os.environ["FILING_READY_QUEUE_URL"] = old


# ---------------------------------------------------------------------------
# TestProcessMessage
# ---------------------------------------------------------------------------

class TestProcessMessage:
    @responses.activate
    def test_end_to_end_success(self, aws_env):
        """Full process_message flow: find doc -> download -> store -> update DynamoDB -> emit."""
        _seed_event_record(aws_env)

        # JSON index
        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON,
            status=200,
        )
        # Primary document
        responses.add(
            responses.GET,
            f"{BASE_URL}/primary-doc.htm",
            body=MOCK_FILING_CONTENT,
            status=200,
        )

        fetcher = FilingFetcher()
        with patch("collectors.filing_fetch.collector.time.sleep"):
            result = fetcher.process_message(_make_sqs_message())

        assert result["status"] == "success"
        assert result["event_id"] == "evt-001"

        # Verify S3 object exists
        s3 = boto3.client("s3", region_name=aws_env["region"])
        obj = s3.get_object(
            Bucket=aws_env["bucket"],
            Key="filings/1234567/10-K/2026-02-14/raw.html",
        )
        assert obj["Body"].read().decode("utf-8") == MOCK_FILING_CONTENT

        # Verify DynamoDB updated
        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        item = scan["Items"][0]
        assert "filing_s3_uri" in item

        # Verify SQS message emitted
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        msgs = sqs.receive_message(
            QueueUrl=aws_env["ready_queue_url"],
            MaxNumberOfMessages=1,
        )
        assert len(msgs.get("Messages", [])) == 1

    @responses.activate
    def test_404_marks_failed(self, aws_env):
        """404 on document download marks fetch as failed."""
        _seed_event_record(aws_env)

        # JSON index returns a document URL
        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON,
            status=200,
        )
        # But document returns 404
        responses.add(
            responses.GET,
            f"{BASE_URL}/primary-doc.htm",
            status=404,
        )

        fetcher = FilingFetcher()
        with patch("collectors.filing_fetch.collector.time.sleep"):
            result = fetcher.process_message(_make_sqs_message())

        assert result["status"] == "not_found"

        # Verify DynamoDB has failure status
        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        item = scan["Items"][0]
        assert item["filing_fetch_status"] == "FILING_FETCH_FAILED"

    @responses.activate
    def test_no_primary_document_marks_failed(self, aws_env):
        """When no primary document is found, marks as failed."""
        _seed_event_record(aws_env)

        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            status=404,
        )
        responses.add(
            responses.GET,
            BASE_FILING_URL,
            body=MOCK_INDEX_HTML_NO_DOCS,
            status=200,
        )

        fetcher = FilingFetcher()
        with patch("collectors.filing_fetch.collector.time.sleep"):
            result = fetcher.process_message(_make_sqs_message())

        assert result["status"] == "no_document"

    @responses.activate
    def test_process_message_with_def_14a(self, aws_env):
        """Form type with spaces normalizes correctly in S3 path."""
        _seed_event_record(aws_env, event_id="evt-def14a", entity_id="ACME")

        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/primary-doc.htm",
            body=MOCK_FILING_CONTENT,
            status=200,
        )

        fetcher = FilingFetcher()
        msg = _make_sqs_message(event_id="evt-def14a", form_type="DEF 14A")
        with patch("collectors.filing_fetch.collector.time.sleep"):
            result = fetcher.process_message(msg)

        assert result["status"] == "success"

        # Verify S3 path uses underscores
        s3 = boto3.client("s3", region_name=aws_env["region"])
        objects = s3.list_objects_v2(Bucket=aws_env["bucket"], Prefix="filings/1234567/DEF_14A/")
        assert objects["KeyCount"] == 1


# ---------------------------------------------------------------------------
# TestSmallFilingWarning
# ---------------------------------------------------------------------------

class TestSmallFilingWarning:
    @responses.activate
    def test_small_filing_stored_with_warning(self, aws_env, caplog):
        """Filing < 1KB logs warning but stores anyway."""
        _seed_event_record(aws_env)

        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/primary-doc.htm",
            body=MOCK_SMALL_FILING,
            status=200,
        )

        fetcher = FilingFetcher()
        import logging
        with caplog.at_level(logging.WARNING), \
             patch("collectors.filing_fetch.collector.time.sleep"):
            result = fetcher.process_message(_make_sqs_message())

        assert result["status"] == "success"
        assert any("Small filing" in msg for msg in caplog.messages)

        # Verify it was still stored
        s3 = boto3.client("s3", region_name=aws_env["region"])
        obj = s3.get_object(
            Bucket=aws_env["bucket"],
            Key="filings/1234567/10-K/2026-02-14/raw.html",
        )
        assert obj["Body"].read().decode("utf-8") == MOCK_SMALL_FILING


# ---------------------------------------------------------------------------
# TestLambdaHandler
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    @responses.activate
    def test_lambda_handler_sqs_batch(self, aws_env):
        """Lambda handler processes SQS batch and returns 200."""
        _seed_event_record(aws_env)

        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/primary-doc.htm",
            body=MOCK_FILING_CONTENT,
            status=200,
        )

        sqs_event = {"Records": [_make_sqs_message()]}
        with patch("collectors.filing_fetch.collector.time.sleep"):
            response = lambda_handler(sqs_event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["processed"] == 1
        assert body["success"] == 1
        assert body["errors"] == 0

    @responses.activate
    def test_lambda_handler_empty_batch(self, aws_env):
        """Lambda handler handles empty batch."""
        response = lambda_handler({"Records": []}, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["processed"] == 0

    @responses.activate
    def test_lambda_handler_error_in_message(self, aws_env):
        """Lambda handler counts errors without crashing."""
        # Invalid message body triggers a JSON parse or key error
        sqs_event = {"Records": [{"body": "not-valid-json"}]}
        response = lambda_handler(sqs_event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["errors"] >= 1

    @responses.activate
    def test_lambda_handler_multiple_records(self, aws_env):
        """Lambda handler processes multiple records."""
        _seed_event_record(aws_env, event_id="evt-001", entity_id="ACME")
        _seed_event_record(aws_env, event_id="evt-002", entity_id="WDGT")

        responses.add(
            responses.GET,
            f"{BASE_URL}/index.json",
            json=MOCK_INDEX_JSON,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/primary-doc.htm",
            body=MOCK_FILING_CONTENT,
            status=200,
        )

        sqs_event = {
            "Records": [
                _make_sqs_message(event_id="evt-001", entity_id="ACME"),
                _make_sqs_message(event_id="evt-002", entity_id="WDGT"),
            ]
        }
        with patch("collectors.filing_fetch.collector.time.sleep"):
            response = lambda_handler(sqs_event, None)

        body = json.loads(response["body"])
        assert body["processed"] == 2
