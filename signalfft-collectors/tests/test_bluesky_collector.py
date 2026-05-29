"""Tests for the Bluesky financial social collector."""

from __future__ import annotations

import json
import os
import sys

import boto3
import pytest
import responses
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collectors.bluesky.collector import (
    BlueskyCollector,
    BLUESKY_SEARCH_URL,
    BLUESKY_CREATE_SESSION_URL,
    DEFAULT_SEARCH_TERMS,
    lambda_handler,
)


# ---------------------------------------------------------------------------
# Mock Bluesky API responses
# ---------------------------------------------------------------------------

MOCK_SESSION_RESPONSE = {
    "did": "did:plc:testuser123",
    "handle": "testuser.bsky.social",
    "accessJwt": "eyJ0eXAiOiJhdCtqd3QiLCJhbGciOiJFUzI1NksifQ.test.signature",
    "refreshJwt": "eyJ0eXAiOiJyZWZyZXNoK2p3dCIsImFsZyI6IkVTMjU2SyJ9.test.signature",
}

MOCK_POST_WITH_CASHTAG = {
    "uri": "at://did:plc:abc123/app.bsky.feed.post/rec1",
    "cid": "bafyabc1",
    "author": {
        "did": "did:plc:abc123",
        "handle": "trader-joe.bsky.social",
        "displayName": "Trader Joe",
    },
    "record": {
        "text": "$AAPL looking strong after earnings beat",
        "createdAt": "2025-02-20T14:30:00.000Z",
    },
    "likeCount": 12,
    "repostCount": 3,
    "replyCount": 2,
    "indexedAt": "2025-02-20T14:30:05.000Z",
}

MOCK_POST_MULTI_CASHTAGS = {
    "uri": "at://did:plc:def456/app.bsky.feed.post/rec2",
    "cid": "bafydef2",
    "author": {
        "did": "did:plc:def456",
        "handle": "cloud-watcher.bsky.social",
        "displayName": "Cloud Watcher",
    },
    "record": {
        "text": "Comparing $MSFT and $GOOGL cloud revenue growth this quarter",
        "createdAt": "2025-02-20T14:35:00.000Z",
    },
    "likeCount": 5,
    "repostCount": 1,
    "replyCount": 0,
    "indexedAt": "2025-02-20T14:35:05.000Z",
}

MOCK_POST_NO_CASHTAGS = {
    "uri": "at://did:plc:ghi789/app.bsky.feed.post/rec3",
    "cid": "bafyghi3",
    "author": {
        "did": "did:plc:ghi789",
        "handle": "cautious-carl.bsky.social",
        "displayName": "Cautious Carl",
    },
    "record": {
        "text": "Market looking choppy today, staying cash heavy",
        "createdAt": "2025-02-20T14:40:00.000Z",
    },
    "likeCount": 2,
    "repostCount": 0,
    "replyCount": 1,
    "indexedAt": "2025-02-20T14:40:05.000Z",
}

MOCK_POST_MISSING_DATA = {
    "uri": "at://did:plc:jkl012/app.bsky.feed.post/rec4",
    "cid": "bafyjkl4",
    "author": {},
    "record": {
        "text": "Quick thought on the market",
        "createdAt": "2025-02-20T15:00:00.000Z",
    },
}

MOCK_SEARCH_RESPONSE = {
    "posts": [
        MOCK_POST_WITH_CASHTAG,
        MOCK_POST_MULTI_CASHTAGS,
        MOCK_POST_NO_CASHTAGS,
    ],
    "cursor": "next-page-cursor",
    "hitsTotal": 100,
}


def _register_auth():
    """Register mock for Bluesky createSession endpoint."""
    responses.add(
        responses.POST,
        BLUESKY_CREATE_SESSION_URL,
        json=MOCK_SESSION_RESPONSE,
        status=200,
    )


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
        os.environ["BLUESKY_HANDLE"] = "testuser.bsky.social"
        os.environ["BLUESKY_APP_PASSWORD"] = "test-app-password-1234"
        os.environ["BLUESKY_SEARCH_TERMS"] = "stocks"

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

        os.environ.pop("BLUESKY_HANDLE", None)
        os.environ.pop("BLUESKY_APP_PASSWORD", None)
        os.environ.pop("BLUESKY_SEARCH_TERMS", None)


# ---------------------------------------------------------------------------
# Tests: source_name and event_type
# ---------------------------------------------------------------------------

class TestBlueskySourceName:
    def test_source_name(self, aws_env):
        collector = BlueskyCollector()
        assert collector.source_name == "BLUESKY"


class TestBlueskyEventType:
    def test_event_type(self, aws_env):
        collector = BlueskyCollector()
        assert collector.extract_event_type({}) == "SOCIAL_POST"


