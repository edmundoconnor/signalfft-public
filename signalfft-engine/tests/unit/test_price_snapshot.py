"""Tests for outcome tracking price snapshot capture."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from signalfft_common.events import SignalScored


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

        sqs = boto3.client("sqs", region_name=region)
        input_q = sqs.create_queue(QueueName="test-signals")
        os.environ["OUTCOME_TRACKING_QUEUE_URL"] = input_q["QueueUrl"]

        # Alpaca keys (mocked — never hit real API)
        os.environ["ALPACA_API_KEY"] = "test-key"
        os.environ["ALPACA_SECRET_KEY"] = "test-secret"

        dynamodb = boto3.client("dynamodb", region_name=region)
        table_name = f"{env}-signalfft-outcomes"
        os.environ["OUTCOMES_TABLE"] = table_name
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
        }


def _outcomes_table(aws_env):
    """Get the DynamoDB Table resource for outcomes."""
    dynamo = boto3.resource("dynamodb", region_name=aws_env["region"])
    return dynamo.Table(aws_env["table_name"])


def _mock_trade(price: float = 150.0):
    """Create a mock Alpaca trade object."""
    trade = MagicMock()
    trade.price = price
    return trade


def _mock_quote(bid: float = 149.90, ask: float = 150.10):
    """Create a mock Alpaca quote object."""
    quote = MagicMock()
    quote.bid_price = bid
    quote.ask_price = ask
    return quote


def _mock_bar(close: float, volume: int):
    """Create a mock Alpaca bar object."""
    bar = MagicMock()
    bar.close = close
    bar.volume = volume
    return bar


def _make_signal_message(
    signal_id: str = "sig-001",
    entity_id: str = "AAPL",
    score: float = 0.85,
) -> dict:
    """Create a mock SQS message containing a SignalScored event."""
    event = SignalScored(
        timestamp="2026-01-15T14:30:00+00:00",
        source="signal_scoring",
        trace_id=str(uuid.uuid4()),
        payload={
            "signal_id": signal_id,
            "entity_id": entity_id,
            "score": score,
            "weight_version": "default",
            "attention_field_version": "v1",
        },
    )
    return {
        "MessageId": str(uuid.uuid4()),
        "ReceiptHandle": "test-receipt-handle",
        "Body": event.to_sqs_message(),
    }


# ===========================================================================
# capture_price_snapshot tests
# ===========================================================================


class TestSuccessfulPriceCapture:
    """Test successful price snapshot capture with all data available."""

    def test_capture_writes_complete_outcome(self, aws_env):
        """Should write a full outcome record with price, quote, and ADDV."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()

        # Mock latest trade
        data_client.get_stock_latest_trade.return_value = {
            "AAPL": _mock_trade(150.0),
        }
        # Mock latest quote
        data_client.get_stock_latest_quote.return_value = {
            "AAPL": _mock_quote(149.90, 150.10),
        }
        # Mock daily bars (5 bars for simplicity)
        bars = [_mock_bar(close=150.0, volume=1_000_000) for _ in range(5)]
        data_client.get_stock_bars.return_value = {"AAPL": bars}

        result = capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-001",
            entity_id="AAPL",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.85,
        )

        assert result["status"] == "CAPTURED"
        assert result["PK"] == "ENTITY#AAPL"
        assert result["SK"] == "OUTCOME#sig-001"
        assert result["price_at_signal"] == Decimal("150.0")
        assert result["bid_at_signal"] == Decimal("149.9")
        assert result["ask_at_signal"] == Decimal("150.1")
        assert result["spread_at_signal"] == Decimal(str(150.10 - 149.90))
        assert result["signal_score"] == Decimal("0.85")
        assert result["direction_score"] == Decimal("0.0")
        # ADDV: 150 * 1_000_000 = 150_000_000
        assert result["addv_20d"] == Decimal("150000000.0")

        # Deferred fields should be None
        assert result["price_t1d"] is None
        assert result["spread_adj_pct_change_t5d"] is None

    def test_outcome_persisted_in_dynamodb(self, aws_env):
        """Outcome should be retrievable from DynamoDB after capture."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()
        data_client.get_stock_latest_trade.return_value = {
            "BSX": _mock_trade(72.50),
        }
        data_client.get_stock_latest_quote.return_value = {
            "BSX": _mock_quote(72.40, 72.60),
        }
        data_client.get_stock_bars.return_value = {"BSX": []}

        capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-bsx",
            entity_id="BSX",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.72,
        )

        # Retrieve from DynamoDB
        response = table.get_item(
            Key={"PK": "ENTITY#BSX", "SK": "OUTCOME#sig-bsx"}
        )
        item = response["Item"]
        assert item["entity_id"] == "BSX"
        assert item["ticker"] == "BSX"
        assert item["signal_id"] == "sig-bsx"
        assert item["price_at_signal"] == Decimal("72.5")


class TestNonTradeableTicker:
    """Test handling of tickers that are not available on Alpaca."""

    def test_price_unavailable_status(self, aws_env):
        """Should write PRICE_UNAVAILABLE when Alpaca returns no data."""
        from alpaca.common.exceptions import APIError
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()

        # Simulate ticker not found — all calls raise APIError
        data_client.get_stock_latest_trade.side_effect = APIError("symbol not found")
        data_client.get_stock_latest_quote.side_effect = APIError("symbol not found")
        data_client.get_stock_bars.side_effect = APIError("symbol not found")

        result = capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-otc",
            entity_id="PINKOTC",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.45,
        )

        assert result["status"] == "PRICE_UNAVAILABLE"
        assert result["price_at_signal"] is None
        assert result["bid_at_signal"] is None
        assert result["ask_at_signal"] is None
        assert result["spread_at_signal"] is None
        assert result["addv_20d"] is None

        # Should still persist
        response = table.get_item(
            Key={"PK": "ENTITY#PINKOTC", "SK": "OUTCOME#sig-otc"}
        )
        assert response["Item"]["status"] == "PRICE_UNAVAILABLE"


class TestADDVCalculation:
    """Test 20-day average daily dollar volume computation."""

    def test_addv_with_varying_bars(self, aws_env):
        """ADDV should be mean(close * volume) across available bars."""
        from engine.outcome_tracking.price_snapshot import _compute_addv_20d

        data_client = MagicMock()
        bars = [
            _mock_bar(close=100.0, volume=500_000),  # $50M
            _mock_bar(close=102.0, volume=600_000),  # $61.2M
            _mock_bar(close=98.0, volume=400_000),   # $39.2M
        ]
        data_client.get_stock_bars.return_value = {"TEST": bars}

        result = _compute_addv_20d(data_client, "TEST")

        expected = (50_000_000 + 61_200_000 + 39_200_000) / 3
        assert abs(result - expected) < 0.01

    def test_addv_no_bars_returns_none(self, aws_env):
        """ADDV should be None when no bar data is available."""
        from engine.outcome_tracking.price_snapshot import _compute_addv_20d

        data_client = MagicMock()
        data_client.get_stock_bars.return_value = {"TEST": []}

        result = _compute_addv_20d(data_client, "TEST")
        assert result is None

    def test_addv_api_failure_returns_none(self, aws_env):
        """ADDV should be None when the API call fails entirely."""
        from alpaca.common.exceptions import APIError
        from engine.outcome_tracking.price_snapshot import _compute_addv_20d

        data_client = MagicMock()
        data_client.get_stock_bars.side_effect = APIError("rate limited")

        result = _compute_addv_20d(data_client, "TEST")
        assert result is None


class TestDynamoDBWrite:
    """Test DynamoDB record format and key structure."""

    def test_pk_sk_format(self, aws_env):
        """PK should be ENTITY#<ticker>, SK should be OUTCOME#<signal_id>."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()
        data_client.get_stock_latest_trade.return_value = {
            "CNMD": _mock_trade(80.0),
        }
        data_client.get_stock_latest_quote.return_value = {
            "CNMD": _mock_quote(79.95, 80.05),
        }
        data_client.get_stock_bars.return_value = {"CNMD": []}

        capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-cnmd-123",
            entity_id="CNMD",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.60,
        )

        response = table.get_item(
            Key={"PK": "ENTITY#CNMD", "SK": "OUTCOME#sig-cnmd-123"}
        )
        item = response["Item"]
        assert item["PK"] == "ENTITY#CNMD"
        assert item["SK"] == "OUTCOME#sig-cnmd-123"
        assert item["signal_id"] == "sig-cnmd-123"
        assert item["entity_id"] == "CNMD"
        assert item["signal_timestamp"] == "2026-01-15T14:30:00+00:00"

    def test_deferred_fields_are_null(self, aws_env):
        """Deferred price change fields should be None in the initial write."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()
        data_client.get_stock_latest_trade.return_value = {
            "MSFT": _mock_trade(400.0),
        }
        data_client.get_stock_latest_quote.return_value = {
            "MSFT": _mock_quote(399.95, 400.05),
        }
        data_client.get_stock_bars.return_value = {"MSFT": []}

        capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-msft",
            entity_id="MSFT",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.91,
        )

        response = table.get_item(
            Key={"PK": "ENTITY#MSFT", "SK": "OUTCOME#sig-msft"}
        )
        item = response["Item"]
        assert item["price_t1h"] is None
        assert item["price_t4h"] is None
        assert item["price_t1d"] is None
        assert item["price_t5d"] is None
        assert item["raw_pct_change_t1d"] is None
        assert item["spread_adj_pct_change_t1d"] is None
        assert item["raw_pct_change_t5d"] is None
        assert item["spread_adj_pct_change_t5d"] is None


class TestNonTickerSkip:
    """Test that non-ticker entities are skipped without Alpaca calls."""

    def test_market_general_skipped(self, aws_env):
        """MARKET_GENERAL should be skipped immediately — no Alpaca calls."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()

        result = capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-mktgen",
            entity_id="MARKET_GENERAL",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.50,
        )

        assert result["status"] == "PRICE_UNAVAILABLE"
        assert result["price_at_signal"] is None
        # No Alpaca API calls should have been made
        data_client.get_stock_latest_trade.assert_not_called()
        data_client.get_stock_latest_quote.assert_not_called()
        data_client.get_stock_bars.assert_not_called()

    def test_underscore_entity_skipped(self, aws_env):
        """Any entity with underscores should be skipped."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()

        result = capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-cik",
            entity_id="CIK_UNKNOWN",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.30,
        )

        assert result["status"] == "PRICE_UNAVAILABLE"
        data_client.get_stock_latest_trade.assert_not_called()

    def test_valid_ticker_not_skipped(self, aws_env):
        """Regular tickers like AAPL should NOT be skipped."""
        from engine.outcome_tracking.price_snapshot import _is_non_ticker

        assert _is_non_ticker("AAPL") is False
        assert _is_non_ticker("BSX") is False
        assert _is_non_ticker("A") is False

    def test_non_ticker_detection(self, aws_env):
        """Entities with underscores or spaces should be detected."""
        from engine.outcome_tracking.price_snapshot import _is_non_ticker

        assert _is_non_ticker("MARKET_GENERAL") is True
        assert _is_non_ticker("CIK_UNKNOWN") is True
        assert _is_non_ticker("SOME ENTITY") is True

    def test_skipped_entity_persisted(self, aws_env):
        """Skipped entities should still be written to DynamoDB."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()

        capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-skip",
            entity_id="MARKET_GENERAL",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.50,
        )

        response = table.get_item(
            Key={"PK": "ENTITY#MARKET_GENERAL", "SK": "OUTCOME#sig-skip"}
        )
        assert response["Item"]["status"] == "PRICE_UNAVAILABLE"
        assert response["Item"]["signal_score"] == Decimal("0.5")


