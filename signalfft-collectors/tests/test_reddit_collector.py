"""Tests for the Reddit financial subreddit collector."""

from __future__ import annotations

import json
import os
import sys
import time

import boto3
import pytest
import responses
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collectors.reddit.collector import (
    RedditCollector,
    REDDIT_OAUTH_BASE,
    REDDIT_TOKEN_URL,
    lambda_handler,
)


# ---------------------------------------------------------------------------
# Mock Reddit API responses
# ---------------------------------------------------------------------------

def _make_reddit_post(
    post_id: str = "abc123",
    title: str = "Great stock pick",
    selftext: str = "I think this is a good buy.",
    subreddit: str = "stocks",
    author: str = "testuser",
    score: int = 50,
    num_comments: int = 10,
    created_utc: float | None = None,
) -> dict:
    """Build a mock Reddit listing child dict."""
    if created_utc is None:
        created_utc = time.time() - 300  # 5 minutes ago
    return {
        "kind": "t3",
        "data": {
            "id": post_id,
            "title": title,
            "selftext": selftext,
            "subreddit": subreddit,
            "author": author,
            "score": score,
            "num_comments": num_comments,
            "permalink": f"/r/{subreddit}/comments/{post_id}/great_stock_pick/",
            "created_utc": created_utc,
        },
    }


def _wrap_listing(*posts) -> dict:
    """Wrap posts in Reddit's listing format."""
    return {
        "kind": "Listing",
        "data": {
            "children": list(posts),
            "after": None,
            "before": None,
        },
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
        # Override to a single subreddit for faster tests
        os.environ["REDDIT_SUBREDDITS"] = "stocks"
        os.environ["REDDIT_MIN_SCORE"] = "5"
        os.environ["REDDIT_MAX_AGE_HOURS"] = "2"
        os.environ["REDDIT_CLIENT_ID"] = "test-client-id"
        os.environ["REDDIT_CLIENT_SECRET"] = "test-client-secret"

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

        os.environ.pop("REDDIT_SUBREDDITS", None)
        os.environ.pop("REDDIT_MIN_SCORE", None)
        os.environ.pop("REDDIT_MAX_AGE_HOURS", None)
        os.environ.pop("REDDIT_CLIENT_ID", None)
        os.environ.pop("REDDIT_CLIENT_SECRET", None)


def _mock_oauth():
    """Register a mock Reddit OAuth token response."""
    responses.add(
        responses.POST,
        REDDIT_TOKEN_URL,
        json={"access_token": "test-token-abc123", "token_type": "bearer", "expires_in": 86400},
        status=200,
    )


# ---------------------------------------------------------------------------
# Tests: source_name and event_type
# ---------------------------------------------------------------------------

class TestRedditSourceName:
    def test_source_name(self, aws_env):
        collector = RedditCollector()
        assert collector.source_name == "REDDIT"


class TestRedditEventType:
    def test_event_type(self, aws_env):
        collector = RedditCollector()
        assert collector.extract_event_type({}) == "SOCIAL_POST"


# ---------------------------------------------------------------------------
# Tests: normalize post
# ---------------------------------------------------------------------------

class TestNormalizePost:
    @responses.activate
    def test_collect_normalizes_post(self, aws_env):
        """Collected post has expected fields."""
        _mock_oauth()
        post = _make_reddit_post(
            post_id="xyz789",
            title="TSLA is looking bullish",
            score=100,
        )
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(post),
            status=200,
        )
        collector = RedditCollector()
        items = collector.collect()

        assert len(items) == 1
        doc = items[0]
        assert doc["reddit_id"] == "xyz789"
        assert doc["title"] == "TSLA is looking bullish"
        assert doc["subreddit"] == "stocks"
        assert doc["score"] == 100
        assert "published_at" in doc
        assert "url" in doc


# ---------------------------------------------------------------------------
# Tests: ticker extraction
# ---------------------------------------------------------------------------

