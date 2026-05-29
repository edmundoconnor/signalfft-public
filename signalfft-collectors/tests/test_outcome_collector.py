"""Comprehensive tests for the deferred price outcome collector."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# Ensure src directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures & helpers
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
        }


def _outcomes_table(aws_env):
    """Get the DynamoDB Table resource for outcomes."""
    return boto3.resource(
        "dynamodb", region_name=aws_env["region"],
    ).Table(aws_env["table_name"])


def _put_outcome(aws_env, entity_id="AAPL", signal_id="sig-001", **overrides):
    """Insert a test outcome record and return it."""
    table = _outcomes_table(aws_env)
    item = {
        "PK": f"ENTITY#{entity_id}",
        "SK": f"OUTCOME#{signal_id}",
        "signal_id": signal_id,
        "entity_id": entity_id,
        "ticker": entity_id,
        "signal_timestamp": "2026-02-20T14:30:00+00:00",
        "price_at_signal": Decimal("150.0"),
        "bid_at_signal": Decimal("149.90"),
        "ask_at_signal": Decimal("150.10"),
        "spread_at_signal": Decimal("0.20"),
        "addv_20d": Decimal("150000000"),
        "signal_score": Decimal("0.85"),
        "direction_score": Decimal("0.0"),
        "status": "CAPTURED",
        "price_t1h": None,
        "price_t4h": None,
        "price_t1d": None,
        "price_t5d": None,
        "raw_pct_change_t1d": None,
        "spread_adj_pct_change_t1d": None,
        "raw_pct_change_t5d": None,
        "spread_adj_pct_change_t5d": None,
    }
    item.update(overrides)
    table.put_item(Item=item)
    return item


def _mock_bar(close: float, timestamp: datetime | None = None, volume: int = 100_000):
    """Create a mock Alpaca bar object."""
    bar = MagicMock()
    bar.close = close
    bar.volume = volume
    bar.timestamp = timestamp or datetime(2026, 2, 20, 15, 0, 0, tzinfo=timezone.utc)
    return bar


# ---------------------------------------------------------------------------
# Test 1: Scan eligible outcomes
# ---------------------------------------------------------------------------


class TestScanEligibleOutcomes:
    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_returns_captured_with_null_prices(self, mock_client_class, aws_env):
        """CAPTURED outcomes with NULL price fields should be returned."""
        _put_outcome(aws_env, signal_id="sig-001")
        _put_outcome(aws_env, signal_id="sig-002", entity_id="BSX")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        items = collector.scan_eligible_outcomes()

        assert len(items) == 2
        signal_ids = {item["signal_id"] for item in items}
        assert "sig-001" in signal_ids
        assert "sig-002" in signal_ids

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_ignores_complete_status(self, mock_client_class, aws_env):
        """COMPLETE outcomes should not be returned."""
        _put_outcome(aws_env, signal_id="sig-done", status="COMPLETE",
                     price_t1h=Decimal("151"), price_t4h=Decimal("152"),
                     price_t1d=Decimal("153"), price_t5d=Decimal("155"))

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        items = collector.scan_eligible_outcomes()

        assert len(items) == 0

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_ignores_price_unavailable(self, mock_client_class, aws_env):
        """PRICE_UNAVAILABLE outcomes should not be returned."""
        _put_outcome(aws_env, signal_id="sig-na", status="PRICE_UNAVAILABLE")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        items = collector.scan_eligible_outcomes()

        assert len(items) == 0

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_respects_scan_limit(self, mock_client_class, aws_env):
        """Should return at most OUTCOME_SCAN_LIMIT items."""
        for i in range(5):
            _put_outcome(aws_env, signal_id=f"sig-{i:03d}", entity_id=f"T{i}")

        os.environ["OUTCOME_SCAN_LIMIT"] = "3"
        try:
            from collectors.outcome.collector import OutcomeCollector
            collector = OutcomeCollector()
            items = collector.scan_eligible_outcomes()
            assert len(items) <= 3
        finally:
            os.environ.pop("OUTCOME_SCAN_LIMIT", None)

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_returns_partially_filled(self, mock_client_class, aws_env):
        """Outcomes with some filled and some NULL prices should be returned."""
        _put_outcome(aws_env, signal_id="sig-partial",
                     price_t1h=Decimal("151"), price_t4h=Decimal("152"))

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        items = collector.scan_eligible_outcomes()

        assert len(items) == 1
        assert items[0]["signal_id"] == "sig-partial"


# ---------------------------------------------------------------------------
# Test 2: Window elapsed
# ---------------------------------------------------------------------------


class TestWindowElapsed:
    def test_t1h_elapsed(self):
        """t1h should be elapsed 1 hour after signal."""
        from collectors.outcome.collector import _window_elapsed

        signal_ts = "2026-02-20T14:30:00+00:00"
        now = datetime(2026, 2, 20, 15, 31, 0, tzinfo=timezone.utc)
        elapsed, target = _window_elapsed(signal_ts, "t1h", now)

        assert elapsed is True
        assert target == datetime(2026, 2, 20, 15, 30, 0, tzinfo=timezone.utc)

    def test_t1h_not_elapsed(self):
        """t1h should NOT be elapsed 30 min after signal."""
        from collectors.outcome.collector import _window_elapsed

        signal_ts = "2026-02-20T14:30:00+00:00"
        now = datetime(2026, 2, 20, 15, 0, 0, tzinfo=timezone.utc)
        elapsed, _ = _window_elapsed(signal_ts, "t1h", now)

        assert elapsed is False

    def test_t4h_elapsed(self):
        """t4h should be elapsed 4+ hours after signal."""
        from collectors.outcome.collector import _window_elapsed

        signal_ts = "2026-02-20T14:30:00+00:00"
        now = datetime(2026, 2, 20, 18, 31, 0, tzinfo=timezone.utc)
        elapsed, target = _window_elapsed(signal_ts, "t4h", now)

        assert elapsed is True
        assert target == datetime(2026, 2, 20, 18, 30, 0, tzinfo=timezone.utc)

    def test_t4h_not_elapsed(self):
        """t4h should NOT be elapsed 2 hours after signal."""
        from collectors.outcome.collector import _window_elapsed

        signal_ts = "2026-02-20T14:30:00+00:00"
        now = datetime(2026, 2, 20, 16, 30, 0, tzinfo=timezone.utc)
        elapsed, _ = _window_elapsed(signal_ts, "t4h", now)

        assert elapsed is False

    def test_t1d_elapsed(self):
        """t1d should be elapsed after next trading day close."""
        from collectors.outcome.collector import _window_elapsed

        # Signal on Friday 2026-02-20 → next trading day is Mon 2026-02-23
        signal_ts = "2026-02-20T14:30:00+00:00"
        now = datetime(2026, 2, 23, 20, 1, 0, tzinfo=timezone.utc)
        elapsed, target = _window_elapsed(signal_ts, "t1d", now)

        assert elapsed is True
        assert target == datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)

    def test_t1d_not_elapsed(self):
        """t1d should NOT be elapsed same day as signal."""
        from collectors.outcome.collector import _window_elapsed

        signal_ts = "2026-02-20T14:30:00+00:00"
        now = datetime(2026, 2, 20, 21, 0, 0, tzinfo=timezone.utc)
        elapsed, _ = _window_elapsed(signal_ts, "t1d", now)

        assert elapsed is False

    def test_t1d_friday_to_monday(self):
        """Signal on Friday → t1d target should be Monday 20:00 UTC."""
        from collectors.outcome.collector import _window_elapsed

        # 2026-02-20 is a Friday
        signal_ts = "2026-02-20T14:30:00+00:00"
        _, target = _window_elapsed(signal_ts, "t1d", datetime.now(timezone.utc))

        # Next trading day after Friday is Monday 2026-02-23
        assert target == datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)

    def test_t5d_elapsed(self):
        """t5d should be elapsed after 5 trading days."""
        from collectors.outcome.collector import _window_elapsed

        # Signal on Monday 2026-02-16 → 5 trading days: Tue-Mon (skip weekend)
        signal_ts = "2026-02-16T14:30:00+00:00"
        now = datetime(2026, 2, 23, 20, 1, 0, tzinfo=timezone.utc)
        elapsed, target = _window_elapsed(signal_ts, "t5d", now)

        assert elapsed is True
        # 5 trading days after Mon 16: Tue 17, Wed 18, Thu 19, Fri 20, Mon 23
        assert target == datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)

    def test_t5d_weekend_boundary(self):
        """t5d should skip weekends in the count."""
        from collectors.outcome.collector import _window_elapsed

        # Signal on Wednesday 2026-02-18
        signal_ts = "2026-02-18T14:30:00+00:00"
        _, target = _window_elapsed(signal_ts, "t5d", datetime.now(timezone.utc))

        # 5 trading days: Thu 19, Fri 20, (skip Sat/Sun), Mon 23, Tue 24, Wed 25
        assert target == datetime(2026, 2, 25, 20, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test 3: Trading day calculation
# ---------------------------------------------------------------------------


class TestTradingDayCalculation:
    def test_next_trading_day_from_monday(self):
        """Next trading day after Monday should be Tuesday."""
        from collectors.outcome.collector import _next_trading_day

        # 2026-02-16 is Monday
        dt = datetime(2026, 2, 16, 14, 0, 0, tzinfo=timezone.utc)
        result = _next_trading_day(dt)

        assert result == datetime(2026, 2, 17, 20, 0, 0, tzinfo=timezone.utc)

    def test_next_trading_day_from_friday(self):
        """Next trading day after Friday should be Monday."""
        from collectors.outcome.collector import _next_trading_day

        # 2026-02-20 is Friday
        dt = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)
        result = _next_trading_day(dt)

        assert result == datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)

    def test_next_trading_day_from_saturday(self):
        """Next trading day after Saturday should be Monday."""
        from collectors.outcome.collector import _next_trading_day

        # 2026-02-21 is Saturday
        dt = datetime(2026, 2, 21, 10, 0, 0, tzinfo=timezone.utc)
        result = _next_trading_day(dt)

        assert result == datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)

    def test_nth_trading_day_1_equals_next(self):
        """nth_trading_day_close(dt, 1) should equal next_trading_day(dt)."""
        from collectors.outcome.collector import _next_trading_day, _nth_trading_day_close

        dt = datetime(2026, 2, 18, 14, 0, 0, tzinfo=timezone.utc)
        assert _nth_trading_day_close(dt, 1) == _next_trading_day(dt)

    def test_nth_trading_day_5_within_week(self):
        """5 trading days from Monday → next Monday."""
        from collectors.outcome.collector import _nth_trading_day_close

        # Monday 2026-02-16
        dt = datetime(2026, 2, 16, 14, 0, 0, tzinfo=timezone.utc)
        result = _nth_trading_day_close(dt, 5)

        # Tue 17, Wed 18, Thu 19, Fri 20, Mon 23
        assert result == datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)

    def test_nth_trading_day_5_spanning_weekend(self):
        """5 trading days from Wednesday → next Wednesday."""
        from collectors.outcome.collector import _nth_trading_day_close

        # Wednesday 2026-02-18
        dt = datetime(2026, 2, 18, 14, 0, 0, tzinfo=timezone.utc)
        result = _nth_trading_day_close(dt, 5)

        # Thu 19, Fri 20, Mon 23, Tue 24, Wed 25
        assert result == datetime(2026, 2, 25, 20, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test 4: Price fetching
# ---------------------------------------------------------------------------


class TestPriceFetching:
    @patch("collectors.outcome.collector.time.sleep")
    def test_hourly_bar_success(self, mock_sleep):
        """Should return the close price from the nearest hourly bar."""
        from collectors.outcome.collector import _fetch_hourly_bar_close

        target = datetime(2026, 2, 20, 15, 30, 0, tzinfo=timezone.utc)
        bar = _mock_bar(close=151.50, timestamp=target)
        data_client = MagicMock()
        data_client.get_stock_bars.return_value = {"AAPL": [bar]}

        result = _fetch_hourly_bar_close(data_client, "AAPL", target)

        assert result == 151.50

    @patch("collectors.outcome.collector.time.sleep")
    def test_hourly_bar_no_data(self, mock_sleep):
        """Should return None when no bars are available."""
        from collectors.outcome.collector import _fetch_hourly_bar_close

        target = datetime(2026, 2, 20, 15, 30, 0, tzinfo=timezone.utc)
        data_client = MagicMock()
        data_client.get_stock_bars.return_value = {"AAPL": []}

        result = _fetch_hourly_bar_close(data_client, "AAPL", target)

        assert result is None

    @patch("collectors.outcome.collector.time.sleep")
    def test_daily_bar_success(self, mock_sleep):
        """Should return the close price from the nearest daily bar."""
        from collectors.outcome.collector import _fetch_daily_bar_close

        target = datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)
        bar = _mock_bar(
            close=155.00,
            timestamp=datetime(2026, 2, 23, 5, 0, 0, tzinfo=timezone.utc),
        )
        data_client = MagicMock()
        data_client.get_stock_bars.return_value = {"AAPL": [bar]}

        result = _fetch_daily_bar_close(data_client, "AAPL", target)

        assert result == 155.00

    @patch("collectors.outcome.collector.time.sleep")
    def test_daily_bar_no_data(self, mock_sleep):
        """Should return None when no daily bars are available."""
        from collectors.outcome.collector import _fetch_daily_bar_close

        target = datetime(2026, 2, 23, 20, 0, 0, tzinfo=timezone.utc)
        data_client = MagicMock()
        data_client.get_stock_bars.return_value = {"AAPL": []}

        result = _fetch_daily_bar_close(data_client, "AAPL", target)

        assert result is None

    @patch("collectors.outcome.collector.time.sleep")
    def test_hourly_bar_selects_closest(self, mock_sleep):
        """When multiple bars exist, should select the one closest to target."""
        from collectors.outcome.collector import _fetch_hourly_bar_close

        target = datetime(2026, 2, 20, 15, 30, 0, tzinfo=timezone.utc)
        bar_early = _mock_bar(
            close=150.00,
            timestamp=datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
        )
        bar_close = _mock_bar(
            close=151.25,
            timestamp=datetime(2026, 2, 20, 15, 0, 0, tzinfo=timezone.utc),
        )
        bar_late = _mock_bar(
            close=152.00,
            timestamp=datetime(2026, 2, 20, 16, 0, 0, tzinfo=timezone.utc),
        )
        data_client = MagicMock()
        data_client.get_stock_bars.return_value = {
            "AAPL": [bar_early, bar_close, bar_late],
        }

        result = _fetch_hourly_bar_close(data_client, "AAPL", target)

        # bar_close at 15:00 is closest to target at 15:30 (30 min away)
        assert result == 151.25

    @patch("collectors.outcome.collector.time.sleep")
    def test_api_error_returns_none(self, mock_sleep):
        """APIError during fetch should return None, not raise."""
        from alpaca.common.exceptions import APIError
        from collectors.outcome.collector import _fetch_hourly_bar_close

        target = datetime(2026, 2, 20, 15, 30, 0, tzinfo=timezone.utc)
        data_client = MagicMock()
        data_client.get_stock_bars.side_effect = APIError("rate limited")

        result = _fetch_hourly_bar_close(data_client, "AAPL", target)

        assert result is None


# ---------------------------------------------------------------------------
# Test 5: Return calculations
# ---------------------------------------------------------------------------


class TestReturnCalculations:
    def test_positive_return(self):
        """Price went up → positive raw pct change."""
        from collectors.outcome.collector import _compute_returns

        result = _compute_returns(100.0, 0.10, 105.0)

        assert abs(result["raw_pct_change"] - 5.0) < 0.001

    def test_negative_return(self):
        """Price went down → negative raw pct change."""
        from collectors.outcome.collector import _compute_returns

        result = _compute_returns(100.0, 0.10, 95.0)

        assert abs(result["raw_pct_change"] - (-5.0)) < 0.001

    def test_spread_adjusted(self):
        """Spread-adjusted return subtracts spread cost."""
        from collectors.outcome.collector import _compute_returns

        result = _compute_returns(100.0, 0.20, 105.0)

        # raw = (105 - 100) / 100 * 100 = 5.0%
        assert abs(result["raw_pct_change"] - 5.0) < 0.001
        # spread_adj = (105 - 100 - 0.20) / 100 * 100 = 4.8%
        assert abs(result["spread_adj_pct_change"] - 4.8) < 0.001

    def test_none_spread(self):
        """When spread is None, spread_adj_pct_change should be None."""
        from collectors.outcome.collector import _compute_returns

        result = _compute_returns(100.0, None, 105.0)

        assert abs(result["raw_pct_change"] - 5.0) < 0.001
        assert result["spread_adj_pct_change"] is None

    def test_zero_price_at_signal(self):
        """Zero price_at_signal should return Nones (avoid division by zero)."""
        from collectors.outcome.collector import _compute_returns

        result = _compute_returns(0, 0.10, 105.0)

        assert result["raw_pct_change"] is None
        assert result["spread_adj_pct_change"] is None

    def test_none_deferred_price(self):
        """None deferred_price should return Nones."""
        from collectors.outcome.collector import _compute_returns

        result = _compute_returns(100.0, 0.10, None)

        assert result["raw_pct_change"] is None
        assert result["spread_adj_pct_change"] is None


# ---------------------------------------------------------------------------
# Test 6: Process outcome
# ---------------------------------------------------------------------------


class TestProcessOutcome:
    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_single_window_fill(self, mock_hourly, mock_client_class, aws_env):
        """Should fill a single elapsed window and update DynamoDB."""
        mock_hourly.return_value = 151.50

        item = _put_outcome(aws_env, signal_id="sig-010")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        # 2 hours after signal — t1h elapsed, t4h not
        now = datetime(2026, 2, 20, 16, 31, 0, tzinfo=timezone.utc)
        result = collector.process_outcome(item, now)

        assert result["updated"] is True
        assert result["completed"] is False

        # Verify DynamoDB
        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-010"},
        )["Item"]
        assert stored["price_t1h"] == Decimal("151.5")
        assert stored["price_t4h"] is None  # not elapsed yet

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_multi_window_fill(self, mock_hourly, mock_client_class, aws_env):
        """Should fill multiple elapsed windows in a single pass."""
        mock_hourly.return_value = 152.00

        item = _put_outcome(aws_env, signal_id="sig-020")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        # 5 hours after signal — t1h and t4h elapsed
        now = datetime(2026, 2, 20, 19, 31, 0, tzinfo=timezone.utc)
        result = collector.process_outcome(item, now)

        assert result["updated"] is True

        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-020"},
        )["Item"]
        assert stored["price_t1h"] == Decimal("152.0")
        assert stored["price_t4h"] == Decimal("152.0")

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_daily_bar_close")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_t1d_with_pct_change(
        self, mock_hourly, mock_daily, mock_client_class, aws_env,
    ):
        """t1d fill should also compute raw and spread-adjusted pct change."""
        mock_hourly.return_value = 151.00
        mock_daily.return_value = 155.00

        item = _put_outcome(aws_env, signal_id="sig-030")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        # Many days later — t1h, t4h, t1d all elapsed
        now = datetime(2026, 2, 24, 21, 0, 0, tzinfo=timezone.utc)
        collector.process_outcome(item, now)

        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-030"},
        )["Item"]
        assert stored["price_t1d"] == Decimal("155.0")
        # raw: (155 - 150) / 150 * 100 = 3.333...
        assert abs(float(stored["raw_pct_change_t1d"]) - 3.333) < 0.01
        # spread_adj: (155 - 150 - 0.20) / 150 * 100 = 3.2
        assert abs(float(stored["spread_adj_pct_change_t1d"]) - 3.2) < 0.01

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_daily_bar_close")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_complete_transition(
        self, mock_hourly, mock_daily, mock_client_class, aws_env,
    ):
        """When all 4 windows filled, status should become COMPLETE."""
        mock_hourly.return_value = 151.00
        mock_daily.return_value = 160.00

        item = _put_outcome(aws_env, signal_id="sig-040")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        # Far future — all windows elapsed
        now = datetime(2026, 3, 15, 21, 0, 0, tzinfo=timezone.utc)
        result = collector.process_outcome(item, now)

        assert result["updated"] is True
        assert result["completed"] is True

        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-040"},
        )["Item"]
        assert stored["status"] == "COMPLETE"
        assert stored["price_t1h"] is not None
        assert stored["price_t4h"] is not None
        assert stored["price_t1d"] is not None
        assert stored["price_t5d"] is not None

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_skip_already_filled(self, mock_hourly, mock_client_class, aws_env):
        """Already-filled windows should not be refetched."""
        mock_hourly.return_value = 999.99  # would be wrong if used

        item = _put_outcome(
            aws_env, signal_id="sig-050",
            price_t1h=Decimal("151.0"),
        )

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        now = datetime(2026, 2, 20, 16, 0, 0, tzinfo=timezone.utc)
        collector.process_outcome(item, now)

        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-050"},
        )["Item"]
        # t1h should retain original value, not the mock 999.99
        assert stored["price_t1h"] == Decimal("151.0")

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_skip_when_no_bars(self, mock_hourly, mock_client_class, aws_env):
        """When fetch returns None, the field should remain NULL."""
        mock_hourly.return_value = None

        item = _put_outcome(aws_env, signal_id="sig-060")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        now = datetime(2026, 2, 20, 16, 0, 0, tzinfo=timezone.utc)
        result = collector.process_outcome(item, now)

        assert result["updated"] is False

        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-060"},
        )["Item"]
        assert stored["price_t1h"] is None


# ---------------------------------------------------------------------------
# Test 7: Update outcome fields
# ---------------------------------------------------------------------------


class TestUpdateOutcomeFields:
    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_atomic_update_preserves_fields(self, mock_client_class, aws_env):
        """Updating one field should not overwrite other existing fields."""
        _put_outcome(aws_env, signal_id="sig-070")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        collector._update_outcome_fields(
            "ENTITY#AAPL", "OUTCOME#sig-070",
            {"price_t1h": Decimal("151.50")},
        )

        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-070"},
        )["Item"]
        # Updated field
        assert stored["price_t1h"] == Decimal("151.5")
        # Untouched fields preserved
        assert stored["price_at_signal"] == Decimal("150.0")
        assert stored["signal_id"] == "sig-070"
        assert stored["status"] == "CAPTURED"

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_multi_field_update(self, mock_client_class, aws_env):
        """Multiple fields can be updated atomically."""
        _put_outcome(aws_env, signal_id="sig-071")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        collector._update_outcome_fields(
            "ENTITY#AAPL", "OUTCOME#sig-071",
            {
                "price_t1h": Decimal("151.00"),
                "price_t4h": Decimal("152.00"),
                "status": "COMPLETE",
            },
        )

        table = _outcomes_table(aws_env)
        stored = table.get_item(
            Key={"PK": "ENTITY#AAPL", "SK": "OUTCOME#sig-071"},
        )["Item"]
        assert stored["price_t1h"] == Decimal("151.0")
        assert stored["price_t4h"] == Decimal("152.0")
        assert stored["status"] == "COMPLETE"


# ---------------------------------------------------------------------------
# Test 8: Run + Lambda handler
# ---------------------------------------------------------------------------


class TestRun:
    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_daily_bar_close")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_end_to_end(
        self, mock_hourly, mock_daily, mock_client_class, aws_env,
    ):
        """Full scan → process → update flow."""
        mock_hourly.return_value = 151.00
        mock_daily.return_value = 160.00

        _put_outcome(aws_env, signal_id="sig-100")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()

        # Patch datetime.now to return far future
        with patch("collectors.outcome.collector.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(
                2026, 3, 15, 21, 0, 0, tzinfo=timezone.utc,
            )
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            stats = collector.run()

        assert stats["scanned"] == 1
        assert stats["updated"] == 1
        assert stats["completed"] == 1
        assert stats["errors"] == 0

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_empty_table(self, mock_client_class, aws_env):
        """No items to process should return zeros."""
        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        stats = collector.run()

        assert stats["scanned"] == 0
        assert stats["updated"] == 0
        assert stats["skipped"] == 0

    def test_no_alpaca_client(self, aws_env):
        """Without Alpaca keys, run should return empty stats immediately."""
        os.environ.pop("ALPACA_API_KEY", None)
        os.environ.pop("ALPACA_SECRET_KEY", None)

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()
        stats = collector.run()

        assert stats["scanned"] == 0
        assert stats["updated"] == 0

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_error_resilience(self, mock_hourly, mock_client_class, aws_env):
        """One item erroring should not prevent processing of others."""
        mock_hourly.side_effect = [RuntimeError("boom"), 151.00]

        _put_outcome(aws_env, signal_id="sig-err1", entity_id="BAD")
        _put_outcome(aws_env, signal_id="sig-err2", entity_id="GOOD")

        from collectors.outcome.collector import OutcomeCollector
        collector = OutcomeCollector()

        with patch("collectors.outcome.collector.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(
                2026, 2, 20, 16, 0, 0, tzinfo=timezone.utc,
            )
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            stats = collector.run()

        assert stats["scanned"] == 2
        assert stats["errors"] == 1
        # The other item should still be processed
        assert stats["updated"] + stats["skipped"] + stats["errors"] == 2


class TestLambdaHandler:
    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    def test_lambda_handler_returns_200(self, mock_client_class, aws_env):
        """lambda_handler should return statusCode 200 with stats in body."""
        from collectors.outcome.collector import lambda_handler

        response = lambda_handler({}, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "scanned" in body
        assert "updated" in body
        assert "completed" in body
        assert "skipped" in body
        assert "errors" in body

    @patch("collectors.outcome.collector.StockHistoricalDataClient")
    @patch("collectors.outcome.collector._fetch_daily_bar_close")
    @patch("collectors.outcome.collector._fetch_hourly_bar_close")
    def test_lambda_handler_processes_items(
        self, mock_hourly, mock_daily, mock_client_class, aws_env,
    ):
        """lambda_handler should actually process eligible outcomes."""
        mock_hourly.return_value = 151.00
        mock_daily.return_value = 160.00

        _put_outcome(aws_env, signal_id="sig-lh1")

        from collectors.outcome.collector import lambda_handler

        with patch("collectors.outcome.collector.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(
                2026, 3, 15, 21, 0, 0, tzinfo=timezone.utc,
            )
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            response = lambda_handler({}, None)

        body = json.loads(response["body"])
        assert body["scanned"] == 1
        assert body["updated"] == 1