class TestDirectionScorePassthrough:
    """Test direction_score parameter passthrough in capture_price_snapshot."""

    def test_direction_score_persisted(self, aws_env):
        """direction_score parameter should be persisted in outcome record."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()
        data_client.get_stock_latest_trade.return_value = {
            "AAPL": _mock_trade(150.0),
        }
        data_client.get_stock_latest_quote.return_value = {
            "AAPL": _mock_quote(149.90, 150.10),
        }
        data_client.get_stock_bars.return_value = {"AAPL": []}

        result = capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-dir",
            entity_id="AAPL",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.85,
            direction_score=0.42,
        )

        assert result["direction_score"] == Decimal("0.42")
        response = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-dir"}
        )
        assert response["Item"]["direction_score"] == Decimal("0.42")

    def test_direction_score_default_zero(self, aws_env):
        """When direction_score is omitted, it should default to 0.0."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()
        data_client.get_stock_latest_trade.return_value = {
            "MSFT": _mock_trade(400.0),
        }
        data_client.get_stock_latest_quote.return_value = {
            "MSFT": _mock_quote(399.95, 400.05),
        }
        data_client.get_stock_bars.return_value = {"MSFT": []}

        result = capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-def",
            entity_id="MSFT",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.90,
        )

        assert result["direction_score"] == Decimal("0.0")

    def test_non_ticker_stores_direction_score(self, aws_env):
        """Non-ticker entities should also store direction_score."""
        from engine.outcome_tracking.price_snapshot import capture_price_snapshot

        table = _outcomes_table(aws_env)
        data_client = MagicMock()

        result = capture_price_snapshot(
            data_client=data_client,
            outcomes_table=table,
            signal_id="sig-nontick",
            entity_id="MARKET_GENERAL",
            signal_timestamp="2026-01-15T14:30:00+00:00",
            signal_score=0.50,
            direction_score=-0.3,
        )

        assert result["direction_score"] == Decimal("-0.3")
        response = table.get_item(
            Key={"PK": "ENTITY#MARKET_GENERAL", "SK": "OUTCOME#sig-nontick"}
        )
        assert response["Item"]["direction_score"] == Decimal("-0.3")