class TestTickerExtraction:
    def test_dollar_sign_ticker(self, aws_env):
        """Title with '$AAPL' -> entity_id 'AAPL'."""
        collector = RedditCollector()
        doc = {"title": "Just bought $AAPL, feeling good", "selftext": ""}
        assert collector.extract_entity_id(doc) == "AAPL"

    def test_dollar_sign_in_selftext(self, aws_env):
        """$TSLA in selftext -> entity_id 'TSLA'."""
        collector = RedditCollector()
        doc = {"title": "My portfolio update", "selftext": "Loaded up on $TSLA"}
        assert collector.extract_entity_id(doc) == "TSLA"

    def test_bare_ticker_in_title(self, aws_env):
        """All-caps known ticker in title -> detected."""
        collector = RedditCollector()
        doc = {"title": "NVDA earnings beat expectations!", "selftext": ""}
        assert collector.extract_entity_id(doc) == "NVDA"

    def test_no_ticker_found(self, aws_env):
        """No recognizable ticker -> 'SOCIAL_GENERAL'."""
        collector = RedditCollector()
        doc = {"title": "Best strategies for 2026", "selftext": "What are your top picks?"}
        assert collector.extract_entity_id(doc) == "SOCIAL_GENERAL"

    def test_unknown_dollar_ticker_ignored(self, aws_env):
        """$XYZZY not in top tickers -> falls through to SOCIAL_GENERAL."""
        collector = RedditCollector()
        doc = {"title": "Check out $XYZZY", "selftext": ""}
        assert collector.extract_entity_id(doc) == "SOCIAL_GENERAL"

    def test_multiple_dollar_tickers_first_wins(self, aws_env):
        """Multiple $TICKER mentions -> first known ticker wins."""
        collector = RedditCollector()
        doc = {"title": "$MSFT vs $AAPL - which is better?", "selftext": ""}
        assert collector.extract_entity_id(doc) == "MSFT"


# ---------------------------------------------------------------------------
# Tests: score filter
# ---------------------------------------------------------------------------

class TestScoreFilter:
    @responses.activate
    def test_min_score_filters_low_posts(self, aws_env):
        """Post with score=2 and min_score=5 -> filtered out."""
        _mock_oauth()
        post_low = _make_reddit_post(post_id="low1", score=2, title="Low score post")
        post_high = _make_reddit_post(post_id="high1", score=50, title="High score post")
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(post_low, post_high),
            status=200,
        )
        collector = RedditCollector()
        items = collector.collect()

        assert len(items) == 1
        assert items[0]["reddit_id"] == "high1"


# ---------------------------------------------------------------------------
# Tests: age filter
# ---------------------------------------------------------------------------

class TestAgeFilter:
    @responses.activate
    def test_max_age_filters_old_posts(self, aws_env):
        """Post from 5 hours ago with max_age=2 -> filtered out."""
        _mock_oauth()
        old_post = _make_reddit_post(
            post_id="old1",
            title="Old post",
            score=100,
            created_utc=time.time() - (5 * 3600),  # 5 hours ago
        )
        recent_post = _make_reddit_post(
            post_id="new1",
            title="Recent post",
            score=100,
            created_utc=time.time() - 300,  # 5 minutes ago
        )
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(old_post, recent_post),
            status=200,
        )
        collector = RedditCollector()
        items = collector.collect()

        assert len(items) == 1
        assert items[0]["reddit_id"] == "new1"


# ---------------------------------------------------------------------------
# Tests: subreddit fetch errors
# ---------------------------------------------------------------------------

