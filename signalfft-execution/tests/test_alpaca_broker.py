"""Tests for the Alpaca paper trading broker adapter."""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from execution.adapters.alpaca_broker import AlpacaBroker


def _make_order(**overrides):
    order = {
        "candidate_id": "tc-001",
        "entity_id": "AAPL",
        "direction": "BUY",
        "quantity": 100,
        "order_type": "MARKET",
        "limit_price": 100.0,
    }
    order.update(overrides)
    return order


def _mock_alpaca_order(
    order_id="order-123",
    status=None,
    filled_avg_price=None,
    filled_qty=None,
    filled_at=None,
):
    """Create a mock Alpaca order object."""
    from alpaca.trading.enums import OrderStatus

    mock = MagicMock()
    mock.id = order_id
    mock.status = status or OrderStatus.FILLED
    mock.filled_avg_price = filled_avg_price or "150.25"
    mock.filled_qty = filled_qty or "13.3"
    mock.filled_at = filled_at or datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
    return mock


@pytest.fixture
def alpaca_broker(monkeypatch):
    """Create an AlpacaBroker with mocked TradingClient."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_NOTIONAL", "2000")

    with patch("execution.adapters.alpaca_broker.TradingClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        broker = AlpacaBroker()
        broker._mock_client = mock_client  # expose for test assertions
        yield broker


class TestSubmitOrderSuccess:
    def test_filled_order_returns_correct_fields(self, alpaca_broker):
        """Mock TradingClient.submit_order to return a filled order."""
        from alpaca.trading.enums import OrderStatus

        mock_submitted = _mock_alpaca_order(status=OrderStatus.NEW)
        alpaca_broker._mock_client.submit_order.return_value = mock_submitted

        mock_filled = _mock_alpaca_order(
            status=OrderStatus.FILLED,
            filled_avg_price="150.25",
            filled_qty="13.3",
        )
        alpaca_broker._mock_client.get_order_by_id.return_value = mock_filled

        result = alpaca_broker.submit_order(_make_order())

        assert result["order_id"] == "order-123"
        assert result["status"] == "FILLED"
        assert result["fill_price"] == 150.25
        assert result["quantity"] == 13.3
        assert result["direction"] == "BUY"
        assert result["slippage"] == 0.0
        assert result["candidate_id"] == "tc-001"
        assert result["entity_id"] == "AAPL"
        assert "latency_ms" in result
        assert "timestamp" in result

    def test_entity_prefix_stripped(self, alpaca_broker):
        """ENTITY# prefix should be stripped from entity_id."""
        from alpaca.trading.enums import OrderStatus

        alpaca_broker._mock_client.submit_order.return_value = _mock_alpaca_order(status=OrderStatus.NEW)
        alpaca_broker._mock_client.get_order_by_id.return_value = _mock_alpaca_order(status=OrderStatus.FILLED)

        result = alpaca_broker.submit_order(_make_order(entity_id="ENTITY#TSLA"))

        call_args = alpaca_broker._mock_client.submit_order.call_args[0][0]
        assert call_args.symbol == "TSLA"
        assert result["status"] == "FILLED"


class TestSubmitOrderSkip:
    def test_market_general_skip(self, alpaca_broker):
        """candidate with entity_id=MARKET_GENERAL should be skipped."""
        result = alpaca_broker.submit_order(_make_order(entity_id="MARKET_GENERAL"))
        assert result["status"] == "SKIPPED"
        assert "Non-tradeable" in result["error"]
        alpaca_broker._mock_client.submit_order.assert_not_called()

    def test_social_general_skip(self, alpaca_broker):
        """candidate with entity_id=SOCIAL_GENERAL should be skipped."""
        result = alpaca_broker.submit_order(_make_order(entity_id="SOCIAL_GENERAL"))
        assert result["status"] == "SKIPPED"
        assert "Non-tradeable" in result["error"]

    def test_cik_number_skip(self, alpaca_broker):
        """candidate with entity_id that is all digits (CIK) should be skipped."""
        result = alpaca_broker.submit_order(_make_order(entity_id="1234567"))
        assert result["status"] == "SKIPPED"
        assert "Non-tradeable" in result["error"]

    def test_cik_with_entity_prefix_skip(self, alpaca_broker):
        """ENTITY#1234567 should strip prefix then detect CIK."""
        result = alpaca_broker.submit_order(_make_order(entity_id="ENTITY#1234567"))
        assert result["status"] == "SKIPPED"

    def test_cik_with_cik_prefix_skip(self, alpaca_broker):
        """CIK_0001811972 should be detected as a CIK identifier."""
        result = alpaca_broker.submit_order(_make_order(entity_id="CIK_0001811972"))
        assert result["status"] == "SKIPPED"
        assert "Non-tradeable" in result["error"]