class TestRetryBehavior:
    """Test exponential backoff retry on Alpaca API failures."""

    @patch("engine.outcome_tracking.price_snapshot.time.sleep")
    def test_retry_on_transient_error(self, mock_sleep, aws_env):
        """Should retry up to 3 times with exponential backoff on transient errors."""
        from alpaca.common.exceptions import APIError
        from engine.outcome_tracking.price_snapshot import _retry_alpaca

        func = MagicMock(side_effect=[
            APIError("rate limited"),
            APIError("rate limited"),
            "success",
        ])

        result = _retry_alpaca(func, "arg1")

        assert result == "success"
        assert func.call_count == 3
        # Backoff: 0.5s, 1.0s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.5)
        mock_sleep.assert_any_call(1.0)

    @patch("engine.outcome_tracking.price_snapshot.time.sleep")
    def test_retry_exhaustion_raises(self, mock_sleep, aws_env):
        """Should raise after all retries are exhausted."""
        from alpaca.common.exceptions import APIError
        from engine.outcome_tracking.price_snapshot import _retry_alpaca

        func = MagicMock(side_effect=APIError("persistent failure"))

        with pytest.raises(APIError):
            _retry_alpaca(func, "arg1")

        assert func.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("engine.outcome_tracking.price_snapshot.time.sleep")
    def test_403_not_retried(self, mock_sleep, aws_env):
        """403 errors should raise immediately without retrying."""
        from alpaca.common.exceptions import APIError
        from engine.outcome_tracking.price_snapshot import _retry_alpaca

        func = MagicMock(
            side_effect=APIError("subscription does not permit querying recent SIP data")
        )

        with pytest.raises(APIError):
            _retry_alpaca(func, "arg1")

        assert func.call_count == 1
        mock_sleep.assert_not_called()

    @patch("engine.outcome_tracking.price_snapshot.time.sleep")
    def test_invalid_symbol_not_retried(self, mock_sleep, aws_env):
        """Invalid symbol errors should raise immediately without retrying."""
        from alpaca.common.exceptions import APIError
        from engine.outcome_tracking.price_snapshot import _retry_alpaca

        func = MagicMock(
            side_effect=APIError("code=400, message=invalid symbol: MARKET_GENERAL")
        )

        with pytest.raises(APIError):
            _retry_alpaca(func, "arg1")

        assert func.call_count == 1
        mock_sleep.assert_not_called()

    @patch("engine.outcome_tracking.price_snapshot.time.sleep")
    def test_forbidden_not_retried(self, mock_sleep, aws_env):
        """Errors containing '403' should raise immediately."""
        from alpaca.common.exceptions import APIError
        from engine.outcome_tracking.price_snapshot import _retry_alpaca

        func = MagicMock(side_effect=APIError("403 Forbidden"))

        with pytest.raises(APIError):
            _retry_alpaca(func, "arg1")

        assert func.call_count == 1
        mock_sleep.assert_not_called()


