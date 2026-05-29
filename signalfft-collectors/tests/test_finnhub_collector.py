"""Tests for the Finnhub financial news collector."""

from __future__ import annotations

import json
import os
import sys

import boto3
import pytest
import responses
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collectors.finnhub_news.collector import (
    FinnhubNewsCollector,
    FINNHUB_NEWS_URL,
    lambda_handler,
)


# ---------------------------------------------------------------------------
# Mock Finnhub API responses
# ---------------------------------------------------------------------------

MOCK_NEWS_ITEM_WITH_TICKER = {
    "category": "technology",
    "datetime": 1740000000,
    "headline": "Apple reports record quarterly revenue driven by iPhone sales",
    "id": 123456,
    "image": "https://example.com/image.jpg",
    "related": "AAPL",
    "source": "Reuters",
    "summary": "Apple Inc. reported quarterly revenue of $120B, beating expectations.",
    "url": "https://example.com/article/123456",
}

MOCK_NEWS_ITEM_MULTI_TICKER = {
    "category": "merger",
    "datetime": 1740000100,
    "headline": "Microsoft and Activision merger approved by regulators",
    "id": 789012,
    "image": "",
    "related": "MSFT,ATVI",
    "source": "Bloomberg",
    "summary": "The $69B acquisition clears its final regulatory hurdle.",
    "url": "https://example.com/article/789012",
}