# ---------------------------------------------------------------------------
# Tests: entity ID extraction
# ---------------------------------------------------------------------------

class TestExtractEntityId:
    def test_with_single_symbol(self, aws_env):
        """Post with symbols=['AAPL'] -> entity_id 'AAPL'."""
        collector = BlueskyCollector()
        doc = {"symbols": ["AAPL"]}
        assert collector.extract_entity_id(doc) == "AAPL"

    def test_with_multiple_symbols(self, aws_env):
        """Post with symbols=['MSFT', 'GOOGL'] -> uses first ticker 'MSFT'."""
        collector = BlueskyCollector()
        doc = {"symbols": ["MSFT", "GOOGL"]}
        assert collector.extract_entity_id(doc) == "MSFT"

    def test_no_symbols(self, aws_env):
        """Post with empty symbols -> 'SOCIAL_GENERAL'."""
        collector = BlueskyCollector()
        doc = {"symbols": []}
        assert collector.extract_entity_id(doc) == "SOCIAL_GENERAL"

    def test_missing_symbols_field(self, aws_env):
        """Post without 'symbols' key -> 'SOCIAL_GENERAL'."""
        collector = BlueskyCollector()
        assert collector.extract_entity_id({"body": "some text"}) == "SOCIAL_GENERAL"


# ---------------------------------------------------------------------------
# Tests: authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    @responses.activate
    def test_auth_sends_credentials(self, aws_env):
        """Verify createSession is called with handle and password."""
        _register_auth()
        responses.add(responses.GET, BLUESKY_SEARCH_URL, json={"posts": []}, status=200)
        collector = BlueskyCollector()
        collector.collect()

        auth_call = responses.calls[0]
        body = json.loads(auth_call.request.body)
        assert body["identifier"] == "testuser.bsky.social"
        assert body["password"] == "test-app-password-1234"

    @responses.activate
    def test_auth_sets_bearer_token(self, aws_env):
        """Verify Bearer token is used for search request."""
        _register_auth()
        responses.add(responses.GET, BLUESKY_SEARCH_URL, json={"posts": []}, status=200)
        collector = BlueskyCollector()
        collector.collect()

        search_call = responses.calls[1]
        assert "Bearer eyJ0eXAiOiJhdCtqd3QiLCJhbGciOiJFUzI1NksifQ.test.signature" in search_call.request.headers.get("Authorization", "")

    @responses.activate
    def test_auth_failure_returns_empty(self, aws_env):
        """Auth 401 -> empty list, no crash."""
        responses.add(responses.POST, BLUESKY_CREATE_SESSION_URL, json={"error": "AuthenticationRequired"}, status=401)
        collector = BlueskyCollector()
        items = collector.collect()
        assert items == []

    def test_missing_credentials_returns_empty(self, aws_env):
        """No credentials set -> empty list, log error."""
        os.environ.pop("BLUESKY_HANDLE", None)
        os.environ.pop("BLUESKY_APP_PASSWORD", None)
        os.environ.pop("BLUESKY_SEARCH_TERMS", None)
        collector = BlueskyCollector()
        items = collector.collect()
        assert items == []


# ---------------------------------------------------------------------------
# Tests: collect
# ---------------------------------------------------------------------------