# ===========================================================================
# OutcomeTrackingService tests
# ===========================================================================


class TestServiceProcessMessage:
    """Test the SQS service wrapper end-to-end."""

    @patch("engine.outcome_tracking.service.StockHistoricalDataClient")
    def test_process_message_end_to_end(self, mock_client_class, aws_env):
        """Full flow: SQS message -> price capture -> DynamoDB write -> ack."""
        mock_data_client = MagicMock()
        mock_client_class.return_value = mock_data_client

        mock_data_client.get_stock_latest_trade.return_value = {
            "AAPL": _mock_trade(150.0),
        }
        mock_data_client.get_stock_latest_quote.return_value = {
            "AAPL": _mock_quote(149.90, 150.10),
        }
        mock_data_client.get_stock_bars.return_value = {
            "AAPL": [_mock_bar(150.0, 1_000_000)],
        }

        from engine.outcome_tracking.service import OutcomeTrackingService
        service = OutcomeTrackingService()

        message = _make_signal_message(
            signal_id="sig-e2e",
            entity_id="AAPL",
            score=0.85,
        )
        service.process_message(message)

        # Verify outcome stored in DynamoDB
        table = _outcomes_table(aws_env)
        response = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-e2e"}
        )
        item = response["Item"]
        assert item["entity_id"] == "AAPL"
        assert item["status"] == "CAPTURED"