class TestSubmitOrderErrors:
    def test_api_error(self, alpaca_broker):
        """Alpaca API error should return error result, not raise."""
        from alpaca.common.exceptions import APIError

        alpaca_broker._mock_client.submit_order.side_effect = APIError({"message": "insufficient buying power"})

        result = alpaca_broker.submit_order(_make_order())

        assert result["status"] == "ERROR"
        assert "insufficient buying power" in result["error"]
        assert result["fill_price"] == 0.0

    def test_unexpected_error(self, alpaca_broker):
        """Unexpected exceptions should return error result, not crash."""
        alpaca_broker._mock_client.submit_order.side_effect = ConnectionError("network timeout")

        result = alpaca_broker.submit_order(_make_order())

        assert result["status"] == "ERROR"
        assert "network timeout" in result["error"]


class TestWaitForFill:
    def test_timeout_returns_pending(self, alpaca_broker):
        """If order never fills within timeout, return status=PENDING."""
        from alpaca.trading.enums import OrderStatus

        alpaca_broker._mock_client.submit_order.return_value = _mock_alpaca_order(status=OrderStatus.NEW)
        alpaca_broker._mock_client.get_order_by_id.return_value = _mock_alpaca_order(status=OrderStatus.NEW)

        result = alpaca_broker.submit_order(_make_order())
        # Override timeout for speed — call _wait_for_fill directly
        # But submit_order uses default 30s, so let's test _wait_for_fill directly
        result = alpaca_broker._wait_for_fill(
            order_id="order-123",
            candidate_id="tc-001",
            entity_id="AAPL",
            submitted_at=datetime.now(timezone.utc),
            timeout_seconds=0,  # immediate timeout
            poll_interval=0.01,
        )

        assert result["status"] == "PENDING"
        assert result["order_id"] == "order-123"

    def test_partial_then_filled(self, alpaca_broker):
        """First call returns PARTIALLY_FILLED, second returns FILLED."""
        from alpaca.trading.enums import OrderStatus

        partial = _mock_alpaca_order(status=OrderStatus.PARTIALLY_FILLED)
        filled = _mock_alpaca_order(
            status=OrderStatus.FILLED,
            filled_avg_price="200.50",
            filled_qty="10.0",
        )
        alpaca_broker._mock_client.get_order_by_id.side_effect = [partial, filled]

        result = alpaca_broker._wait_for_fill(
            order_id="order-123",
            candidate_id="tc-001",
            entity_id="AAPL",
            submitted_at=datetime.now(timezone.utc),
            timeout_seconds=5,
            poll_interval=0.01,
        )

        assert result["status"] == "FILLED"
        assert result["fill_price"] == 200.50
        assert result["quantity"] == 10.0

    def test_rejected_order(self, alpaca_broker):
        """Rejected order returns immediately with REJECTED status."""
        from alpaca.trading.enums import OrderStatus

        rejected = _mock_alpaca_order(status=OrderStatus.REJECTED)
        alpaca_broker._mock_client.get_order_by_id.return_value = rejected

        result = alpaca_broker._wait_for_fill(
            order_id="order-123",
            candidate_id="tc-001",
            entity_id="AAPL",
            submitted_at=datetime.now(timezone.utc),
            timeout_seconds=5,
            poll_interval=0.01,
        )

        assert result["status"] == "REJECTED"