class TestCollect:
    @responses.activate
    def test_collect_parses_response(self, aws_env):
        """Mock Bluesky API, verify posts are normalized."""
        _register_auth()
        responses.add(
            responses.GET,
            BLUESKY_SEARCH_URL,
            json=MOCK_SEARCH_RESPONSE,
            status=200,
        )
        collector = BlueskyCollector()
        items = collector.collect()

        assert len(items) == 3
        # First post: has cashtag
        assert items[0]["bluesky_uri"] == "at://did:plc:abc123/app.bsky.feed.post/rec1"
        assert items[0]["body"] == "$AAPL looking strong after earnings beat"
        assert items[0]["symbols"] == ["AAPL"]
        assert items[0]["author_handle"] == "trader-joe.bsky.social"
        assert items[0]["author_display_name"] == "Trader Joe"
        assert items[0]["like_count"] == 12

        # Second post: multiple cashtags
        assert items[1]["symbols"] == ["MSFT", "GOOGL"]
        assert items[1]["author_handle"] == "cloud-watcher.bsky.social"

        # Third post: no cashtags
        assert items[2]["symbols"] == []

    @responses.activate
    def test_collect_sends_search_params(self, aws_env):
        """Verify search query and sort params are sent."""
        _register_auth()
        responses.add(responses.GET, BLUESKY_SEARCH_URL, json={"posts": []}, status=200)
        collector = BlueskyCollector()
        collector.collect()

        # calls[0] is auth, calls[1] is search
        request_url = responses.calls[1].request.url
        assert "sort=latest" in request_url
        assert "limit=" in request_url

    @responses.activate
    def test_collect_handles_missing_data(self, aws_env):
        """Posts with missing fields should still be collected."""
        _register_auth()
        responses.add(
            responses.GET,
            BLUESKY_SEARCH_URL,
            json={"posts": [MOCK_POST_MISSING_DATA]},
            status=200,
        )
        collector = BlueskyCollector()
        items = collector.collect()

        assert len(items) == 1
        assert items[0]["symbols"] == []
        assert items[0]["author_handle"] == ""
        assert items[0]["author_display_name"] == ""
        assert items[0]["like_count"] == 0

    @responses.activate
    def test_api_error_returns_empty(self, aws_env):
        """HTTP 500 -> empty list, no crash."""
        _register_auth()
        responses.add(responses.GET, BLUESKY_SEARCH_URL, json={}, status=500)
        collector = BlueskyCollector()
        items = collector.collect()
        assert items == []

    @responses.activate
    def test_rate_limit_returns_empty(self, aws_env):
        """HTTP 429 -> empty list, no crash."""
        _register_auth()
        responses.add(responses.GET, BLUESKY_SEARCH_URL, json={}, status=429)
        collector = BlueskyCollector()
        items = collector.collect()
        assert items == []

    @responses.activate
    def test_non_list_posts_returns_empty(self, aws_env):
        """Non-list posts field -> empty list."""
        _register_auth()
        responses.add(
            responses.GET,
            BLUESKY_SEARCH_URL,
            json={"posts": "not a list"},
            status=200,
        )
        collector = BlueskyCollector()
        items = collector.collect()
        assert items == []

    @responses.activate
    def test_missing_posts_key(self, aws_env):
        """Response without 'posts' key -> empty list."""
        _register_auth()
        responses.add(
            responses.GET,
            BLUESKY_SEARCH_URL,
            json={"cursor": "abc"},
            status=200,
        )
        collector = BlueskyCollector()
        items = collector.collect()
        assert items == []


# ---------------------------------------------------------------------------
# Tests: dedup via BaseCollector.run()
# ---------------------------------------------------------------------------

class TestDedup:
    @responses.activate
    def test_dedup_across_runs(self, aws_env):
        """Same post in two runs -> second run detects duplicate."""
        _register_auth()
        responses.add(
            responses.GET, BLUESKY_SEARCH_URL,
            json={"posts": [MOCK_POST_WITH_CASHTAG]}, status=200,
        )
        collector1 = BlueskyCollector()
        result1 = collector1.run()
        assert result1["stored"] == 1
        assert result1["duplicates"] == 0

        # Second run with same post
        _register_auth()
        responses.add(
            responses.GET, BLUESKY_SEARCH_URL,
            json={"posts": [MOCK_POST_WITH_CASHTAG]}, status=200,
        )
        collector2 = BlueskyCollector()
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
        _register_auth()
        responses.add(
            responses.GET, BLUESKY_SEARCH_URL,
            json=MOCK_SEARCH_RESPONSE,
            status=200,
        )
        collector = BlueskyCollector()
        result = collector.run()

        assert result["collected"] == 3
        assert result["stored"] == 3
        assert result["errors"] == 0

        # S3 artifacts
        s3 = boto3.client("s3", region_name=aws_env["region"])
        objects = s3.list_objects_v2(
            Bucket=aws_env["bucket"], Prefix="raw/BLUESKY/"
        )
        assert objects["KeyCount"] == 3

        # DynamoDB records
        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        items = scan["Items"]
        assert len(items) == 3
        sources = {item["source"] for item in items}
        assert sources == {"BLUESKY"}
        event_types = {item["event_type"] for item in items}
        assert event_types == {"SOCIAL_POST"}

        # SQS messages
        sqs = boto3.client("sqs", region_name=aws_env["region"])
        msgs = sqs.receive_message(
            QueueUrl=aws_env["queue_url"], MaxNumberOfMessages=10,
        )
        assert len(msgs.get("Messages", [])) == 3


# ---------------------------------------------------------------------------
# Tests: Lambda handler
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    @responses.activate
    def test_lambda_handler_returns_200(self, aws_env):
        _register_auth()
        responses.add(
            responses.GET, BLUESKY_SEARCH_URL,
            json={"posts": [MOCK_POST_WITH_CASHTAG]}, status=200,
        )
        result = lambda_handler({}, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["collected"] == 1
        assert body["stored"] == 1

    @responses.activate
    def test_lambda_handler_empty_response(self, aws_env):
        _register_auth()
        responses.add(
            responses.GET, BLUESKY_SEARCH_URL,
            json={"posts": []}, status=200,
        )
        result = lambda_handler({}, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["collected"] == 0