class TestFetchErrors:
    @responses.activate
    def test_403_skips_subreddit(self, aws_env):
        """403 response -> skip subreddit, no crash."""
        _mock_oauth()
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json={"error": "forbidden"},
            status=403,
        )
        collector = RedditCollector()
        items = collector.collect()
        assert items == []

    @responses.activate
    def test_404_skips_subreddit(self, aws_env):
        """404 response -> skip subreddit, no crash."""
        _mock_oauth()
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json={"error": "not found"},
            status=404,
        )
        collector = RedditCollector()
        items = collector.collect()
        assert items == []

    @responses.activate
    def test_invalid_json_skips(self, aws_env):
        """Non-JSON response -> skip, no crash."""
        _mock_oauth()
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            body="<html>error</html>",
            status=200,
            content_type="text/html",
        )
        collector = RedditCollector()
        items = collector.collect()
        assert items == []


# ---------------------------------------------------------------------------
# Tests: dedup by reddit_id (via BaseCollector content_hash)
# ---------------------------------------------------------------------------

class TestDedup:
    @responses.activate
    def test_dedup_by_content_hash(self, aws_env):
        """Same post appearing in two runs -> second is deduped."""
        _mock_oauth()
        post = _make_reddit_post(post_id="dup1", title="$AAPL to the moon", score=50)
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(post),
            status=200,
        )
        collector1 = RedditCollector()
        result1 = collector1.run()
        assert result1["stored"] == 1
        assert result1["duplicates"] == 0

        # Same post again — need fresh OAuth token too
        _mock_oauth()
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(post),
            status=200,
        )
        collector2 = RedditCollector()
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
        """End-to-end: collect -> S3 -> DynamoDB -> SQS."""
        _mock_oauth()
        post1 = _make_reddit_post(post_id="fp1", title="$NVDA earnings!", score=200)
        post2 = _make_reddit_post(post_id="fp2", title="Market outlook", score=30)
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(post1, post2),
            status=200,
        )
        collector = RedditCollector()
        result = collector.run()

        assert result["collected"] == 2
        assert result["stored"] == 2
        assert result["errors"] == 0

        # S3 artifacts
        s3 = boto3.client("s3", region_name=aws_env["region"])
        objects = s3.list_objects_v2(
            Bucket=aws_env["bucket"], Prefix="raw/REDDIT/"
        )
        assert objects["KeyCount"] == 2

        # DynamoDB records
        dynamodb = boto3.resource("dynamodb", region_name=aws_env["region"])
        table = dynamodb.Table(aws_env["table_name"])
        scan = table.scan()
        items = scan["Items"]
        assert len(items) == 2
        sources = {item["source"] for item in items}
        assert sources == {"REDDIT"}
        event_types = {item["event_type"] for item in items}
        assert event_types == {"SOCIAL_POST"}

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
        _mock_oauth()
        post = _make_reddit_post(post_id="lh1", title="$META breakout", score=75)
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(post),
            status=200,
        )
        result = lambda_handler({}, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["collected"] == 1
        assert body["stored"] == 1

    @responses.activate
    def test_lambda_handler_no_posts(self, aws_env):
        _mock_oauth()
        responses.add(
            responses.GET,
            f"{REDDIT_OAUTH_BASE}/r/stocks/new",
            json=_wrap_listing(),
            status=200,
        )
        result = lambda_handler({}, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["collected"] == 0


# ---------------------------------------------------------------------------
# Tests: subreddit configuration
# ---------------------------------------------------------------------------

class TestSubredditConfig:
    def test_default_subreddits(self, aws_env):
        """Without env override, uses default subreddits."""
        os.environ.pop("REDDIT_SUBREDDITS", None)
        collector = RedditCollector()
        assert collector._subreddits == ["stocks", "investing", "wallstreetbets", "StockMarket"]

    def test_custom_subreddits(self, aws_env):
        """REDDIT_SUBREDDITS env var overrides defaults."""
        os.environ["REDDIT_SUBREDDITS"] = "stocks,options"
        try:
            collector = RedditCollector()
            assert collector._subreddits == ["stocks", "options"]
        finally:
            os.environ["REDDIT_SUBREDDITS"] = "stocks"