class TestDisabledAdapter:
    def test_disabled_when_no_keys(self, monkeypatch):
        """Without API keys, adapter is disabled."""
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        with patch("execution.adapters.alpaca_broker.TradingClient"):
            broker = AlpacaBroker()

        assert broker.enabled is False
        assert broker.client is None

    def test_disabled_submit_returns_error(self, monkeypatch):
        """Disabled adapter returns error result from submit_order."""
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

        with patch("execution.adapters.alpaca_broker.TradingClient"):
            broker = AlpacaBroker()

        result = broker.submit_order(_make_order())
        assert result["status"] == "ERROR"
        assert "disabled" in result["error"].lower()


class TestGetAccount:
    def test_get_account_returns_info(self, alpaca_broker):
        """get_account returns buying_power, portfolio_value, cash, positions_count."""
        mock_account = MagicMock()
        mock_account.buying_power = "98000.00"
        mock_account.portfolio_value = "100000.00"
        mock_account.cash = "98000.00"
        alpaca_broker._mock_client.get_account.return_value = mock_account
        alpaca_broker._mock_client.get_all_positions.return_value = [MagicMock(), MagicMock()]

        result = alpaca_broker.get_account()

        assert result["buying_power"] == 98000.00
        assert result["portfolio_value"] == 100000.00
        assert result["cash"] == 98000.00
        assert result["positions_count"] == 2

    def test_get_account_disabled(self, monkeypatch):
        """Disabled adapter returns error from get_account."""
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with patch("execution.adapters.alpaca_broker.TradingClient"):
            broker = AlpacaBroker()

        result = broker.get_account()
        assert "error" in result


class TestRouterAlpacaMode:
    def test_router_instantiates_alpaca_broker(self, monkeypatch):
        """BROKER_MODE=alpaca should instantiate AlpacaBroker."""
        monkeypatch.setenv("INPUT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/q")
        monkeypatch.setenv("BROKER_MODE", "alpaca")
        monkeypatch.setenv("EXECUTION_TELEMETRY_TABLE", "")
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        monkeypatch.delenv("GRAPH_EDGES_TABLE", raising=False)

        with (
            patch("execution.router.boto3") as mock_boto3,
            patch("execution.telemetry.boto3"),
            patch("execution.router.GraphWriter"),
            patch("execution.adapters.alpaca_broker.TradingClient") as mock_tc_cls,
        ):
            mock_boto3.client.return_value = MagicMock()
            mock_client = MagicMock()
            mock_tc_cls.return_value = mock_client

            # Mock get_account for startup logging
            mock_account = MagicMock()
            mock_account.buying_power = "98000"
            mock_account.portfolio_value = "100000"
            mock_client.get_account.return_value = mock_account
            mock_client.get_all_positions.return_value = []

            from execution.router import ExecutionRouter

            router = ExecutionRouter()

        assert isinstance(router._broker, AlpacaBroker)
        assert router._broker.enabled is True

    def test_router_skips_non_tradeable_entity(self, monkeypatch):
        """Router returns None for SKIPPED orders (non-tradeable symbols)."""
        monkeypatch.setenv("INPUT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/q")
        monkeypatch.setenv("BROKER_MODE", "alpaca")
        monkeypatch.setenv("EXECUTION_TELEMETRY_TABLE", "")
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        monkeypatch.delenv("GRAPH_EDGES_TABLE", raising=False)

        with (
            patch("execution.router.boto3") as mock_boto3,
            patch("execution.telemetry.boto3"),
            patch("execution.router.GraphWriter"),
            patch("execution.adapters.alpaca_broker.TradingClient") as mock_tc_cls,
        ):
            mock_boto3.client.return_value = MagicMock()
            mock_client = MagicMock()
            mock_tc_cls.return_value = mock_client
            mock_account = MagicMock()
            mock_account.buying_power = "98000"
            mock_account.portfolio_value = "100000"
            mock_client.get_account.return_value = mock_account
            mock_client.get_all_positions.return_value = []

            from execution.router import ExecutionRouter

            router = ExecutionRouter()

        message = {
            "payload": {
                "candidate_id": "tc-001",
                "signal_id": "sig-001",
                "entity_id": "MARKET_GENERAL",
                "score": 0.87,
            },
        }
        outcome = router.process_candidate(message)
        assert outcome is None