MOCK_NEWS_ITEM_NO_TICKER = {
    "category": "general",
    "datetime": 1740000200,
    "headline": "Federal Reserve signals potential rate cut in Q2",
    "id": 345678,
    "image": "",
    "related": "",
    "source": "CNBC",
    "summary": "Fed Chair indicated that economic conditions may warrant lower rates.",
    "url": "https://example.com/article/345678",
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
        os.environ["FINNHUB_API_KEY"] = "test-api-key-12345"

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

        os.environ.pop("FINNHUB_API_KEY", None)


# ---------------------------------------------------------------------------
# Tests: source_name and event_type
# ---------------------------------------------------------------------------

class TestFinnhubSourceName:
    def test_source_name(self, aws_env):
        collector = FinnhubNewsCollector()
        assert collector.source_name == "FINNHUB_NEWS"


class TestFinnhubEventType:
    def test_event_type(self, aws_env):
        collector = FinnhubNewsCollector()
        assert collector.extract_event_type({}) == "NEWS_ARTICLE"


# ---------------------------------------------------------------------------
# Tests: entity ID extraction
# ---------------------------------------------------------------------------

class TestExtractEntityId:
    def test_with_single_ticker(self, aws_env):
        """Item with related='AAPL' -> entity_id 'AAPL'."""
        collector = FinnhubNewsCollector()
        assert collector.extract_entity_id(MOCK_NEWS_ITEM_WITH_TICKER) == "AAPL"

    def test_with_multiple_tickers(self, aws_env):
        """Item with related='MSFT,ATVI' -> entity_id uses first ticker 'MSFT'."""
        collector = FinnhubNewsCollector()
        assert collector.extract_entity_id(MOCK_NEWS_ITEM_MULTI_TICKER) == "MSFT"

    def test_no_ticker(self, aws_env):
        """Item with empty related field -> 'MARKET_GENERAL'."""
        collector = FinnhubNewsCollector()
        assert collector.extract_entity_id(MOCK_NEWS_ITEM_NO_TICKER) == "MARKET_GENERAL"

    def test_missing_related_field(self, aws_env):
        """Item without 'related' key at all -> 'MARKET_GENERAL'."""
        collector = FinnhubNewsCollector()
        assert collector.extract_entity_id({"headline": "some news"}) == "MARKET_GENERAL"


# ---------------------------------------------------------------------------
# Tests: collect
# ---------------------------------------------------------------------------

class TestCollect:
    @responses.activate
    def test_collect_parses_response(self, aws_env):
        """Mock Finnhub API, verify news items are returned."""
        responses.add(
            responses.GET,
            FINNHUB_NEWS_URL,
            json=[MOCK_NEWS_ITEM_WITH_TICKER, MOCK_NEWS_ITEM_NO_TICKER],
            status=200,
        )
        collector = FinnhubNewsCollector()
        items = collector.collect()

        assert len(items) == 2
        assert items[0]["headline"] == MOCK_NEWS_ITEM_WITH_TICKER["headline"]
        assert items[1]["headline"] == MOCK_NEWS_ITEM_NO_TICKER["headline"]

    @responses.activate
    def test_collect_sends_api_key(self, aws_env):
        """Verify the API key is passed as a query parameter."""
        responses.add(responses.GET, FINNHUB_NEWS_URL, json=[], status=200)
        collector = FinnhubNewsCollector()
        collector.collect()

        assert len(responses.calls) == 1
        assert "token=test-api-key-12345" in responses.calls[0].request.url

    @responses.activate
    def test_api_error_returns_empty(self, aws_env):
        """HTTP 500 -> empty list, no crash."""
        responses.add(responses.GET, FINNHUB_NEWS_URL, json={}, status=500)
        collector = FinnhubNewsCollector()
        items = collector.collect()
        assert items == []

    @responses.activate
    def test_rate_limit_returns_empty(self, aws_env):
        """HTTP 429 -> empty list, no crash."""
        responses.add(responses.GET, FINNHUB_NEWS_URL, json={}, status=429)
        collector = FinnhubNewsCollector()
        items = collector.collect()
        assert items == []

    def test_missing_api_key(self, aws_env):
        """No FINNHUB_API_KEY set -> empty list, log error."""
        os.environ.pop("FINNHUB_API_KEY", None)
        collector = FinnhubNewsCollector()
        items = collector.collect()
        assert items == []


# ---------------------------------------------------------------------------
# Tests: dedup via BaseCollector.run()
# ---------------------------------------------------------------------------

class TestDedup:
    @responses.activate
    def test_dedup_across_runs(self, aws_env):
        """Same news item in two runs -> second run detects duplicate."""
        responses.add(
            responses.GET, FINNHUB_NEWS_URL,
            json=[MOCK_NEWS_ITEM_WITH_TICKER], status=200,
        )
        collector1 = FinnhubNewsCollector()
        result1 = collector1.run()
        assert result1["stored"] == 1
        assert result1["duplicates"] == 0

        # Second run with same item
        responses.add(
            responses.GET, FINNHUB_NEWS_URL,
            json=[MOCK_NEWS_ITEM_WITH_TICKER], status=200,
        )
        collector2 = FinnhubNewsCollector()
        result2 = collector2.run()
        assert result2["collected"] == 1
        assert result2["stored"] == 0
        assert result2["duplicates"] == 1


# ---------------------------------------------------------------------------
# Tests: full pipeline
# ---------------------------------------------------------------------------

class TestFullPipeline:
    @responses.activate
    def test_run_stores_to_s3_dynamo_sqs(self, aws_env):
        """End-to-end: collect -> dedup -> S3 -> DynamoDB -> SQS."""
        responses.add(
            responses.GET, FINNHUB_NEWS_URL,
            json=[MOCK_NEWS_ITEM_WITH_TICKER, MOCK_NEWS_ITEM_NO_TICKER],
            status=200,
        )
        collector = FinnhubNewsCollector()
        result = collector.run()

        assert result["collected"] == 2
        assert result["stored"] == 2
        assert result["errors"] == 0

        # S3 artifacts
        s3 = boto3.client("s3", region_name=aws_env["region"])
        objects = s3.list_objects_v2(
            Bucket=aws_env["bucket"], Prefix="raw/FINNHUB_NEWS/"
        )
        assert objects["KeyCount"] == 2

        # DynamoDB records
        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        items = scan["Items"]
        assert len(items) == 2
        sources = {item["source"] for item in items}
        assert sources == {"FINNHUB_NEWS"}
        event_types = {item["event_type"] for item in items}
        assert event_types == {"NEWS_ARTICLE"}

        # SQS messages
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        msgs = sqs.receive_message(
            QueueUrl=aws_env["queue_url"], MaxNumberOfMessages=10,
        )
        assert len(msgs.get("Messages", [])) == 2


# ---------------------------------------------------------------------------
# Tests: Lambda handler
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    @responses.activate
    def test_lambda_handler_returns_200(self, aws_env):
        responses.add(
            responses.GET, FINNHUB_NEWS_URL,
            json=[MOCK_NEWS_ITEM_WITH_TICKER], status=200,
        )
        result = lambda_handler({}, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["collected"] == 1
        assert body["stored"] == 1

    @responses.activate
    def test_lambda_handler_empty_response(self, aws_env):
        responses.add(responses.GET, FINNHUB_NEWS_URL, json=[], status=200)
        result = lambda_handler({}, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["collected"] == 0