class TestServiceLifecycle:
    """Test service initialization and lifecycle."""

    @patch("engine.outcome_tracking.service.StockHistoricalDataClient")
    def test_stop_sets_flag(self, mock_client_class, aws_env):
        """stop() should set _running to False."""
        from engine.outcome_tracking.service import OutcomeTrackingService
        service = OutcomeTrackingService()
        assert service._running is True
        service.stop()
        assert service._running is False

    @patch("engine.outcome_tracking.service.StockHistoricalDataClient")
    def test_service_init_defaults(self, mock_client_class, aws_env):
        """Service should pick up env vars and set correct defaults."""
        from engine.outcome_tracking.service import OutcomeTrackingService
        service = OutcomeTrackingService()
        assert service._region == "us-east-1"
        assert service._env == "test"
        assert service.input_queue_url == aws_env["input_queue_url"]
        assert service._poll_interval == 5
        assert service._running is True

    def test_service_without_alpaca_keys(self, aws_env):
        """Service should initialize but log warning when keys are missing."""
        os.environ.pop("ALPACA_API_KEY", None)
        os.environ.pop("ALPACA_SECRET_KEY", None)

        from engine.outcome_tracking.service import OutcomeTrackingService
        service = OutcomeTrackingService()
        assert service._data_client is None

    @patch("engine.outcome_tracking.service.StockHistoricalDataClient")
    def test_poll_messages_empty(self, mock_client_class, aws_env):
        """When no messages are in the queue, poll should return empty list."""
        from engine.outcome_tracking.service import OutcomeTrackingService
        service = OutcomeTrackingService()
        messages = service._poll_messages()
        assert messages == []

    @patch("engine.outcome_tracking.service.StockHistoricalDataClient")
    def test_process_message_error_doesnt_crash(self, mock_client_class, aws_env):
        """A malformed message should be logged but not crash the service."""
        from engine.outcome_tracking.service import OutcomeTrackingService
        service = OutcomeTrackingService()
        bad_message = {
            "MessageId": "bad-msg-001",
            "ReceiptHandle": "bad-receipt",
            "Body": '{"this_is": "not_a_valid_event"}',
        }
        service.process_message(bad_message)
